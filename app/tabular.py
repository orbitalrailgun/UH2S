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
    """bytes + имя файла -> (ok, error_or_none, records). Поддержка .csv (stdlib), .xlsx/.xls (pandas)
    и .ndjson/.jsonl (newline-delimited JSON, stdlib). CSV: значения — строки. XLSX: типы через
    JSON-native (NaN -> null, даты -> ISO). NDJSON: по одному JSON-объекту на строку, пустые строки
    пропускаются. Возвращаемые записи JSON-сериализуемы (пригодны для storage)."""
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

    if name.endswith(".ndjson") or name.endswith(".jsonl"):
        # newline-delimited JSON: по одному JSON-объекту на строку. Пустые строки пропускаем.
        # Держим результат в RAM (для больших файлов вызов обёрнут в run.io_bound на стороне UI,
        # чтобы не блокировать event loop). Ошибку сопровождаем номером строки для диагностики.
        try:
            records = []
            for line_number, raw_line in enumerate(_decode_text(content).splitlines(), 1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as parse_error:
                    return False, f"ошибка ndjson в строке {line_number}: {parse_error}", []
                if not isinstance(obj, dict):
                    return False, f"строка {line_number} ndjson не является объектом JSON", []
                records.append(obj)
            if not records:
                return False, "в ndjson нет записей", []
            return True, None, records
        except BaseException as e:
            return False, f"ошибка разбора ndjson: {e}", []

    if name.endswith(".xlsx") or name.endswith(".xls"):
        try:
            import pandas
            data_frame = pandas.read_excel(io.BytesIO(content), sheet_name=0)
            # to_json -> loads даёт JSON-native типы (без numpy int64/Timestamp, несериализуемых в storage)
            records = json.loads(data_frame.to_json(orient="records", date_format="iso"))
            return True, None, records
        except BaseException as e:
            return False, f"ошибка разбора XLSX: {e}", []

    return False, "поддерживаются только .csv, .xlsx, .ndjson", []
