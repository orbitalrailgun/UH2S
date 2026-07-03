"""Тесты сборки FREETEXT-IQL для CMDB jira_sm (app/sources/jira_sm._build_freetext_iql) — офлайн."""
import unittest

from app.sources.jira_sm import _build_freetext_iql


class TestFreetextIQL(unittest.TestCase):
    def test_freetext_only(self):
        self.assertEqual(_build_freetext_iql("192.168.0.1"), '"192.168.0.1"')

    def test_with_object_type(self):
        self.assertEqual(_build_freetext_iql("laptop", "Host"), 'objectType = "Host" AND "laptop"')

    def test_quotes_escaped(self):
        self.assertEqual(_build_freetext_iql('say "hi"', "Server"), 'objectType = "Server" AND "say \\"hi\\""')

    def test_empty_freetext_none(self):
        self.assertIsNone(_build_freetext_iql(""))
        self.assertIsNone(_build_freetext_iql("   "))
        self.assertIsNone(_build_freetext_iql(None, "Host"))   # freetext обязателен

    def test_strips(self):
        self.assertEqual(_build_freetext_iql("  db01  "), '"db01"')


if __name__ == "__main__":
    unittest.main()
