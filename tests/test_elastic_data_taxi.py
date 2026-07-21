"""Офлайн-тесты data_taxi (нативный elastic-клиент): диагностика «0 строк», устойчивость к пустому
результату и дедуп по _id. Сеть не нужна — elastic_client подменяется фейком с каноничными ответами.
data_taxi больше не зависит от pandas (дедуп in-place), поэтому тесты идут и в core-only окружении."""
import unittest

from app.sources.additional.elastic2python import data_taxi, dedup_by_id


class FakeClient:
    """Мок elasticsearch-клиента: .search(...) отдаёт заранее заданный ответ (dict-able)."""
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def search(self, index=None, body=None):
        self.calls += 1
        return self._response


class TestDataTaxiZeroRows(unittest.TestCase):
    def test_empty_result_no_keyerror(self):
        # фильтр ничего не нашёл: hits пустой. Раньше pandas.DataFrame([]).drop_duplicates("_id")
        # кидал KeyError '_id' -> внешний except -> False. Теперь -> корректный пустой успех.
        client = FakeClient({"hits": {"total": {"value": 0}, "hits": []}})
        ok, msg, func, data = data_taxi(client, "idx", {"match_all": {}}, [{"@timestamp": {"order": "desc"}}],
                                        ["@timestamp"], 10, -10, -1)
        self.assertTrue(ok)
        self.assertEqual(data, [])
        self.assertEqual(func, "data_taxi")
        self.assertIn("matched 0", msg)
        self.assertIn("rows 0", msg)

    def test_fields_trap_calls_debug_log_with_hint(self):
        # matched>0, но у хитов нет ключа fields (типичный _source:false + text-поле) ->
        # 0 строк, debug_log зовётся с подсказкой про fields.
        response = {"hits": {"total": {"value": 5}, "hits": [{"_id": "1"}, {"_id": "2"}]}}
        client = FakeClient(response)
        captured = {}
        ok, msg, func, data = data_taxi(client, "idx", {"match_all": {}}, [{"@timestamp": {"order": "desc"}}],
                                        ["message"], 10, -10, -1, debug=False,
                                        debug_log=lambda meta: captured.update(meta))
        self.assertTrue(ok)
        self.assertEqual(data, [])
        self.assertEqual(captured.get("matched"), 5)
        self.assertEqual(captured.get("rows"), 0)
        self.assertIn("fields", captured.get("hint", ""))

    def test_zero_matched_hint_is_filter(self):
        response = {"hits": {"total": {"value": 0}, "hits": []}}
        captured = {}
        data_taxi(FakeClient(response), "idx", {"match_all": {}}, [{"@timestamp": {"order": "desc"}}],
                  ["@timestamp"], 10, -10, -1, debug_log=lambda meta: captured.update(meta))
        self.assertIn("filter matched nothing", captured.get("hint", ""))


class TestDataTaxiHappyPath(unittest.TestCase):
    def test_rows_extracted_no_debug_log(self):
        # поля резолвятся -> строки извлекаются, debug_log НЕ зовётся, сообщение содержит matched/rows.
        response = {"hits": {"total": {"value": 2}, "hits": [
            {"_id": "1", "fields": {"@timestamp": ["2026-01-01T00:00:00Z"]}},
            {"_id": "2", "fields": {"@timestamp": ["2026-01-02T00:00:00Z"]}},
        ]}}
        called = {"n": 0}
        ok, msg, func, data = data_taxi(FakeClient(response), "idx", {"match_all": {}},
                                        [{"@timestamp": {"order": "desc"}}], ["@timestamp"], 10, -10, -1,
                                        debug_log=lambda meta: called.__setitem__("n", called["n"] + 1))
        self.assertTrue(ok)
        self.assertEqual(len(data), 2)
        self.assertEqual(called["n"], 0)
        self.assertIn("matched 2", msg)
        self.assertIn("rows 2", msg)

    def test_zero_limit_short_circuit(self):
        # limit==0 -> ранний выход без запроса
        client = FakeClient({"hits": {"total": {"value": 99}, "hits": []}})
        ok, msg, func, data = data_taxi(client, "idx", {}, [{"@timestamp": {"order": "desc"}}],
                                        ["@timestamp"], 10, -10, 0)
        self.assertTrue(ok)
        self.assertEqual(data, [])
        self.assertEqual(client.calls, 0)


class TestDedupById(unittest.TestCase):
    """dedup_by_id не требует pandas — тестируем всегда."""
    def test_keeps_first_occurrence_in_order(self):
        rows = [{"_id": "a", "n": 1}, {"_id": "b", "n": 2}, {"_id": "a", "n": 3}, {"_id": "c", "n": 4}]
        result = dedup_by_id(rows)
        self.assertEqual([r["_id"] for r in result], ["a", "b", "c"])
        self.assertEqual(result[0]["n"], 1)  # осталась именно первая строка с _id=a

    def test_in_place_returns_same_list(self):
        rows = [{"_id": "a"}, {"_id": "a"}]
        result = dedup_by_id(rows)
        self.assertIs(result, rows)          # тот же объект (in-place, без второй копии)
        self.assertEqual(len(rows), 1)

    def test_empty(self):
        self.assertEqual(dedup_by_id([]), [])

    def test_no_duplicates_unchanged(self):
        rows = [{"_id": "a"}, {"_id": "b"}]
        self.assertEqual(dedup_by_id(rows), [{"_id": "a"}, {"_id": "b"}])

    def test_missing_id_collapse_to_one(self):
        # строки без _id -> id=None -> схлопываются в одну (как drop_duplicates по NaN)
        rows = [{"x": 1}, {"x": 2}, {"_id": "a"}]
        result = dedup_by_id(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {"x": 1})
        self.assertEqual(result[1], {"_id": "a"})


if __name__ == "__main__":
    unittest.main()
