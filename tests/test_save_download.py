"""Офлайн-тесты records_to_download: сборка файла НА ДИСКЕ (возврат пути), авто-откат
xlsx→csv_in_zip при превышении лимита строк листа Excel, потоковая запись csv/json.
Порог EXCEL_MAX_ROWS подменяется на маленький, чтобы не материализовать миллион строк.
Требует pandas/openpyxl (как APPLY-тесты) — иначе пропуск."""
import io
import os
import unittest
import zipfile

try:
    import pandas  # noqa: F401
    import openpyxl  # noqa: F401
    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False

if HAS_XLSX:
    import app.interface as interface
    from app.interface import records_to_download


@unittest.skipUnless(HAS_XLSX, "pandas + openpyxl required")
class TestRecordsToDownload(unittest.TestCase):
    def setUp(self):
        self.rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}]
        self._paths = []

    def tearDown(self):
        for path in self._paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def _build(self, tables, fmt, base):
        path, filename, media, warning = records_to_download(tables, fmt, base)
        self._paths.append(path)
        self.assertTrue(os.path.exists(path))  # файл собран на диске
        return path, filename, media, warning

    def test_xlsx_small_no_warning(self):
        path, filename, media, warning = self._build({"t": self.rows}, "xlsx", "out")
        self.assertTrue(filename.endswith(".xlsx"))
        self.assertIsNone(warning)
        self.assertIn("spreadsheetml", media)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(2), b"PK")  # xlsx — это zip-контейнер

    def test_csv_in_zip_passthrough_no_warning(self):
        _path, filename, media, warning = self._build({"t": self.rows}, "csv_in_zip", "out")
        self.assertTrue(filename.endswith(".csv.zip"))
        self.assertEqual(media, "application/zip")
        self.assertIsNone(warning)

    def test_xlsx_overflow_falls_back_to_csv_zip(self):
        original = interface.EXCEL_MAX_ROWS
        interface.EXCEL_MAX_ROWS = 3  # порог: помещается <=2 строк данных (3 - заголовок)
        try:
            path, filename, media, warning = self._build({"big": self.rows}, "xlsx", "dump")
        finally:
            interface.EXCEL_MAX_ROWS = original
        self.assertTrue(filename.endswith(".csv.zip"))
        self.assertEqual(media, "application/zip")
        self.assertEqual(warning, {"rows": 3, "limit": 3})
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].endswith(".csv"))
            body = zf.read(names[0]).decode("utf-8-sig")
            self.assertIn("a,b", body)
            self.assertIn("3,z", body)

    def test_csv_stream_heterogeneous_columns(self):
        data = [{"a": 1}, {"b": 2}, {"a": 3, "b": 4}]
        path, _fn, _m, warning = self._build({"t": data}, "csv_in_zip", "out")
        self.assertIsNone(warning)
        with zipfile.ZipFile(path) as zf:
            body = zf.read(zf.namelist()[0]).decode("utf-8-sig")
        lines = body.splitlines()
        self.assertEqual(lines[0], "a,b")
        self.assertEqual(lines[1], "1,")
        self.assertEqual(lines[2], ",2")
        self.assertEqual(lines[3], "3,4")

    def test_csv_stream_nested_values_json(self):
        import csv as _csv
        data = [{"id": 1, "tags": ["x", "y"], "meta": {"k": "v"}}]
        path, _fn, _m, _w = self._build({"t": data}, "csv_in_zip", "out")
        with zipfile.ZipFile(path) as zf:
            body = zf.read(zf.namelist()[0]).decode("utf-8-sig")
        rows = list(_csv.DictReader(io.StringIO(body)))
        self.assertEqual(rows[0]["tags"], '["x", "y"]')
        self.assertEqual(rows[0]["meta"], '{"k": "v"}')

    def test_json_stream_is_valid_array(self):
        import json as _json
        data = [{"a": 1}, {"b": "два"}]
        path, filename, _media, _w = self._build({"t": data}, "json_in_zip", "out")
        self.assertTrue(filename.endswith(".json.zip"))
        with zipfile.ZipFile(path) as zf:
            parsed = _json.loads(zf.read(zf.namelist()[0]).decode("utf-8"))
        self.assertEqual(parsed, data)

    def test_overflow_only_triggers_for_xlsx(self):
        original = interface.EXCEL_MAX_ROWS
        interface.EXCEL_MAX_ROWS = 1
        try:
            _path, filename, _media, warning = self._build({"t": self.rows}, "csv_in_zip", "out")
        finally:
            interface.EXCEL_MAX_ROWS = original
        self.assertIsNone(warning)
        self.assertTrue(filename.endswith(".csv.zip"))

    def test_unknown_format_raises_no_leak(self):
        with self.assertRaises(ValueError):
            records_to_download({"t": self.rows}, "parquet", "out")


class TestServeDownload(unittest.TestCase):
    """Ветвление _serve_download: небольшой файл -> blob (bytes через ui.download, не зависит от
    доверия к TLS-сертификату — регресс SAVE(xlsx) под самоподписанным HTTPS), большой -> роут /download."""

    def setUp(self):
        import tempfile
        import app.interface as interface
        self.interface = interface
        self._orig_download = interface.ui.download
        self._orig_max = interface.DOWNLOAD_INLINE_MAX_BYTES
        self.calls = {"blob": [], "url": []}

        test_case = self

        class _FakeDownload:
            def __call__(self, content, filename, media_type=""):
                test_case.calls["blob"].append((bytes(content), filename, media_type))

            def from_url(self, url, filename=None, media_type=""):
                test_case.calls["url"].append((url, filename))

        interface.ui.download = _FakeDownload()
        fd, self.path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)

    def tearDown(self):
        self.interface.ui.download = self._orig_download
        self.interface.DOWNLOAD_INLINE_MAX_BYTES = self._orig_max
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_small_file_served_as_blob_and_deleted(self):
        with open(self.path, "wb") as handle:
            handle.write(b"data" * 100)
        self.interface.DOWNLOAD_INLINE_MAX_BYTES = 50 * 1024 * 1024
        token = self.interface._serve_download(self.path, "siem_test.xlsx", "application/octet-stream")
        self.assertIsNone(token)                       # ушло blob'ом, роут не задействован
        self.assertEqual(len(self.calls["blob"]), 1)
        self.assertEqual(self.calls["blob"][0][1], "siem_test.xlsx")
        self.assertEqual(self.calls["url"], [])
        self.assertFalse(os.path.exists(self.path))    # temp-файл удалён после отдачи

    def test_large_file_served_via_route(self):
        with open(self.path, "wb") as handle:
            handle.write(b"y" * 5000)
        self.interface.DOWNLOAD_INLINE_MAX_BYTES = 100  # маленький порог -> файл считается «большим»
        token = self.interface._serve_download(self.path, "big.csv.zip", "application/zip")
        self.assertTrue(token)                          # выдан токен роута
        self.assertEqual(len(self.calls["url"]), 1)
        self.assertIn(f"/download/{token}", self.calls["url"][0][0])
        self.assertEqual(self.calls["blob"], [])
        self.assertTrue(os.path.exists(self.path))      # файл удалит роут после отдачи, не _serve_download


if __name__ == "__main__":
    unittest.main()
