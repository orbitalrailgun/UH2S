"""Офлайн-тесты источника llm (app/sources/llm_source): парсинг JSON, слияние строк, регистрация в карте.
llm_chat мокается — сеть/LLM не нужны."""
import unittest

import app.sources.llm_source as llm_source
from app.sources.llm_source import (_parse_json_object, _parse_json_array, _merge_generated,
                                    _is_transient_error, execute_llm_line_analysis, execute_llm_data_analysis)

STATE = {"app_name": "UH2S", "app_version": "test", "username": "tester", "processes": 4}


class TestJsonParsers(unittest.TestCase):
    def test_object_fenced(self):
        self.assertEqual(_parse_json_object("тут:\n```json\n{\"a\":1}\n```"), {"a": 1})

    def test_object_dirty(self):
        self.assertEqual(_parse_json_object('бла {"v":"ok"} бла'), {"v": "ok"})

    def test_object_bad(self):
        self.assertIsNone(_parse_json_object("нет json"))

    def test_array_plain(self):
        self.assertEqual(_parse_json_array('[{"a":1},{"a":2}]'), [{"a": 1}, {"a": 2}])

    def test_array_wrapped_in_results(self):
        self.assertEqual(_parse_json_array('{"results":[{"x":1}]}'), [{"x": 1}])

    def test_array_bad(self):
        self.assertIsNone(_parse_json_array("nope"))


class TestMergeGenerated(unittest.TestCase):
    def test_new_columns_added(self):
        self.assertEqual(_merge_generated({"ip": "1.1.1.1"}, {"verdict": "benign", "accuracy": 0.9}),
                         {"ip": "1.1.1.1", "verdict": "benign", "accuracy": 0.9})

    def test_collision_prefixed(self):
        merged = _merge_generated({"ip": "1.1.1.1", "verdict": "orig"}, {"verdict": "benign"})
        self.assertEqual(merged["verdict"], "orig")
        self.assertEqual(merged["llm_verdict"], "benign")


class TestLineAnalysis(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(__import__("app.llm", fromlist=["llm_chat"]), "llm_chat")

    def tearDown(self):
        import app.llm
        app.llm.llm_chat = self._orig

    def _patch_llm(self, fn):
        import app.llm
        app.llm.llm_chat = fn

    def test_per_row_columns_added(self):
        # мок: модель возвращает verdict/accuracy на основе строки
        def fake_chat(llm_json, messages, current_state):
            return True, '{"verdict":"benign","accuracy":0.8}', {"prompt_tokens": 1, "completion_tokens": 1}
        self._patch_llm(fake_chat)
        data_map = {"alerts": [{"ip": "1.1.1.1"}, {"ip": "2.2.2.2"}]}
        ok, msg, _fn, rows = execute_llm_line_analysis(
            {"data": "alerts", "instructions": "оцени"}, {}, data_map, STATE)
        self.assertTrue(ok, msg)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["verdict"] == "benign" and r["accuracy"] == 0.8 for r in rows))
        # исходные поля сохранены и порядок строк сохранён
        self.assertEqual([r["ip"] for r in rows], ["1.1.1.1", "2.2.2.2"])

    def test_row_error_does_not_crash(self):
        def failing_chat(llm_json, messages, current_state):
            return False, "boom", {"prompt_tokens": 0, "completion_tokens": 0}
        self._patch_llm(failing_chat)
        data_map = {"alerts": [{"ip": "1.1.1.1"}]}
        ok, _msg, _fn, rows = execute_llm_line_analysis(
            {"data": "alerts", "instructions": "оцени"}, {}, data_map, STATE)
        self.assertTrue(ok)
        self.assertIn("llm_error", rows[0])

    def test_missing_table(self):
        ok, msg, _fn, rows = execute_llm_line_analysis(
            {"data": "nope", "instructions": "x"}, {}, {"alerts": []}, STATE)
        self.assertFalse(ok)
        self.assertEqual(rows, [])

    def test_empty_table(self):
        ok, msg, _fn, rows = execute_llm_line_analysis(
            {"data": "alerts", "instructions": "x"}, {}, {"alerts": []}, STATE)
        self.assertTrue(ok)
        self.assertEqual(rows, [])


class TestDataAnalysis(unittest.TestCase):
    def setUp(self):
        import app.llm
        self._orig = app.llm.llm_chat

    def tearDown(self):
        import app.llm
        app.llm.llm_chat = self._orig

    def test_returns_array(self):
        import app.llm
        app.llm.llm_chat = lambda j, m, s: (True, '[{"group":"a","count":2}]', {"prompt_tokens": 1, "completion_tokens": 1})
        ok, msg, _fn, rows = execute_llm_data_analysis(
            {"data": "alerts", "instructions": "сгруппируй"}, {}, {"alerts": [{"g": "a"}, {"g": "a"}]}, STATE)
        self.assertTrue(ok, msg)
        self.assertEqual(rows, [{"group": "a", "count": 2}])

    def test_unparseable_fails(self):
        import app.llm
        app.llm.llm_chat = lambda j, m, s: (True, "не json", {"prompt_tokens": 1, "completion_tokens": 1})
        ok, _msg, _fn, rows = execute_llm_data_analysis(
            {"data": "alerts", "instructions": "x"}, {}, {"alerts": [{"g": "a"}]}, STATE)
        self.assertFalse(ok)


class TestRetry(unittest.TestCase):
    def setUp(self):
        import app.llm
        self._orig = app.llm.llm_chat

    def tearDown(self):
        import app.llm
        app.llm.llm_chat = self._orig

    def test_transient_classification(self):
        self.assertTrue(_is_transient_error("... Read timed out."))
        self.assertTrue(_is_transient_error("openai chat http 503: unavailable"))
        self.assertTrue(_is_transient_error("Too Many Requests 429"))
        self.assertFalse(_is_transient_error("openai chat http 400: bad request"))
        self.assertFalse(_is_transient_error("openai chat http 401: unauthorized"))

    def test_recovers_after_transient_timeouts(self):
        import app.llm
        calls = {"n": 0}

        def flaky(llm_json, messages, current_state):
            calls["n"] += 1
            if calls["n"] < 3:
                return False, "llm chat fail: Read timed out.", {}
            return True, '{"verdict":"benign","accuracy":0.7}', {"prompt_tokens": 1, "completion_tokens": 1}

        app.llm.llm_chat = flaky
        ok, _msg, _fn, rows = execute_llm_line_analysis(
            {"data": "t", "instructions": "x"}, {"max_retries": 3, "retry_backoff_seconds": 0},
            {"t": [{"ip": "1.1.1.1"}]}, dict(STATE))
        self.assertTrue(ok)
        self.assertEqual(rows[0]["verdict"], "benign")
        self.assertEqual(calls["n"], 3)

    def test_non_transient_not_retried(self):
        import app.llm
        calls = {"n": 0}

        def bad_request(llm_json, messages, current_state):
            calls["n"] += 1
            return False, "openai chat http 400: bad request", {}

        app.llm.llm_chat = bad_request
        ok, _msg, _fn, rows = execute_llm_line_analysis(
            {"data": "t", "instructions": "x"}, {"max_retries": 3, "retry_backoff_seconds": 0},
            {"t": [{"ip": "1.1.1.1"}]}, dict(STATE))
        self.assertTrue(ok)  # прогон не падает
        self.assertIn("llm_error", rows[0])
        self.assertEqual(calls["n"], 1)  # 400 не повторяется


class TestLlmRegisteredAndOllamaRemoved(unittest.TestCase):
    def test_engine_map(self):
        from app.engine import ENGINE_SOURCES_AND_FUNCTIONS_MAP as M
        self.assertIn("llm", M)
        self.assertIn("line_analysis", M["llm"]["functions"])
        self.assertIn("data_analysis", M["llm"]["functions"])
        self.assertNotIn("ollama", M)
        self.assertNotIn("llama", M)


if __name__ == "__main__":
    unittest.main()
