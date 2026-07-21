"""Офлайн-тесты реестра one-shot загрузок (app/downloads) — без сети и без pandas."""
import os
import time
import unittest

from app.downloads import (register_download, consume_download, export_tempfile,
                           _registry, _sweep_expired)
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
        self.assertEqual(entry, (path, "file.csv.zip", "application/zip"))
        # one-shot: повторно — уже нет
        self.assertIsNone(consume_download(token))

    def test_consume_unknown_token(self):
        self.assertIsNone(consume_download("nope"))

    def test_sweep_expired_removes_old_unclaimed_files(self):
        path = self._tempfile()
        token = register_download(path, "old.zip")
        # сделать запись «старой»
        p, fn, mt, _created = _registry[token]
        _registry[token] = (p, fn, mt, time.monotonic() - downloads.DOWNLOAD_TTL_SECONDS - 10)
        _sweep_expired(time.monotonic())
        self.assertNotIn(token, _registry)     # запись убрана
        self.assertFalse(os.path.exists(path))  # файл удалён с диска


if __name__ == "__main__":
    unittest.main()
