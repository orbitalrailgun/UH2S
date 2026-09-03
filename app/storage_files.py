"""Файловое хранение больших таблиц: файл кладётся на диск, в БД — только метаданные.

Зачем: обычная запись хранилища держит таблицу одним JSON-блобом в ячейке БД. Для крупных выгрузок это
не работает — 6.3 млн строк (CSV 1.8 ГБ) дают ~3.1 ГБ JSON против предела 1 ГБ на ячейку SQLite и на
поле PostgreSQL, а материализация в list-of-dict — порядка 16 ГБ RAM. Поэтому большой файл сохраняется
потоком на диск (память O(1)), а читает его `duckdb_im` напрямую (`read_csv_auto`/`read_json_auto`/
`read_parquet`), не поднимая строки в Python.

Безопасность: имя файла на диске генерирует сервер (`secrets.token_urlsafe`), пользовательский ввод в
путь не попадает — path traversal невозможен; исходное имя живёт только в метаданных. Формат — по
allowlist расширений, для parquet дополнительно проверяется магия `PAR1`. Каталог хранения вне
web root, наружу файл отдаётся только через одноразовый capability-token роут (`app/downloads.py`).

Модуль без nicegui/pandas/duckdb в зависимостях: `stage_upload` работает с любым объектом, у которого
есть `save(path)`/`size()` (в приложении это `FileUpload` из NiceGUI), а `describe_file` использует
duckdb только если он установлен. Поэтому модуль тестируется офлайн.
"""
import os
import re
import secrets

# каталог для файлов хранилища: UH2S_STORAGE_DIR или <корень репозитория>/storage_files.
# В отличие от экспортов (app/downloads.py) каталог ПЕРСИСТЕНТНЫЙ — не системный tempdir.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STORAGE_DIR = os.path.join(_REPO_ROOT, "storage_files")

# предел размера загружаемого файла (клиентская проверка Quasar + серверная страховка)
DEFAULT_UPLOAD_MAX_BYTES = 16 * 1024 * 1024 * 1024      # 16 ГиБ
# от какого размера файл хранится файлом, а не строками в БД
DEFAULT_FILE_THRESHOLD_BYTES = 64 * 1024 * 1024         # 64 МиБ

# allowlist форматов: расширение -> (формат, табличная функция duckdb).
# .gz duckdb распаковывает прозрачно, поэтому отдельной обработки не нужно.
FORMAT_READERS = {
    "csv": ("csv", "read_csv_auto"),
    "csv.gz": ("csv", "read_csv_auto"),
    "tsv": ("tsv", "read_csv_auto"),
    "tsv.gz": ("tsv", "read_csv_auto"),
    "ndjson": ("ndjson", "read_json_auto"),
    "ndjson.gz": ("ndjson", "read_json_auto"),
    "jsonl": ("ndjson", "read_json_auto"),
    "jsonl.gz": ("ndjson", "read_json_auto"),
    "parquet": ("parquet", "read_parquet"),
}


def storage_dir():
    """Каталог файлов хранилища (создаётся при первом обращении)."""
    path = os.environ.get("UH2S_STORAGE_DIR") or DEFAULT_STORAGE_DIR
    os.makedirs(path, exist_ok=True)
    return path


def _env_bytes(name, default):
    try:
        value = int(str(os.environ.get(name) or "").strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def upload_max_bytes():
    """Предел размера файла при загрузке (UH2S_UPLOAD_MAX_BYTES)."""
    return _env_bytes("UH2S_UPLOAD_MAX_BYTES", DEFAULT_UPLOAD_MAX_BYTES)


def file_threshold_bytes():
    """Порог, с которого таблица хранится файлом (UH2S_STORAGE_FILE_THRESHOLD_BYTES)."""
    return _env_bytes("UH2S_STORAGE_FILE_THRESHOLD_BYTES", DEFAULT_FILE_THRESHOLD_BYTES)


def detect_format(filename):
    """Имя файла -> (ok, формат|сообщение об ошибке, расширение).

    Формат определяется по расширению из allowlist (двойное расширение .csv.gz учитывается)."""
    name = (filename or "").strip().lower()
    for extension in sorted(FORMAT_READERS, key=len, reverse=True):
        if name.endswith("." + extension):
            return True, FORMAT_READERS[extension][0], extension
    supported = ", ".join(sorted(FORMAT_READERS))
    return False, f"неподдерживаемый формат файла (ожидается один из: {supported})", ""


def should_store_as_file(size_bytes, explicit=None, threshold=None):
    """Хранить файлом? explicit=True/False — явный выбор пользователя; None — по порогу размера."""
    if explicit is not None:
        return bool(explicit)
    try:
        size = int(size_bytes)
    except (TypeError, ValueError):
        return False
    return size >= (threshold if threshold is not None else file_threshold_bytes())


def storage_mode_for_upload(filename, size_bytes, explicit=None, threshold=None):
    """Режим хранения загружаемого файла: "file" (файлом на диске) или "rows" (строками в БД).

    Файлом хранятся только форматы, которые умеет читать duckdb (см. FORMAT_READERS): xlsx/xls
    разбираются в строки как раньше, даже если файл крупный (иначе большой xlsx получал бы
    «неподдерживаемый формат» вместо загрузки)."""
    if not detect_format(filename)[0]:
        return "rows"
    return "file" if should_store_as_file(size_bytes, explicit, threshold) else "rows"


def staged_path(extension):
    """Путь для нового файла: серверное случайное имя в каталоге хранилища (ввод пользователя не участвует)."""
    safe_extension = re.sub(r"[^a-z0-9.]", "", (extension or "bin").lower()) or "bin"
    return os.path.join(storage_dir(), f"{secrets.token_urlsafe(24)}.{safe_extension}")


def _parquet_magic_ok(path):
    """У parquet первые 4 байта — PAR1; для csv/ndjson содержимое не сниффится (нечего проверять)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"PAR1"
    except OSError:
        return False


async def stage_upload(file_upload, original_name=None, max_bytes=None):
    """Положить загруженный файл на диск потоком. Возврат (ok, error|None, meta).

    file_upload — объект NiceGUI FileUpload (или совместимый: `size()` и async `save(path)`); NiceGUI
    сама пишет чанками по 1 МБ, поэтому файл в память не поднимается. meta:
    {path, name, format, extension, size_bytes}."""
    name = original_name or getattr(file_upload, "name", "") or ""
    ok, fmt_or_error, extension = detect_format(name)
    if not ok:
        return False, fmt_or_error, {}

    limit = max_bytes if max_bytes is not None else upload_max_bytes()
    try:
        size_bytes = int(file_upload.size())
    except BaseException:
        size_bytes = None
    if size_bytes is not None and size_bytes > limit:
        return False, f"файл {size_bytes} байт больше предела {limit} байт (UH2S_UPLOAD_MAX_BYTES)", {}

    path = staged_path(extension)
    try:
        await file_upload.save(path)
    except BaseException as save_error:
        remove_file(path)
        return False, f"не удалось сохранить файл: {save_error}", {}

    try:
        real_size = os.path.getsize(path)
    except OSError as size_error:
        remove_file(path)
        return False, f"файл не найден после сохранения: {size_error}", {}
    if real_size > limit:
        remove_file(path)
        return False, f"файл {real_size} байт больше предела {limit} байт (UH2S_UPLOAD_MAX_BYTES)", {}
    if fmt_or_error == "parquet" and not _parquet_magic_ok(path):
        remove_file(path)
        return False, "файл не похож на parquet (нет сигнатуры PAR1)", {}

    return True, None, {"path": path, "name": name, "format": fmt_or_error,
                        "extension": extension, "size_bytes": real_size}


def _fieldnames(records):
    """Объединение колонок всех строк с сохранением порядка первого появления."""
    names, seen = [], set()
    for row in records:
        if isinstance(row, dict):
            for key in row:
                if key not in seen:
                    seen.add(key)
                    names.append(str(key))
    return names or ["value"]


def _write_records_csv(records, path):
    """Записать строки в CSV потоком (stdlib): память не зависит от числа строк."""
    import csv
    fieldnames = _fieldnames(records)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow(row if isinstance(row, dict) else {"value": row})
    return fieldnames


def write_records_file(records, prefer_parquet=True):
    """Таблицу (list-of-dict) -> файл в каталоге хранилища. Возврат (ok, error|None, meta).

    Пишем сначала CSV потоком (память O(1) от числа строк), затем, если доступен duckdb, конвертируем
    в parquet через `COPY (SELECT * FROM read_csv_auto(...)) TO ... (FORMAT PARQUET)` — duckdb делает это
    сам, тоже не поднимая таблицу в память. Parquet типизирован и в разы компактнее, а duckdb_im читает
    его быстрее всего; без duckdb остаётся CSV. Фактический формат — в meta['format'] и в колонке
    «Формат» раздела «Хранилище»."""
    if not isinstance(records, list) or not records:
        return False, "нет строк для сохранения", {}
    csv_path = staged_path("csv")
    try:
        columns = _write_records_csv(records, csv_path)
    except BaseException as write_error:
        remove_file(csv_path)
        return False, f"не удалось записать csv: {write_error}", {}

    if not prefer_parquet:
        return True, None, {"path": csv_path, "format": "csv", "extension": "csv",
                            "size_bytes": os.path.getsize(csv_path), "rows": len(records),
                            "columns": columns}
    try:
        import duckdb
    except ImportError:
        return True, None, {"path": csv_path, "format": "csv", "extension": "csv",
                            "size_bytes": os.path.getsize(csv_path), "rows": len(records),
                            "columns": columns}

    parquet_path = staged_path("parquet")
    try:
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(f"COPY (SELECT * FROM read_csv_auto({quote_literal(csv_path)})) "
                               f"TO {quote_literal(parquet_path)} (FORMAT PARQUET)")
        finally:
            connection.close()
    except BaseException as convert_error:
        # не смогли в parquet (напр. duckdb не разобрал csv) — оставляем csv, это рабочий формат
        remove_file(parquet_path)
        return True, None, {"path": csv_path, "format": "csv", "extension": "csv",
                            "size_bytes": os.path.getsize(csv_path), "rows": len(records),
                            "columns": columns, "note": f"parquet failed: {convert_error}"}
    remove_file(csv_path)
    return True, None, {"path": parquet_path, "format": "parquet", "extension": "parquet",
                        "size_bytes": os.path.getsize(parquet_path), "rows": len(records),
                        "columns": columns}


def remove_file(path):
    """Идемпотентно удалить файл хранилища (нет файла — не ошибка)."""
    if not path:
        return False
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def sweep_orphans(known_paths):
    """Удалить из каталога файлы, на которые нет записи в БД. Возврат числа удалённых.

    Сироты появляются, если запись удалили в обход UI или контейнер пересоздали с потерей тома."""
    known = {os.path.abspath(p) for p in (known_paths or []) if p}
    removed = 0
    directory = storage_dir()
    try:
        entries = os.listdir(directory)
    except OSError:
        return 0
    for entry in entries:
        candidate = os.path.abspath(os.path.join(directory, entry))
        if not os.path.isfile(candidate) or candidate in known:
            continue
        if remove_file(candidate):
            removed += 1
    return removed


def quote_identifier(name):
    """Имя таблицы/VIEW для SQL в двойных кавычках (внутренние кавычки удваиваются).

    Ключи хранилища — произвольные строки (пробелы, точки), поэтому идентификатор всегда quoted."""
    return '"' + str(name).replace('"', '""') + '"'


def quote_literal(text):
    """Строковый литерал SQL в одинарных кавычках (внутренние кавычки удваиваются)."""
    return "'" + str(text).replace("'", "''") + "'"


def reader_for_format(fmt):
    """Табличная функция duckdb для формата (по умолчанию — read_csv_auto)."""
    for format_name, reader in FORMAT_READERS.values():
        if format_name == fmt:
            return reader
    return "read_csv_auto"


def file_view_sql(key, path, fmt):
    """SQL создания VIEW поверх файла: имя — ключ хранилища, источник — табличная функция duckdb.

    И имя, и путь квотируются; путь всегда серверного происхождения (см. `staged_path`)."""
    return (f"CREATE OR REPLACE VIEW {quote_identifier(key)} AS "
            f"SELECT * FROM {reader_for_format(fmt)}({quote_literal(path)})")


def preview_file(path, fmt, limit=200):
    """Первые `limit` строк файла как list-of-dict. Возврат (ok, error|None, rows).

    Читает через duckdb с LIMIT — на многогигабайтном файле в память попадает только эта выборка."""
    try:
        import duckdb
    except ImportError:
        return False, "для предпросмотра файловой записи нужен duckdb", []
    source = f"{reader_for_format(fmt)}({quote_literal(path)})"
    try:
        connection = duckdb.connect(":memory:")
        try:
            rows = connection.execute(f"SELECT * FROM {source} LIMIT {int(limit)}").fetchall()
            columns = [description[0] for description in connection.description]
            return True, None, [dict(zip(columns, row)) for row in rows]
        finally:
            connection.close()
    except BaseException as preview_error:
        return False, f"не удалось прочитать файл: {preview_error}", []


def describe_file(path, fmt):
    """Колонки и число строк файла через duckdb. Возврат (columns:list, rows:int|None).

    duckdb опционален (ядро ставится без коннекторов): без него — ([], None), в UI это «—»."""
    try:
        import duckdb
    except ImportError:
        return [], None
    source = f"{reader_for_format(fmt)}({quote_literal(path)})"
    try:
        connection = duckdb.connect(":memory:")
        try:
            columns = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
            rows = connection.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
            return columns, int(rows)
        finally:
            connection.close()
    except BaseException:
        return [], None
