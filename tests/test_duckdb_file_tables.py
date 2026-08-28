"""Тесты авторегистрации файловых таблиц хранилища в duckdb_im.

БД не нужна: резолвер `list_storage_file_tables` подменяется (как `analyzer.get_actual_object_by_name`
в tests/test_analyzer.py). Требуют duckdb + pandas — в облегчённом окружении пропускаются."""
import csv
import json
import os
import shutil
import tempfile
import unittest

try:
    import duckdb  # noqa: F401
    import pandas  # noqa: F401
    import pytz  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

CS = {"app_name": "t", "app_version": "0", "username": "u"}


@unittest.skipUnless(HAS_DUCKDB, "duckdb + pandas + pytz required")
class TestDuckdbFileTables(unittest.TestCase):
    def setUp(self):
        import app.sources.duckdb as duckdb_source
        self.module = duckdb_source
        self._saved_resolver = duckdb_source.list_storage_file_tables
        self.dir = tempfile.mkdtemp(prefix="uh2s_test_files_")
        self.csv_path = os.path.join(self.dir, "events.csv")
        with open(self.csv_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["host", "level"])
            for index in range(300):
                writer.writerow([f"srv-{index % 3}", "ERROR" if index % 2 else "INFO"])

    def tearDown(self):
        self.module.list_storage_file_tables = self._saved_resolver
        shutil.rmtree(self.dir, ignore_errors=True)

    def _files(self, *entries):
        self.module.list_storage_file_tables = lambda current_state: list(entries)

    def _query(self, sql, data_map=None):
        return self.module.execute_duckdb({"type": "table", "queries": [sql]}, {"json": {}},
                                          data_map or {}, CS)

    def _entry(self, key, path=None, fmt="csv", expired=False):
        return {"id": key, "path": path or self.csv_path, "format": fmt, "expired": expired}

    def test_file_table_is_queryable_without_declaration(self):
        self._files(self._entry("events"))
        ok, info, _func, rows = self._query("SELECT COUNT(*) AS c FROM events")
        self.assertTrue(ok, info)
        self.assertEqual(rows, [{"c": 300}])

    def test_group_by_over_file_table(self):
        self._files(self._entry("events"))
        ok, info, _func, rows = self._query(
            "SELECT host, COUNT(*) AS c FROM events GROUP BY 1 ORDER BY 1")
        self.assertTrue(ok, info)
        self.assertEqual([row["host"] for row in rows], ["srv-0", "srv-1", "srv-2"])

    def test_key_with_space_and_dot_needs_quoting_and_works(self):
        self._files(self._entry("claude events.2026"))
        ok, info, _func, rows = self._query('SELECT COUNT(*) AS c FROM "claude events.2026"')
        self.assertTrue(ok, info)
        self.assertEqual(rows, [{"c": 300}])

    def test_collected_data_shadows_file_table_with_same_name(self):
        self._files(self._entry("events"))
        ok, info, _func, rows = self._query("SELECT * FROM events",
                                            {"events": [{"host": "from-data-map"}]})
        self.assertTrue(ok, info)
        self.assertEqual(rows, [{"host": "from-data-map"}])

    def test_expired_entry_is_not_registered(self):
        self._files(self._entry("stale", expired=True))
        ok, info, _func, _rows = self._query("SELECT * FROM stale")
        self.assertFalse(ok)
        self.assertIn("stale", info)

    def test_missing_file_does_not_break_other_tables(self):
        self._files(self._entry("gone", path=os.path.join(self.dir, "no_such.csv")),
                    self._entry("events"))
        ok, info, _func, rows = self._query("SELECT COUNT(*) AS c FROM events")
        self.assertTrue(ok, info)      # битая запись только логируется
        self.assertEqual(rows, [{"c": 300}])

    def test_ndjson_file_table(self):
        path = os.path.join(self.dir, "events.jsonl")
        with open(path, "w") as handle:
            for index in range(10):
                handle.write(json.dumps({"host": f"srv-{index}", "n": index}) + "\n")
        self._files(self._entry("events_json", path=path, fmt="ndjson"))
        ok, info, _func, rows = self._query("SELECT SUM(n) AS total FROM events_json")
        self.assertTrue(ok, info)
        self.assertEqual(rows[0]["total"], 45)

    def test_parquet_file_table(self):
        path = os.path.join(self.dir, "events.parquet")
        duckdb.connect(":memory:").execute(
            f"COPY (SELECT * FROM read_csv_auto('{self.csv_path}')) TO '{path}' (FORMAT PARQUET)")
        self._files(self._entry("events_parquet", path=path, fmt="parquet"))
        ok, info, _func, rows = self._query("SELECT COUNT(*) AS c FROM events_parquet")
        self.assertTrue(ok, info)
        self.assertEqual(rows, [{"c": 300}])

    def test_join_of_file_table_and_collected_data(self):
        self._files(self._entry("events"))
        data_map = {"hosts": [{"host": "srv-0", "owner": "ivanov"}]}
        ok, info, _func, rows = self._query(
            "SELECT h.owner, COUNT(*) AS c FROM events e JOIN hosts h ON h.host = e.host "
            "GROUP BY 1", data_map)
        self.assertTrue(ok, info)
        self.assertEqual(rows, [{"owner": "ivanov", "c": 100}])

    def test_describe_and_preview_of_staged_file(self):
        from app.storage_files import describe_file, preview_file
        columns, row_count = describe_file(self.csv_path, "csv")
        self.assertEqual(columns, ["host", "level"])
        self.assertEqual(row_count, 300)
        ok, error, rows = preview_file(self.csv_path, "csv", limit=5)
        self.assertTrue(ok, error)
        self.assertEqual(len(rows), 5)
        self.assertEqual(set(rows[0]), {"host", "level"})

    def test_preview_reports_error_on_broken_file(self):
        from app.storage_files import preview_file
        ok, error, rows = preview_file(os.path.join(self.dir, "absent.csv"), "csv")
        self.assertFalse(ok)
        self.assertIn("не удалось прочитать файл", error)
        self.assertEqual(rows, [])


@unittest.skipUnless(HAS_DUCKDB, "duckdb + pandas + pytz required")
class TestDataMapTableNames(unittest.TestCase):
    """Регрессия: имя таблицы из data_map подставлялось в CREATE TABLE без кавычек — имя с точкой
    или пробелом ломало запрос (ParserException)."""

    def test_table_name_with_dot_and_space(self):
        from app.sources.duckdb import execute_duckdb
        ok, info, _func, rows = execute_duckdb(
            {"type": "table", "queries": ['SELECT COUNT(*) AS c FROM "my data.2026"']},
            {"json": {}}, {"my data.2026": [{"a": 1}, {"a": 2}]}, CS)
        self.assertTrue(ok, info)
        self.assertEqual(rows, [{"c": 2}])


if __name__ == "__main__":
    unittest.main()
