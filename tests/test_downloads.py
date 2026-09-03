"""Офлайн-тесты реестра one-shot загрузок (app/downloads) — без сети и без pandas."""
import os
import time
import unittest

from app.downloads import (MODE_EXTERNAL, MODE_REUSABLE, register_download, consume_download,
                           export_tempfile, _registry, _sweep_expired)
import app.downloads as downloads


class TestDownloadsRegistry(unittest.TestCase):
    def setUp(self):
        _registry.clear()
        self._paths = []

    def tearDown(self):
        _registry.clear()
        for path in self._paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def _tempfile(self, suffix=".bin"):
        path = export_tempfile(suffix)
        self._paths.append(path)
        return path

    def test_export_tempfile_created_and_unique(self):
        p1, p2 = self._tempfile(), self._tempfile()
        self.assertTrue(os.path.exists(p1) and os.path.exists(p2))
        self.assertNotEqual(p1, p2)

    def test_register_returns_unguessable_unique_token(self):
        t1 = register_download(self._tempfile(), "a.bin", "application/octet-stream")
        t2 = register_download(self._tempfile(), "b.bin")
        self.assertNotEqual(t1, t2)
        self.assertGreater(len(t1), 20)  # token_urlsafe(32) — длинный

    def test_consume_returns_entry_then_none(self):
        path = self._tempfile()
        token = register_download(path, "file.csv.zip", "application/zip")
        entry = consume_download(token)
        # 4-й элемент — delete_after: временный экспорт удаляется после отдачи
        self.assertEqual(entry, (path, "file.csv.zip", "application/zip", True))
        # one-shot: повторно — уже нет
        self.assertIsNone(consume_download(token))

    def test_consume_unknown_token(self):
        self.assertIsNone(consume_download("nope"))

    def _expire(self, token):
        path, filename, media_type, _created, mode = _registry[token]
        _registry[token] = (path, filename, media_type,
                            time.monotonic() - downloads.DOWNLOAD_TTL_SECONDS - 10, mode)

    def test_sweep_keeps_external_files_on_disk(self):
        # ссылка истекает, но сам файл хранилища остаётся: иначе через час данные пропали бы,
        # а запись в БД осталась
        path = self._tempfile()
        token = register_download(path, "events.csv", "text/csv", mode=MODE_EXTERNAL)
        self._expire(token)
        _sweep_expired(time.monotonic())
        self.assertNotIn(token, _registry)      # ссылка убрана
        self.assertTrue(os.path.exists(path))   # файл на месте

    def test_sweep_removes_reusable_export_after_ttl(self):
        # переиспользуемый экспорт SAVE — всё-таки temp-файл: по TTL он должен убираться
        path = self._tempfile()
        token = register_download(path, "export.xlsx", "", mode=MODE_REUSABLE)
        self._expire(token)
        _sweep_expired(time.monotonic())
        self.assertFalse(os.path.exists(path))

    def test_reusable_link_can_be_consumed_twice(self):
        # несколько SAVE в прогоне отдаются кнопками, поэтому ссылка не одноразовая
        path = self._tempfile()
        token = register_download(path, "export.xlsx", "", mode=MODE_REUSABLE)
        first = consume_download(token)
        second = consume_download(token)
        self.assertEqual(first, (path, "export.xlsx", "", False))
        self.assertEqual(second, first)
        self.assertTrue(os.path.exists(path))

    def test_external_file_is_marked_not_to_delete(self):
        # файлы хранилища (app/storage_files.py) — это сами данные, после отдачи их удалять нельзя
        path = self._tempfile()
        token = register_download(path, "events.csv", "text/csv", mode=MODE_EXTERNAL)
        self.assertEqual(consume_download(token), (path, "events.csv", "text/csv", False))
        self.assertTrue(os.path.exists(path))

    def test_sweep_expired_removes_old_unclaimed_files(self):
        path = self._tempfile()
        token = register_download(path, "old.zip")
        # сделать запись «старой»
        self._expire(token)
        _sweep_expired(time.monotonic())
        self.assertNotIn(token, _registry)     # запись убрана
        self.assertFalse(os.path.exists(path))  # файл удалён с диска


if __name__ == "__main__":
    unittest.main()
