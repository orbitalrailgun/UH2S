"""Офлайн-тесты чистых хелперов AI-агента (app/ai_pipeline) — без nicegui/LLM/БД."""
import unittest

from app.ai_pipeline import extract_action, extract_final_harvester, parse_save_object, AGENT_ACTIONS

B = "`" * 3


class TestExtractAction(unittest.TestCase):
    def test_run(self):
        self.assertEqual(extract_action(f"тест {B}run\nGET a:b() AS d\n{B} конец"), ("run", "GET a:b() AS d"))

    def test_none(self):
        self.assertEqual(extract_action("просто текст"), (None, None))

    def test_harvester_is_not_action(self):
        self.assertEqual(extract_action(f"{B}harvester\nGET a:b() AS d\n{B}"), (None, None))


class TestExtractFinalHarvester(unittest.TestCase):
    def test_last_block(self):
        text = f"{B}harvester\nX\n{B}  {B}harvester\nGET y:z() AS d\n{B}"
        self.assertEqual(extract_final_harvester(text), "GET y:z() AS d")

    def test_none(self):
        self.assertIsNone(extract_final_harvester("нет блока"))


class TestParseSaveObject(unittest.TestCase):
    def test_ok(self):
        ok, err, norm = parse_save_object('{"name":"soc","json":{"script":"GET a:b() AS d","return":"d"}}')
        self.assertTrue(ok, err)
        self.assertEqual(norm["name"], "soc")
        self.assertEqual(norm["type"], "script")
        self.assertEqual(norm["json"]["script"], "GET a:b() AS d")
        self.assertEqual(norm["roles"], ["fullmaster"])

    def test_roles_passthrough(self):
        ok, err, norm = parse_save_object('{"name":"s","roles":["aiadmin"],"json":{"script":"x"}}')
        self.assertTrue(ok, err)
        self.assertEqual(norm["roles"], ["aiadmin"])

    def test_no_name(self):
        self.assertFalse(parse_save_object('{"json":{"script":"x"}}')[0])

    def test_bad_json(self):
        self.assertFalse(parse_save_object("{not json")[0])

    def test_no_script(self):
        self.assertFalse(parse_save_object('{"name":"x","json":{}}')[0])

    def test_only_script_type(self):
        self.assertFalse(parse_save_object('{"name":"x","type":"source","json":{"script":"y"}}')[0])


class TestAgentActions(unittest.TestCase):
    def test_save_object_registered(self):
        self.assertIn("save_object", AGENT_ACTIONS)


if __name__ == "__main__":
    unittest.main()
