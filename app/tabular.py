"""Разбор загруженных табличных файлов (CSV/XLSX) в list-of-dict.
Без nicegui/БД — чтобы CSV-путь тестировался офлайн. XLSX требует pandas (ленивый импорт)."""
import io
import csv
import json


def _decode_text(content):
    """Декодировать байты текста, толерантно к кодировке (utf-8 с BOM, затем cp1251, затем replace)."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_table_file(content, filename):
    """bytes + имя файла -> (ok, error_or_none, records). Поддержка .csv (stdlib) и .xlsx/.xls (pandas).
    CSV: значения — строки (инвентаризационные данные это устраивает). XLSX: типы через JSON-native
    (NaN -> null, даты -> ISO). Возвращаемые записи JSON-сериализуемы (пригодны для storage)."""
    name = (filename or "").strip().lower()
    if not content:
        return False, "пустой файл", []

    if name.endswith(".csv"):
        try:
            reader = csv.DictReader(io.StringIO(_decode_text(content)))
            records = [dict(row) for row in reader]
            if not reader.fieldnames:
                return False, "в CSV не найдены заголовки колонок", []
            return True, None, records
        except BaseException as e:
            return False, f"ошибка разбора CSV: {e}", []

    if name.endswith(".xlsx") or name.endswith(".xls"):
        try:
            import pandas
            data_frame = pandas.read_excel(io.BytesIO(content), sheet_name=0)
            # to_json -> loads даёт JSON-native типы (без numpy int64/Timestamp, несериализуемых в storage)
            records = json.loads(data_frame.to_json(orient="records", date_format="iso"))
            return True, None, records
        except BaseException as e:
            return False, f"ошибка разбора XLSX: {e}", []

    return False, "поддерживаются только .csv и .xlsx", []
