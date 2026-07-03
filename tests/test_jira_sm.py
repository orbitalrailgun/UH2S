"""Тесты FREETEXT-поиска CMDB jira_sm (парсинг ответа Insight) — офлайн."""
import unittest

from app.sources.jira_sm import _extract_cmdb_entries


class TestExtractCmdbEntries(unittest.TestCase):
    def test_plain_list(self):
        self.assertEqual(_extract_cmdb_entries([{"a": 1}]), [{"a": 1}])

    def test_object_entries_key(self):
        self.assertEqual(_extract_cmdb_entries({"objectEntries": [{"id": 1}]}), [{"id": 1}])

    def test_results_key(self):
        self.assertEqual(_extract_cmdb_entries({"results": [{"id": 2}]}), [{"id": 2}])

    def test_no_list(self):
        self.assertEqual(_extract_cmdb_entries({"total": 0}), [])
        self.assertEqual(_extract_cmdb_entries(None), [])
        self.assertEqual(_extract_cmdb_entries("x"), [])


if __name__ == "__main__":
    unittest.main()
