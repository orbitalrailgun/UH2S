"""Офлайн-тест извлечения имён-выходов script-объекта (_script_output_names) — автоподсказка
поля output (return) в форме создания/редактирования объектов. Команды DSL разделяются '|'."""
import unittest

from app.interface import _script_output_names

CS = {"app_name": "t", "app_version": "0", "username": "u"}


class TestScriptOutputNames(unittest.TestCase):
    def test_def_calc_get_names(self):
        body = 'DEF 5 AS v1 | CALC(v1, 1, PLUS) AS v2 | GET s:f() AS d'
        self.assertEqual(_script_output_names(body, CS), ["d", "v1", "v2"])

    def test_empty_and_blank(self):
        self.assertEqual(_script_output_names("", CS), [])
        self.assertEqual(_script_output_names("   ", CS), [])
        self.assertEqual(_script_output_names(None, CS), [])

    def test_dedup_and_sorted(self):
        body = 'GET a:b() AS x | GET c:d() AS x | PRINT x'
        self.assertEqual(_script_output_names(body, CS), ["x"])

    def test_unparsable_commands_skipped(self):
        # мусор не должен ронять извлечение; валидные имена всё равно возвращаются
        body = 'GET s:f() AS good | !!!broken!!!'
        self.assertIn("good", _script_output_names(body, CS))


if __name__ == "__main__":
    unittest.main()
