"""Офлайн-тесты разбора загружаемых таблиц (app/tabular). CSV — stdlib, тестируется без pandas."""
import unittest

from app.tabular import parse_table_file


class TestParseTableFile(unittest.TestCase):
    def test_csv_basic(self):
        ok, err, records = parse_table_file(b"host,ip\nsrv1,10.0.0.1\nsrv2,10.0.0.2\n", "inventory.csv")
        self.assertTrue(ok, err)
        self.assertEqual(records, [{"host": "srv1", "ip": "10.0.0.1"}, {"host": "srv2", "ip": "10.0.0.2"}])

    def test_csv_with_bom(self):
        ok, err, records = parse_table_file("﻿a,b\n1,2\n".encode("utf-8"), "x.csv")
        self.assertTrue(ok, err)
        self.assertEqual(records, [{"a": "1", "b": "2"}])

    def test_csv_cp1251_fallback(self):
        ok, err, records = parse_table_file("имя,город\nсервер,Москва\n".encode("cp1251"), "ru.csv")
        self.assertTrue(ok, err)
        self.assertEqual(records[0]["город"], "Москва")

    def test_empty_content(self):
        self.assertFalse(parse_table_file(b"", "a.csv")[0])

    def test_unsupported_extension(self):
        ok, err, _ = parse_table_file(b"whatever", "a.txt")
        self.assertFalse(ok)
        self.assertIn(".csv", err)

    def test_csv_only_header_gives_empty_records(self):
        ok, err, records = parse_table_file(b"col1,col2\n", "h.csv")
        self.assertTrue(ok, err)
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
