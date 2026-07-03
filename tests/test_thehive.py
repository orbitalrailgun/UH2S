"""Тесты пересборки уплощённого алерта TheHive (app/sources/thehive.regroup_thehive_alert) — офлайн."""
import json
import unittest

from app.sources.thehive import regroup_thehive_alert, flatten_data


class TestTheHiveRegroup(unittest.TestCase):
    def _alert(self):
        return {
            "title": "susp login", "severity": 2,
            "tags": ["phishing", "critical", "T1078"],
            "customFields": [
                {"_id": "cf1", "name": "source_ip", "type": "string", "value": "10.0.0.5", "order": 1},
                {"_id": "cf2", "name": "risk_score", "type": "integer", "value": 87, "order": 2},
            ],
        }

    def test_tags_to_textual_list(self):
        out = regroup_thehive_alert(flatten_data(self._alert()))
        self.assertEqual(out["tags"], json.dumps(["phishing", "critical", "T1078"], ensure_ascii=False))
        self.assertIsInstance(out["tags"], str)

    def test_customfields_to_named_columns(self):
        out = regroup_thehive_alert(flatten_data(self._alert()))
        self.assertEqual(out["source_ip"], "10.0.0.5")
        self.assertEqual(out["risk_score"], 87)

    def test_raw_columns_removed_and_others_kept(self):
        out = regroup_thehive_alert(flatten_data(self._alert()))
        self.assertFalse(any(k.startswith("tags_") or k.startswith("customFields_") for k in out))
        self.assertEqual(out["title"], "susp login")
        self.assertEqual(out["severity"], 2)

    def test_no_tags_no_customfields(self):
        out = regroup_thehive_alert(flatten_data({"title": "x", "severity": 1}))
        self.assertNotIn("tags", out)
        self.assertEqual(out, {"title": "x", "severity": 1})

    def test_customfield_without_name_skipped(self):
        flat = flatten_data({"customFields": [{"_id": "c", "value": "v", "order": 1}]})  # без name
        out = regroup_thehive_alert(flat)
        self.assertFalse(any(k.startswith("customFields_") for k in out))
        self.assertEqual(out, {})   # нет name -> столбец не создаём, сырые убраны


if __name__ == "__main__":
    unittest.main()
