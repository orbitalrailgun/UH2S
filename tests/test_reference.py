"""Офлайн-тесты справочника редактора Harvester (app/reference): каталог, сниппеты, фильтр, вставка."""
import unittest

from app.reference import (dsl_command_snippets, calc_operation_snippets, format_dsl_literal,
                           source_function_snippet, source_function_entries, script_object_entries,
                           knowledge_entries, filter_entries, insert_snippet, extract_search_token)


class TestDslSnippets(unittest.TestCase):
    def test_has_core_commands(self):
        labels = " ".join(e["label"] for e in dsl_command_snippets())
        for cmd in ("DEF", "CALC", "GET", "APPLY", "PRINT", "SHOW", "SAVE", "LOAD", "NOTIFY"):
            self.assertIn(cmd, labels)

    def test_entry_shape(self):
        for e in dsl_command_snippets():
            self.assertEqual(set(e.keys()), {"group", "label", "signature", "snippet", "doc"})

    def test_calc_operations_all_present_and_searchable(self):
        # каждая операция CALC — отдельная находимая запись (не только PLUS)
        entries = dsl_command_snippets()
        for op in ("MINUS", "MULT", "DEV", "POW", "TRIM", "CONCAT", "SPLIT",
                   "RE_SEARCH", "RE_SUBSTRING", "DATETIME_FORMAT", "UNIXTIME_TO_DATETIME", "DATETIME_TO_UNIXTIME"):
            self.assertTrue(filter_entries(entries, op), f"CALC {op} должна находиться поиском")

    def test_calc_snippets_have_operation_in_quotes(self):
        for e in calc_operation_snippets():
            op = e["label"].split(" ", 1)[1]
            self.assertIn(f'"{op}"', e["snippet"])


class TestFormatLiteral(unittest.TestCase):
    def test_types(self):
        self.assertEqual(format_dsl_literal("x"), '"x"')
        self.assertEqual(format_dsl_literal(5), "5")
        self.assertEqual(format_dsl_literal(True), "true")
        self.assertEqual(format_dsl_literal(False), "false")
        self.assertEqual(format_dsl_literal(["a", "b"]), '["a", "b"]')
        self.assertEqual(format_dsl_literal({"k": "v"}), '{"k": "v"}')


class TestSourceFunctionEntries(unittest.TestCase):
    def test_snippet_with_required_params(self):
        snippet = source_function_snippet("elastic_requests", "query",
                                          {"url": {"type": "string", "example": "https://x"},
                                           "limit": {"type": "int", "example": -1}})
        self.assertEqual(snippet, 'GET elastic_requests:query(url="https://x", limit=-1) AS data')

    def test_entries_from_describe(self):
        def fake_describe(source_type):
            return {"functions": [
                {"function": "query", "required": {"q": {"type": "string", "example": "x"}}, "optional": {}}]}
        entries = source_function_entries(["mysrc"], fake_describe)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["label"], "mysrc:query")
        self.assertIn("GET mysrc:query(", entries[0]["snippet"])


class TestScriptAndKnowledgeEntries(unittest.TestCase):
    def test_script_entry(self):
        e = script_object_entries([{"name": "triage", "params_summary": "limit", "return": "out"}])[0]
        self.assertEqual(e["snippet"], "GET script:triage() AS data")
        self.assertIn("limit", e["doc"])

    def test_knowledge_entry_is_comment(self):
        e = knowledge_entries([{"title": "T", "content": "escape quotes", "tags": ["sql"]}])[0]
        self.assertTrue(e["snippet"].startswith("/*"))
        self.assertTrue(e["snippet"].endswith("*/"))


class TestFilter(unittest.TestCase):
    ENTRIES = [
        {"group": "DSL", "label": "GET", "signature": "GET x:y()", "doc": ""},
        {"group": "source: llm", "label": "llm:line_analysis", "signature": "", "doc": "обогащение"},
    ]

    def test_empty_returns_all(self):
        self.assertEqual(len(filter_entries(self.ENTRIES, "")), 2)

    def test_match_label(self):
        self.assertEqual([e["label"] for e in filter_entries(self.ENTRIES, "line_analysis")], ["llm:line_analysis"])

    def test_case_insensitive_and_doc(self):
        self.assertEqual([e["label"] for e in filter_entries(self.ENTRIES, "ОБОГАЩ")], ["llm:line_analysis"])


class TestInsertSnippet(unittest.TestCase):
    def test_insert_into_empty(self):
        self.assertEqual(insert_snippet("", "GET a:b() AS d"), "GET a:b() AS d")
        self.assertEqual(insert_snippet("   ", "GET a:b() AS d"), "GET a:b() AS d")

    def test_append_joins_with_pipe(self):
        self.assertEqual(insert_snippet("DEF 1 AS x", "GET a:b() AS d"), "DEF 1 AS x\n| GET a:b() AS d")

    def test_append_trailing_newline(self):
        self.assertEqual(insert_snippet("DEF 1 AS x\n", "GET a:b() AS d"), "DEF 1 AS x\n| GET a:b() AS d")


class TestExtractSearchToken(unittest.TestCase):
    def test_last_word_of_last_line(self):
        self.assertEqual(extract_search_token("DEF 1 AS x\nGET duckdb_im:quer"), "duckdb_im:quer")

    def test_stops_at_non_word_chars(self):
        self.assertEqual(extract_search_token('CALC(a, b, "CON'), "CON")

    def test_empty_and_trailing_space(self):
        self.assertEqual(extract_search_token(""), "")
        self.assertEqual(extract_search_token("GET a:b() "), "")


if __name__ == "__main__":
    unittest.main()
