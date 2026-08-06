"""Регрессия: вложенные dict/list в столбцах (напр. из ndjson) при прогоне через SQL-движки
(duckdb_im / sqlite3_im) должны выводиться ВАЛИДНЫМ JSON, а не Python-repr (str({...}) с апострофами).

Уровни: ячейка (`sql_object_cell`) — офлайн, без зависимостей; DataFrame (`normalize_object_columns`)
и sqlite3_im — требуют pandas; duckdb_im — ещё и duckdb (в облегчённом окружении пропускается)."""
import datetime
import decimal
import json
import unittest
import warnings

from app.sources.additional.sql_cells import sql_object_cell

try:
    import pandas  # noqa: F401
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import pytz  # noqa: F401   (движки sqlite3_im/duckdb_im импортируют его для UDF времени)
    HAS_SQL_DEPS = HAS_PANDAS
except ImportError:
    HAS_SQL_DEPS = False

try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = HAS_SQL_DEPS
except ImportError:
    HAS_DUCKDB = False

CS = {"app_name": "t", "app_version": "0", "username": "u"}


class TestSqlObjectCell(unittest.TestCase):
    """Уровень ячейки — работает без pandas/duckdb, поэтому ловит регрессии и в CI, и локально."""

    def test_nested_structures_are_valid_json(self):
        self.assertEqual(sql_object_cell({"k": "v"}), '{"k": "v"}')
        self.assertEqual(sql_object_cell(["a", "b"]), '["a", "b"]')
        self.assertEqual(json.loads(sql_object_cell({"x": {"y": [1, 2]}})), {"x": {"y": [1, 2]}})

    def test_cyrillic_stays_readable(self):
        self.assertEqual(sql_object_cell({"имя": "значение"}), '{"имя": "значение"}')

    def test_plain_values_are_not_quoted(self):
        self.assertEqual(sql_object_cell("hello world"), "hello world")
        self.assertEqual(sql_object_cell(42), "42")
        self.assertEqual(sql_object_cell(True), "True")
        self.assertEqual(sql_object_cell(None), "")

    def test_non_json_types_inside_structure_do_not_raise(self):
        # datetime/Decimal/bytes/set внутри вложенной структуры: json.dumps(default=str) вместо падения
        # шага (раньше такие таблицы просто не загружались бы в SQL-движок)
        value = {"d": datetime.datetime(2026, 8, 5, 12, 0), "p": decimal.Decimal("1.5"),
                 "b": b"\x00", "s": {1}}
        text = sql_object_cell(value)
        self.assertEqual(json.loads(text)["p"], "1.5")
        self.assertIn("2026-08-05", text)


@unittest.skipUnless(HAS_PANDAS, "pandas required")
class TestNormalizeObjectColumns(unittest.TestCase):
    def test_missing_values_become_empty_string(self):
        # None/NaN/NaT/pd.NA -> '' (как прежний fillna('')), а не 'NaT'/'<NA>'
        frame = pandas.DataFrame([{"a": None, "b": float("nan"), "c": pandas.NaT, "d": pandas.NA}]).astype(object)
        from app.sources.additional.sql_cells import normalize_object_columns
        normalize_object_columns(frame)
        self.assertEqual(frame.iloc[0].to_dict(), {"a": "", "b": "", "c": "", "d": ""})

    def test_nested_and_plain_in_one_column(self):
        from app.sources.additional.sql_cells import normalize_object_columns
        frame = pandas.DataFrame([{"v": {"k": 1}}, {"v": "plain"}, {"v": None}])
        normalize_object_columns(frame)
        self.assertEqual(list(frame["v"]), ['{"k": 1}', "plain", ""])

    def test_selects_object_and_string_columns_without_deprecation_warning(self):
        # pandas 3: строки — отдельный dtype 'str', select_dtypes('object') захватывает их только
        # ради обратной совместимости (Pandas4Warning). Отбор по dtype.kind работает в обеих ветках.
        from app.sources.additional.sql_cells import object_like_columns
        frame = pandas.DataFrame([{"s": "text", "n": 1, "f": 1.5, "b": True,
                                   "d": pandas.Timestamp("2026-01-01"), "obj": {"k": 1}}])
        with warnings.catch_warnings():
            warnings.simplefilter("error")             # любое предупреждение -> падение теста
            columns = object_like_columns(frame)
        self.assertEqual(sorted(columns), ["obj", "s"])

    def test_categorical_columns_are_left_alone(self):
        from app.sources.additional.sql_cells import object_like_columns
        frame = pandas.DataFrame({"c": pandas.Series(["a", "b"], dtype="category"),
                                  "v": [{"k": 1}, None]})
        self.assertEqual(object_like_columns(frame), ["v"])

    def test_string_dtype_column_is_normalized(self):
        from app.sources.additional.sql_cells import normalize_object_columns
        frame = pandas.DataFrame({"s": pandas.Series(["a", None], dtype="string")})
        normalize_object_columns(frame)
        self.assertEqual(list(frame["s"]), ["a", ""])


@unittest.skipUnless(HAS_SQL_DEPS, "pandas + pytz required")
class TestSqliteNestedJsonCells(unittest.TestCase):
    def _run(self, data_map, queries):
        from app.sources.sqlite3 import execute_sqlite3
        return execute_sqlite3({"queries": queries}, {"json": {}}, data_map, CS)

    def test_nested_dict_list_are_valid_json(self):
        data_map = {"data": [
            {"id": 1, "tags": ["a", "b"], "meta": {"k": "v", "n": [1, 2]}, "name": "srv1"},
            {"id": 2, "tags": [], "meta": {"x": {"y": 1}}, "name": "srv2"},
        ]}
        ok, msg, _fn, recs = self._run(data_map, ["SELECT * FROM data"])
        self.assertTrue(ok, msg)
        self.assertEqual(json.loads(recs[0]["tags"]), ["a", "b"])
        self.assertEqual(json.loads(recs[0]["meta"]), {"k": "v", "n": [1, 2]})
        self.assertEqual(json.loads(recs[1]["meta"]), {"x": {"y": 1}})
        self.assertNotIn("'", recs[0]["meta"])

    def test_json_functions_work_on_such_cells(self):
        # практический смысл правки: по колонке можно работать JSON1-функциями
        data_map = {"data": [{"id": 1, "meta": {"owner": "ivanov"}}]}
        ok, msg, _fn, recs = self._run(
            data_map, ["SELECT json_valid(meta) AS valid, json_extract(meta, '$.owner') AS owner FROM data"])
        self.assertTrue(ok, msg)
        self.assertEqual((recs[0]["valid"], recs[0]["owner"]), (1, "ivanov"))

    def test_plain_strings_not_extra_quoted(self):
        ok, msg, _fn, recs = self._run({"data": [{"name": "srv1", "note": "hello world"}]},
                                       ["SELECT * FROM data"])
        self.assertTrue(ok, msg)
        self.assertEqual(recs[0]["name"], "srv1")
        self.assertEqual(recs[0]["note"], "hello world")

    def test_non_json_types_inside_structure_do_not_fail_the_step(self):
        data_map = {"data": [{"id": 1, "meta": {"when": datetime.datetime(2026, 8, 5, 12, 0)}}]}
        ok, msg, _fn, recs = self._run(data_map, ["SELECT * FROM data"])
        self.assertTrue(ok, msg)
        self.assertIn("2026-08-05", recs[0]["meta"])


@unittest.skipUnless(HAS_DUCKDB, "duckdb + pandas required")
class TestDuckdbNestedJsonCells(unittest.TestCase):
    def _run_duckdb(self, data_map):
        from app.sources.duckdb import execute_duckdb
        params = {"type": "table", "queries": ["SELECT * FROM data"]}
        return execute_duckdb(params, {"json": {}}, data_map, CS)

    def test_nested_dict_list_are_valid_json(self):
        data_map = {"data": [
            {"id": 1, "tags": ["a", "b"], "meta": {"k": "v", "n": [1, 2]}, "name": "srv1"},
            {"id": 2, "tags": [], "meta": {"x": {"y": 1}}, "name": "srv2"},
        ]}
        ok, msg, _fn, recs = self._run_duckdb(data_map)
        self.assertTrue(ok, msg)
        # вложенные значения — валидный JSON (double quotes), парсятся и совпадают по смыслу
        self.assertEqual(json.loads(recs[0]["tags"]), ["a", "b"])
        self.assertEqual(json.loads(recs[0]["meta"]), {"k": "v", "n": [1, 2]})
        self.assertEqual(json.loads(recs[1]["meta"]), {"x": {"y": 1}})
        # апострофов Python-repr быть не должно
        self.assertNotIn("'", recs[0]["meta"])

    def test_plain_strings_not_extra_quoted(self):
        # простые строки не должны получать лишний JSON-квотинг
        data_map = {"data": [{"name": "srv1", "note": "hello world"}]}
        ok, msg, _fn, recs = self._run_duckdb(data_map)
        self.assertTrue(ok, msg)
        self.assertEqual(recs[0]["name"], "srv1")
        self.assertEqual(recs[0]["note"], "hello world")

    def test_missing_values_become_empty_string(self):
        data_map = {"data": [{"a": "x", "b": None}, {"a": None, "b": "y"}]}
        ok, msg, _fn, recs = self._run_duckdb(data_map)
        self.assertTrue(ok, msg)
        self.assertEqual([r["a"] for r in recs], ["x", ""])


if __name__ == "__main__":
    unittest.main()
