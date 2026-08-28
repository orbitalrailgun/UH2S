"""Нормализация значений перед загрузкой собранных данных в SQL-движки (`sqlite3_im`/`duckdb_im`).

SQL-движки принимают из pandas плоские значения, поэтому object-колонки приводятся к строкам.
Вложенные `dict`/`list` (обычное дело для ndjson, CMDB, API-ответов) при `.astype(str)` превращались
в Python-repr с апострофами (`{'k': 'v'}`) — такой текст не проходит `json_valid()` и не читается
`json_extract`/JSON1. Здесь они сериализуются валидным JSON, а пропуски (`None`/`NaN`/`NaT`/`pd.NA`)
дают пустую строку, как прежний `fillna('')`.

Модуль без pandas на уровне ячейки (`sql_object_cell` тестируется офлайн); работа с DataFrame —
в `normalize_object_columns`.
"""
import datetime
import decimal
import json
import uuid

# что json/orjson кодируют сами; всё остальное на выходе из движка надо приводить
_JSON_NATIVE_TYPES = (str, bool, int, float, type(None))


def _is_missing(value):
    """Пропуск pandas (`NaN`/`NaT`/`pd.NA`)? Скалярная проверка, массивы игнорируются."""
    try:
        import pandas
    except ImportError:
        return False
    try:
        result = pandas.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except BaseException:
        return False


def json_safe_value(value):
    """Значение из SQL-движка/pandas -> JSON-сериализуемое. Никогда не бросает исключение.

    Зачем: записи шага уходят и в UI (websocket, orjson), и в storage/SAVE (`json.dumps`). duckdb по
    CSV/parquet выводит типы, поэтому в строках оказываются `pandas.Timestamp`, `Decimal`, numpy-скаляры
    и BLOB — orjson на них падает, emit не уходит, и шаг выглядит «вечно выполняющимся».

    Даты -> ISO-строка (как `to_json(date_format="iso")` в pandas_im), пропуски -> None,
    интервалы -> секунды, BLOB -> hex, UUID/прочее незнакомое -> str."""
    if isinstance(value, _JSON_NATIVE_TYPES):
        # NaN/inf стандартный json пишет как NaN/Infinity — это не валидный JSON
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return None
        return value
    if _is_missing(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe_value(item) for item in value]
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()      # pandas.Timestamp — подкласс datetime
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    item_method = getattr(value, "item", None)   # numpy/pandas-скаляры: .item() -> python-тип
    if callable(item_method):
        try:
            return json_safe_value(item_method())
        except BaseException:
            pass
    return str(value)


def json_safe_records(records):
    """list-of-dict -> тот же список с JSON-сериализуемыми значениями (по ячейке, без исключений)."""
    safe = []
    for row in records:
        if isinstance(row, dict):
            safe.append({str(key): json_safe_value(value) for key, value in row.items()})
        else:
            safe.append({"value": json_safe_value(row)})
    return safe


def dataframe_to_records(output_df):
    """DataFrame -> list-of-dict, гарантированно JSON-сериализуемый.

    Поячеечно через `json_safe_value`, а не через `to_json(date_format="iso")`: последний быстрее
    (0.12 s против 0.44 s на 1 млн ячеек), но падает на не-UTF8 BLOB и объектах UUID и типизирует
    иначе (`Decimal` -> строка `"1.5"` вместо числа) — то есть один и тот же запрос давал бы разные
    типы в зависимости от данных. Предсказуемость важнее: результаты шагов DSL — обычно выборки и
    агрегаты, а 0.44 s на 1 млн ячеек несопоставимы со временем самого запроса и отрисовки."""
    return json_safe_records(output_df.to_dict("records"))


def sql_object_cell(value):
    """Значение ячейки object-колонки -> строка для SQL-движка.

    `dict`/`list` -> валидный JSON (`ensure_ascii=False` — кириллица остаётся читаемой,
    `default=str` — datetime/Decimal/bytes внутри структуры не роняют шаг); `None` -> ''; остальное
    -> `str()`. Пропуски pandas (`NaN`/`NaT`/`pd.NA`) снимаются до вызова — см. `normalize_object_columns`."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return str(value)


def object_like_columns(input_df):
    """Колонки, способные держать python-объекты и строки.

    `select_dtypes(include='object')` для этого не годится: в pandas 3 строковые колонки уехали в
    отдельный dtype `str`, и такой отбор захватывает их лишь ради обратной совместимости
    (`Pandas4Warning`), а в будущей версии перестанет. Отбираем по `dtype.kind == "O"` — это и
    `object`, и `str`/`string`, и в pandas 2, и в pandas 3, без предупреждений. Категориальные
    колонки (у них тот же kind) не трогаем — как и прежний отбор по `object`."""
    import pandas
    return [column for column in input_df.columns
            if input_df[column].dtype.kind == "O"
            and not isinstance(input_df[column].dtype, pandas.CategoricalDtype)]


def normalize_object_columns(input_df):
    """object/строковые колонки DataFrame -> строки (вложенные структуры — валидным JSON, пропуски — '').

    Пропуски снимаются векторно (`notna()` корректно работает на object-колонке со списками),
    затем ячейки проходят через `sql_object_cell`. Изменяет DataFrame на месте и возвращает его."""
    for column in object_like_columns(input_df):
        input_df[column] = input_df[column].where(input_df[column].notna(), '').map(sql_object_cell)
    return input_df
