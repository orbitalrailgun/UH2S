"""Нормализация значений перед загрузкой собранных данных в SQL-движки (`sqlite3_im`/`duckdb_im`).

SQL-движки принимают из pandas плоские значения, поэтому object-колонки приводятся к строкам.
Вложенные `dict`/`list` (обычное дело для ndjson, CMDB, API-ответов) при `.astype(str)` превращались
в Python-repr с апострофами (`{'k': 'v'}`) — такой текст не проходит `json_valid()` и не читается
`json_extract`/JSON1. Здесь они сериализуются валидным JSON, а пропуски (`None`/`NaN`/`NaT`/`pd.NA`)
дают пустую строку, как прежний `fillna('')`.

Модуль без pandas на уровне ячейки (`sql_object_cell` тестируется офлайн); работа с DataFrame —
в `normalize_object_columns`.
"""
import json


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
