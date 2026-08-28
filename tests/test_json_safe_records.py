"""Регрессия: записи, которые шаг отдаёт наружу, должны быть JSON-сериализуемыми.

duckdb по CSV/parquet выводит типы, поэтому в строках оказывались `pandas.Timestamp` (а также
Decimal, numpy-скаляры, BLOB). orjson в NiceGUI на них падал: emit по websocket не уходил, и запрос
выглядел «вечно выполняющимся» (`SELECT * FROM big_csv_table LIMIT 20`). Те же значения ломали и
`json.dumps` — то есть SAVE в storage и HTTP API.

Уровень значения тестируется офлайн; DataFrame-уровень требует pandas, движки — duckdb/pytz."""
import datetime
import decimal
import json
import unittest
import uuid

from app.sources.additional.sql_cells import json_safe_records, json_safe_value

try:
    import pandas  # noqa: F401
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


class TestJsonSafeValue(unittest.TestCase):
    def test_json_native_values_pass_through(self):
        for value in ("text", 42, 3.5, True, False, None):
            self.assertEqual(json_safe_value(value), value)

    def test_datetime_becomes_iso(self):
        self.assertEqual(json_safe_value(datetime.datetime(2026, 8, 28, 10, 0)), "2026-08-28T10:00:00")
        self.assertEqual(json_safe_value(datetime.date(2026, 8, 28)), "2026-08-28")
        self.assertEqual(json_safe_value(datetime.time(10, 30)), "10:30:00")

    def test_timedelta_becomes_seconds(self):
        self.assertEqual(json_safe_value(datetime.timedelta(hours=1, seconds=30)), 3630.0)

    def test_decimal_uuid_bytes(self):
        self.assertEqual(json_safe_value(decimal.Decimal("1.5")), 1.5)
        identifier = uuid.uuid4()
        self.assertEqual(json_safe_value(identifier), str(identifier))
        self.assertEqual(json_safe_value(b"\x00\x01"), "0001")
        self.assertEqual(json_safe_value(bytearray(b"\xff")), "ff")

    def test_nan_and_inf_become_null(self):
        # json.dumps пишет их как NaN/Infinity — это не валидный JSON
        self.assertIsNone(json_safe_value(float("nan")))
        self.assertIsNone(json_safe_value(float("inf")))
        self.assertIsNone(json_safe_value(float("-inf")))

    def test_containers_are_walked(self):
        value = {"when": datetime.date(2026, 1, 1), "items": [decimal.Decimal("2"), {"b": b"\x01"}]}
        self.assertEqual(json_safe_value(value),
                         {"when": "2026-01-01", "items": [2.0, {"b": "01"}]})
        self.assertEqual(json_safe_value({1, 2}), [1, 2])

    def test_unknown_object_falls_back_to_string_and_never_raises(self):
        class Exotic:
            def __repr__(self):
                return "<exotic>"

        self.assertEqual(json_safe_value(Exotic()), "<exotic>")

    def test_records_are_normalized_and_serializable(self):
        records = [{"ts": datetime.datetime(2026, 8, 28, 10, 0), "n": decimal.Decimal("1")}, "scalar-row"]
        safe = json_safe_records(records)
        self.assertEqual(safe, [{"ts": "2026-08-28T10:00:00", "n": 1.0}, {"value": "scalar-row"}])
        json.dumps(safe)      # не должно бросать


@unittest.skipUnless(HAS_PANDAS, "pandas required")
class TestDataframeToRecords(unittest.TestCase):
    def _records(self, frame):
        from app.sources.additional.sql_cells import dataframe_to_records
        return dataframe_to_records(frame)

    def test_timestamps_become_iso_strings(self):
        frame = pandas.DataFrame([{"ts": pandas.Timestamp("2026-08-28T10:00:00Z"), "n": 1}])
        records = self._records(frame)
        self.assertIsInstance(records[0]["ts"], str)
        self.assertIn("2026-08-28T10:00:00", records[0]["ts"])
        json.dumps(records)

    def test_missing_values_become_null(self):
        frame = pandas.DataFrame([{"ts": pandas.NaT, "x": float("nan")}])
        records = self._records(frame)
        self.assertIsNone(records[0]["ts"])
        self.assertIsNone(records[0]["x"])

    def test_blob_and_uuid_columns_are_predictable(self):
        # именно из-за таких колонок отказались от быстрого to_json: он падает на не-UTF8 байтах
        # и UUID, а ASCII-байты превращал в строку с управляющими символами
        frame = pandas.DataFrame([{"blob": b"\x00\x01", "ts": pandas.Timestamp("2026-01-01")}])
        records = self._records(frame)
        self.assertEqual(records[0]["blob"], "0001")
        self.assertIsInstance(records[0]["ts"], str)
        json.dumps(records)

    def test_non_utf8_blob_and_uuid_do_not_break(self):
        frame = pandas.DataFrame([{"blob": b"\xff\xfe", "uid": uuid.uuid4(), "dec": decimal.Decimal("1.5")}])
        records = self._records(frame)
        self.assertEqual(records[0]["blob"], "fffe")
        self.assertIsInstance(records[0]["uid"], str)
        self.assertEqual(records[0]["dec"], 1.5)          # число, а не строка "1.5"
        json.dumps(records)

    def test_numpy_scalars_are_python_types(self):
        frame = pandas.DataFrame([{"i": 5, "f": 1.5, "b": True}])
        records = self._records(frame)
        self.assertEqual({type(v).__name__ for v in records[0].values()}, {"int", "float", "bool"})
        json.dumps(records)


@unittest.skipUnless(HAS_PANDAS, "pandas required")
class TestAggridOptionsAreSerializable(unittest.TestCase):
    def test_exotic_values_do_not_break_serialization(self):
        try:
            from nicegui.json import dumps as nicegui_dumps
        except ImportError:
            self.skipTest("nicegui required")
        from app.interface import records_to_aggrid_options
        rows = [{"ts": pandas.Timestamp("2026-08-28T10:00:00Z"), "d": decimal.Decimal("1.5"),
                 "nat": pandas.NaT, "blob": b"\x00\x01"}]
        options = records_to_aggrid_options(rows)
        nicegui_dumps(options)                       # раньше здесь падал emit -> «вечное выполнение»
        self.assertIsNone(options["rowData"][0]["nat"])
        self.assertEqual(options["rowData"][0]["blob"], "0001")


if __name__ == "__main__":
    unittest.main()
