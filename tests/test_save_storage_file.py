"""Тесты SAVE(<таблица>, storage_file[, ttl]) AS <ключ> — запись таблицы файлом в хранилище.

Зачем: долгие прогоны (часы/дни) должны складывать промежуточные результаты на сервер, а обычный
storage держит таблицу JSON-блобом в ячейке (предел ~1 ГБ и материализация всех строк в RAM).

Уровень записи файла и разбор команды — офлайн; parquet требует duckdb (иначе фолбэк на csv)."""
import csv
import os
import shutil
import tempfile
import unittest

from app.engine import command_parser, run_save_storage_command
from app.storage_files import write_records_file

try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

CS = {"app_name": "t", "app_version": "0", "username": "u"}
ROWS = [{"host": "srv-1", "level": "INFO", "n": 1},
        {"host": "srv-2", "level": "ERROR", "n": 2},
        {"host": "srv-3", "level": "INFO", "n": 3, "extra": "поле есть не у всех"}]


class StorageDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="uh2s_save_file_")
        self._saved_env = os.environ.get("UH2S_STORAGE_DIR")
        os.environ["UH2S_STORAGE_DIR"] = self.dir

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("UH2S_STORAGE_DIR", None)
        else:
            os.environ["UH2S_STORAGE_DIR"] = self._saved_env
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSaveStorageFileParsing(unittest.TestCase):
    def _parse(self, line):
        return command_parser(line, CS)[0]

    def test_storage_file_is_recognized(self):
        command = self._parse("SAVE(big_table, storage_file) AS intermediate")
        self.assertTrue(command["parsed"], command.get("parsed_comment"))
        self.assertTrue(command["save_is_storage"])
        self.assertTrue(command["storage_as_file"])
        self.assertEqual((command["storage_key"], command["storage_dataname"]), ("intermediate", "big_table"))
        self.assertIsNone(command["storage_ttl"])

    def test_ttl_is_parsed(self):
        command = self._parse("SAVE(big_table, storage_file, 86400) AS intermediate")
        self.assertEqual(command["storage_ttl"], 86400)

    def test_plain_storage_is_not_a_file(self):
        command = self._parse("SAVE(t, storage) AS k")
        self.assertTrue(command["save_is_storage"])
        self.assertFalse(command["storage_as_file"])

    def test_key_and_single_table_required(self):
        no_key = self._parse("SAVE(t, storage_file)")
        self.assertFalse(no_key["parsed"])
        self.assertIn("AS dataname_id", no_key["parsed_comment"])
        two_tables = self._parse("SAVE([a, b], storage_file) AS k")
        self.assertFalse(two_tables["parsed"])
        self.assertIn("exactly one dataname", two_tables["parsed_comment"])


class TestWriteRecordsFile(StorageDirCase):
    def test_csv_fallback_keeps_all_columns(self):
        ok, error, meta = write_records_file(ROWS, prefer_parquet=False)
        self.assertTrue(ok, error)
        self.assertEqual((meta["format"], meta["rows"]), ("csv", 3))
        self.assertEqual(meta["columns"], ["host", "level", "n", "extra"])   # объединение по всем строкам
        with open(meta["path"], encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["host"], "srv-1")
        self.assertEqual(rows[2]["extra"], "поле есть не у всех")

    def test_file_lands_in_storage_dir_with_server_name(self):
        meta = write_records_file(ROWS, prefer_parquet=False)[2]
        self.assertEqual(os.path.dirname(meta["path"]), self.dir)
        self.assertTrue(meta["path"].endswith(".csv"))
        self.assertGreater(meta["size_bytes"], 0)

    def test_empty_table_is_an_error(self):
        ok, error, meta = write_records_file([])
        self.assertFalse(ok)
        self.assertIn("нет строк", error)
        self.assertEqual(meta, {})
        self.assertEqual(os.listdir(self.dir), [])

    @unittest.skipUnless(HAS_DUCKDB, "duckdb required")
    def test_parquet_is_default_and_readable(self):
        ok, error, meta = write_records_file(ROWS)
        self.assertTrue(ok, error)
        self.assertEqual(meta["format"], "parquet")
        self.assertTrue(meta["path"].endswith(".parquet"))
        # промежуточный csv за собой не оставляем
        self.assertEqual([os.path.splitext(f)[1] for f in os.listdir(self.dir)], [".parquet"])
        with open(meta["path"], "rb") as handle:
            self.assertEqual(handle.read(4), b"PAR1")
        result = duckdb.connect(":memory:").execute(
            f"SELECT COUNT(*) c FROM read_parquet('{meta['path']}')").fetchone()
        self.assertEqual(result[0], 3)

    @unittest.skipUnless(HAS_DUCKDB, "duckdb required")
    def test_parquet_is_much_smaller_than_csv(self):
        rows = [{"host": f"srv-{i % 50}", "level": "INFO", "n": i} for i in range(20000)]
        parquet_meta = write_records_file(rows)[2]
        csv_meta = write_records_file(rows, prefer_parquet=False)[2]
        self.assertLess(parquet_meta["size_bytes"], csv_meta["size_bytes"])


class TestRunSaveStorageFile(StorageDirCase):
    """Исполнение шага: файл пишется, метаданные ложатся в реестр, при ошибке файл не остаётся."""

    def setUp(self):
        super().setUp()
        import app.db as db
        self.db = db
        self._saved = (db.storage_file_save, db.storage_save)
        self.saved_calls = []
        db.storage_file_save = lambda key, meta, ttl, cs: (
            self.saved_calls.append((key, dict(meta), ttl)) or (True, "Ok", "f", None))

    def tearDown(self):
        self.db.storage_file_save, self.db.storage_save = self._saved
        super().tearDown()

    def _command(self, line="SAVE(intermediate, storage_file, 3600) AS step1"):
        return command_parser(line, CS)[0]

    def test_table_is_written_and_registered(self):
        ok, info, _func, key = run_save_storage_command(self._command(), {"intermediate": ROWS}, {}, CS)
        self.assertTrue(ok, info)
        self.assertEqual(key, "step1")
        self.assertIn("as file (3 rows", info)
        registered_key, meta, ttl = self.saved_calls[0]
        self.assertEqual((registered_key, ttl), ("step1", 3600))
        self.assertEqual(meta["rows"], 3)
        self.assertEqual(meta["name"], f"step1.{meta['extension']}")   # понятное имя для скачивания
        self.assertTrue(os.path.isfile(meta["path"]))

    def test_table_from_def_variables(self):
        ok, info, _func, _key = run_save_storage_command(self._command(), {}, {"intermediate": ROWS}, CS)
        self.assertTrue(ok, info)

    def test_missing_table_reports_error(self):
        ok, info, _func, _key = run_save_storage_command(self._command(), {}, {}, CS)
        self.assertFalse(ok)
        self.assertIn("no table data", info)

    def test_empty_table_reports_error(self):
        ok, info, _func, _key = run_save_storage_command(self._command(), {"intermediate": []}, {}, CS)
        self.assertFalse(ok)
        self.assertIn("is empty", info)

    def test_registry_failure_removes_the_file(self):
        self.db.storage_file_save = lambda key, meta, ttl, cs: (False, "db is down", "f", None)
        ok, info, _func, _key = run_save_storage_command(self._command(), {"intermediate": ROWS}, {}, CS)
        self.assertFalse(ok)
        self.assertIn("db is down", info)
        self.assertEqual(os.listdir(self.dir), [])     # файла-сироты не осталось

    def test_previous_file_of_the_same_key_is_removed(self):
        stale = os.path.join(self.dir, "stale.parquet")
        with open(stale, "wb") as handle:
            handle.write(b"PAR1old")
        self.db.storage_file_save = lambda key, meta, ttl, cs: (True, "Ok", "f", stale)
        ok, info, _func, _key = run_save_storage_command(self._command(), {"intermediate": ROWS}, {}, CS)
        self.assertTrue(ok, info)
        self.assertFalse(os.path.exists(stale))

    def test_plain_storage_still_goes_to_db(self):
        stored = []
        self.db.storage_save = lambda key, records, ttl, cs: (
            stored.append((key, len(records), ttl)) or (True, "Ok", "f", key))
        command = self._command("SAVE(intermediate, storage, 60) AS rows_key")
        ok, info, _func, _key = run_save_storage_command(command, {"intermediate": ROWS}, {}, CS)
        self.assertTrue(ok, info)
        self.assertEqual(stored, [("rows_key", 3, 60)])
        self.assertEqual(self.saved_calls, [])          # файловый путь не задействован


if __name__ == "__main__":
    unittest.main()
