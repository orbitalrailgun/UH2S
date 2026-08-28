"""Офлайн-тесты файлового хранения больших таблиц (app/storage_files) — без duckdb и без nicegui.

`stage_upload` работает с любым объектом, у которого есть `size()` и async `save(path)`, поэтому
загрузка проверяется на подставном объекте — как реальный FileUpload из NiceGUI, но без зависимости.
"""
import asyncio
import os
import shutil
import tempfile
import unittest

import app.storage_files as storage_files
from app.storage_files import (detect_format, file_view_sql, quote_identifier, quote_literal,
                               remove_file, should_store_as_file, stage_upload,
                               storage_mode_for_upload, sweep_orphans)


class FakeUpload:
    """Подставной FileUpload: пишет заданные байты по указанному пути (как LargeFileUpload.save)."""

    def __init__(self, name, data=b"a,b\n1,2\n"):
        self.name = name
        self._data = data

    def size(self):
        return len(self._data)

    async def save(self, path):
        with open(path, "wb") as handle:
            handle.write(self._data)


class StorageDirCase(unittest.TestCase):
    """Каталог хранилища подменяется на временный — тесты не трогают рабочий каталог."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="uh2s_test_storage_")
        self._saved_env = os.environ.get("UH2S_STORAGE_DIR")
        os.environ["UH2S_STORAGE_DIR"] = self._dir

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("UH2S_STORAGE_DIR", None)
        else:
            os.environ["UH2S_STORAGE_DIR"] = self._saved_env
        shutil.rmtree(self._dir, ignore_errors=True)


class TestDetectFormat(unittest.TestCase):
    def test_supported_formats(self):
        self.assertEqual(detect_format("events.csv")[:2], (True, "csv"))
        self.assertEqual(detect_format("EVENTS.CSV")[:2], (True, "csv"))
        self.assertEqual(detect_format("events.csv.gz"), (True, "csv", "csv.gz"))
        self.assertEqual(detect_format("data.jsonl")[:2], (True, "ndjson"))
        self.assertEqual(detect_format("data.ndjson.gz"), (True, "ndjson", "ndjson.gz"))
        self.assertEqual(detect_format("data.tsv")[:2], (True, "tsv"))
        self.assertEqual(detect_format("data.parquet")[:2], (True, "parquet"))

    def test_double_extension_wins_over_short_one(self):
        # .csv.gz должен определиться как gz-вариант, а не как csv
        self.assertEqual(detect_format("events.csv.gz")[2], "csv.gz")

    def test_rejected_formats(self):
        for name in ("payload.exe", "table.xlsx", "no_extension", "", None):
            ok, message, extension = detect_format(name)
            self.assertFalse(ok, name)
            self.assertIn("неподдерживаемый формат", message)
            self.assertEqual(extension, "")


class TestStorageModeChoice(unittest.TestCase):
    def test_by_threshold(self):
        self.assertFalse(should_store_as_file(10, None, threshold=100))
        self.assertTrue(should_store_as_file(100, None, threshold=100))
        self.assertTrue(should_store_as_file(1000, None, threshold=100))

    def test_explicit_choice_wins(self):
        self.assertTrue(should_store_as_file(1, True, threshold=10 ** 9))
        self.assertFalse(should_store_as_file(10 ** 9, False, threshold=1))

    def test_bad_size_is_not_a_file(self):
        self.assertFalse(should_store_as_file(None, None, threshold=1))
        self.assertFalse(should_store_as_file("x", None, threshold=1))


class TestStorageModeForUpload(unittest.TestCase):
    """Какой режим выбирается при загрузке: файлом или строками в БД."""

    def test_large_supported_format_goes_to_file(self):
        self.assertEqual(storage_mode_for_upload("events.csv", 10 ** 9, None, threshold=1000), "file")
        self.assertEqual(storage_mode_for_upload("events.parquet", 10 ** 9, None, threshold=1000), "file")

    def test_small_file_stays_rows(self):
        self.assertEqual(storage_mode_for_upload("events.csv", 10, None, threshold=1000), "rows")

    def test_xlsx_is_always_parsed_to_rows(self):
        # duckdb xlsx не читает: большой xlsx должен разбираться как раньше, а не падать на формате
        self.assertEqual(storage_mode_for_upload("book.xlsx", 10 ** 9, None, threshold=1000), "rows")
        self.assertEqual(storage_mode_for_upload("book.xls", 10 ** 9, True, threshold=1000), "rows")

    def test_explicit_switch_forces_file_for_supported_format(self):
        self.assertEqual(storage_mode_for_upload("events.csv", 1, True, threshold=10 ** 9), "file")


class TestSqlQuoting(unittest.TestCase):
    def test_identifier_quoted_and_escaped(self):
        self.assertEqual(quote_identifier("events"), '"events"')
        self.assertEqual(quote_identifier("claude events.2026"), '"claude events.2026"')
        self.assertEqual(quote_identifier('ev"il'), '"ev""il"')

    def test_literal_quoted_and_escaped(self):
        self.assertEqual(quote_literal("/data/a.csv"), "'/data/a.csv'")
        self.assertEqual(quote_literal("/data/it's.csv"), "'/data/it''s.csv'")

    def test_view_sql_uses_reader_per_format(self):
        self.assertIn("read_csv_auto('/d/a.csv')", file_view_sql("t", "/d/a.csv", "csv"))
        self.assertIn("read_json_auto('/d/a.jsonl')", file_view_sql("t", "/d/a.jsonl", "ndjson"))
        self.assertIn("read_parquet('/d/a.parquet')", file_view_sql("t", "/d/a.parquet", "parquet"))
        self.assertTrue(file_view_sql("odd name", "/d/a.csv", "csv").startswith(
            'CREATE OR REPLACE VIEW "odd name" AS'))

    def test_unknown_format_falls_back_to_csv_reader(self):
        self.assertIn("read_csv_auto", file_view_sql("t", "/d/a.dat", "whatever"))


class TestStageUpload(StorageDirCase):
    def test_file_is_written_with_server_generated_name(self):
        ok, error, meta = asyncio.run(stage_upload(FakeUpload("../../etc/passwd.csv")))
        self.assertTrue(ok, error)
        self.assertTrue(os.path.isfile(meta["path"]))
        # путь всегда внутри каталога хранилища, имя серверное — от исходного имени ничего не остаётся
        self.assertEqual(os.path.dirname(meta["path"]), self._dir)
        self.assertNotIn("passwd", os.path.basename(meta["path"]))
        self.assertTrue(meta["path"].endswith(".csv"))
        self.assertEqual(meta["name"], "../../etc/passwd.csv")     # исходное имя — только в метаданных
        self.assertEqual((meta["format"], meta["size_bytes"]), ("csv", 8))

    def test_two_uploads_do_not_collide(self):
        first = asyncio.run(stage_upload(FakeUpload("a.csv")))[2]["path"]
        second = asyncio.run(stage_upload(FakeUpload("a.csv")))[2]["path"]
        self.assertNotEqual(first, second)

    def test_unsupported_format_rejected_without_writing(self):
        ok, error, meta = asyncio.run(stage_upload(FakeUpload("payload.exe")))
        self.assertFalse(ok)
        self.assertIn("неподдерживаемый формат", error)
        self.assertEqual(meta, {})
        self.assertEqual(os.listdir(self._dir), [])

    def test_size_limit_rejected_before_saving(self):
        ok, error, _meta = asyncio.run(stage_upload(FakeUpload("a.csv", b"x" * 100), max_bytes=10))
        self.assertFalse(ok)
        self.assertIn("больше предела", error)
        self.assertEqual(os.listdir(self._dir), [])

    def test_parquet_without_magic_rejected_and_cleaned_up(self):
        ok, error, _meta = asyncio.run(stage_upload(FakeUpload("t.parquet", b"not-parquet")))
        self.assertFalse(ok)
        self.assertIn("PAR1", error)
        self.assertEqual(os.listdir(self._dir), [])            # мусор за собой не оставили

    def test_parquet_with_magic_accepted(self):
        ok, error, meta = asyncio.run(stage_upload(FakeUpload("t.parquet", b"PAR1rest-of-file")))
        self.assertTrue(ok, error)
        self.assertEqual(meta["format"], "parquet")

    def test_save_failure_leaves_no_file(self):
        class BrokenUpload(FakeUpload):
            async def save(self, path):
                with open(path, "wb") as handle:
                    handle.write(b"partial")
                raise OSError("disk full")

        ok, error, _meta = asyncio.run(stage_upload(BrokenUpload("a.csv")))
        self.assertFalse(ok)
        self.assertIn("disk full", error)
        self.assertEqual(os.listdir(self._dir), [])


class TestRemoveAndSweep(StorageDirCase):
    def test_remove_file_is_idempotent(self):
        path = asyncio.run(stage_upload(FakeUpload("a.csv")))[2]["path"]
        self.assertTrue(remove_file(path))
        self.assertFalse(remove_file(path))                    # второй раз — не ошибка
        self.assertFalse(remove_file(None))

    def test_sweep_removes_only_unknown_files(self):
        kept = asyncio.run(stage_upload(FakeUpload("keep.csv")))[2]["path"]
        orphan = asyncio.run(stage_upload(FakeUpload("orphan.csv")))[2]["path"]
        self.assertEqual(sweep_orphans([kept]), 1)
        self.assertTrue(os.path.isfile(kept))
        self.assertFalse(os.path.exists(orphan))

    def test_sweep_with_no_known_paths_clears_directory(self):
        asyncio.run(stage_upload(FakeUpload("a.csv")))
        asyncio.run(stage_upload(FakeUpload("b.csv")))
        self.assertEqual(sweep_orphans([]), 2)
        self.assertEqual(os.listdir(self._dir), [])


class TestEnvLimits(unittest.TestCase):
    def _with_env(self, name, value, getter):
        saved = os.environ.get(name)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        try:
            return getter()
        finally:
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved

    def test_upload_max_bytes_from_env(self):
        self.assertEqual(self._with_env("UH2S_UPLOAD_MAX_BYTES", "123", storage_files.upload_max_bytes), 123)
        self.assertEqual(self._with_env("UH2S_UPLOAD_MAX_BYTES", None, storage_files.upload_max_bytes),
                         storage_files.DEFAULT_UPLOAD_MAX_BYTES)
        # мусор в переменной не должен ломать загрузку — берём дефолт
        self.assertEqual(self._with_env("UH2S_UPLOAD_MAX_BYTES", "nonsense", storage_files.upload_max_bytes),
                         storage_files.DEFAULT_UPLOAD_MAX_BYTES)

    def test_threshold_from_env(self):
        self.assertEqual(self._with_env("UH2S_STORAGE_FILE_THRESHOLD_BYTES", "7",
                                        storage_files.file_threshold_bytes), 7)
        self.assertEqual(self._with_env("UH2S_STORAGE_FILE_THRESHOLD_BYTES", "0",
                                        storage_files.file_threshold_bytes),
                         storage_files.DEFAULT_FILE_THRESHOLD_BYTES)


if __name__ == "__main__":
    unittest.main()
