"""Офлайн-тесты чистых хелперов AI-агента (app/ai_pipeline) — без nicegui/LLM/БД."""
import unittest

from app.ai_pipeline import (extract_action, extract_final_harvester, parse_save_object,
                             parse_memory_save, rank_notes_by_query, AGENT_ACTIONS)

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

    def test_memory_actions_registered(self):
        for action in ("memory_save", "memory_search", "memory_list", "memory_get", "memory_delete"):
            self.assertIn(action, AGENT_ACTIONS)

    def test_memory_action_extracted(self):
        self.assertEqual(
            extract_action(f'{B}memory_save\n{{"title":"T","content":"C"}}\n{B}'),
            ("memory_save", '{"title":"T","content":"C"}'))


class TestParseMemorySave(unittest.TestCase):
    def test_ok(self):
        ok, err, norm = parse_memory_save('{"title":"Elastic","content":"escape quotes","tags":["elastic","sql"]}')
        self.assertTrue(ok, err)
        self.assertEqual(norm["title"], "Elastic")
        self.assertEqual(norm["content"], "escape quotes")
        self.assertEqual(norm["tags"], ["elastic", "sql"])

    def test_tags_default_empty(self):
        ok, err, norm = parse_memory_save('{"title":"T","content":"C"}')
        self.assertTrue(ok, err)
        self.assertEqual(norm["tags"], [])

    def test_tags_cleaned(self):
        ok, err, norm = parse_memory_save('{"title":"T","content":"C","tags":["a"," ","b"]}')
        self.assertTrue(ok, err)
        self.assertEqual(norm["tags"], ["a", "b"])

    def test_no_title(self):
        self.assertFalse(parse_memory_save('{"content":"C"}')[0])

    def test_no_content(self):
        self.assertFalse(parse_memory_save('{"title":"T"}')[0])

    def test_bad_json(self):
        self.assertFalse(parse_memory_save("not json")[0])

    def test_not_object(self):
        self.assertFalse(parse_memory_save('["a","b"]')[0])


class TestRankNotesByQuery(unittest.TestCase):
    NOTES = [
        {"title": "Elastic dotted cols", "content": "escape quotes for dotted", "tags": ["elastic", "sql"], "updated_at": "2026-01-01"},
        {"title": "TheHive tags", "content": "tags_N to list", "tags": ["thehive"], "updated_at": "2026-02-01"},
        {"title": "Netbox search", "content": "address field is primary", "tags": ["netbox"], "updated_at": "2026-03-01"},
    ]

    def test_relevance_match(self):
        top = rank_notes_by_query(self.NOTES, "how to query elastic dotted columns", 3)
        self.assertEqual(top[0]["title"], "Elastic dotted cols")

    def test_only_matches_returned(self):
        top = rank_notes_by_query(self.NOTES, "elastic", 5)
        self.assertEqual([n["title"] for n in top], ["Elastic dotted cols"])

    def test_empty_query_returns_freshest(self):
        top = rank_notes_by_query(self.NOTES, "", 2)
        self.assertEqual([n["title"] for n in top], ["Netbox search", "TheHive tags"])

    def test_no_match_returns_freshest(self):
        top = rank_notes_by_query(self.NOTES, "zzz nonexistent", 1)
        self.assertEqual(top[0]["title"], "Netbox search")

    def test_limit_respected(self):
        self.assertLessEqual(len(rank_notes_by_query(self.NOTES, "", 2)), 2)


if __name__ == "__main__":
    unittest.main()
