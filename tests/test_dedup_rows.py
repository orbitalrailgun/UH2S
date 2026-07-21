"""Офлайн-тесты in-place дедупликации строк (app.engine.dedup_rows) — замена pandas
drop_duplicates для APPLY(unique). Проверяют семантику ключей, порядок и экономию копий."""
import unittest

from app.engine import dedup_rows


class TestDedupRows(unittest.TestCase):
    def test_single_column_first_seen_order(self):
        rows = [{"a": 1, "n": "first"}, {"a": 2}, {"a": 1, "n": "dup"}, {"a": 3}]
        out = dedup_rows(rows, ["a"])
        self.assertEqual([r["a"] for r in out], [1, 2, 3])
        # сохраняется ПЕРВОЕ появление (не последнее)
        self.assertEqual(out[0]["n"], "first")

    def test_multi_column_key(self):
        rows = [{"a": 1, "b": "x"}, {"a": 1, "b": "x"}, {"a": 1, "b": "y"}, {"a": 2, "b": "x"}]
        out = dedup_rows(rows, ["a", "b"])
        self.assertEqual(out, [{"a": 1, "b": "x"}, {"a": 1, "b": "y"}, {"a": 2, "b": "x"}])

    def test_missing_column_treated_as_none(self):
        # строка без ключевой колонки -> ключ None; все такие схлопываются в одну (как NaN у pandas)
        rows = [{"a": 1}, {"b": 2}, {"a": 1}, {"c": 3}]
        out = dedup_rows(rows, ["a"])
        self.assertEqual(out, [{"a": 1}, {"b": 2}])

    def test_unhashable_values_equal_structs_collapse(self):
        # dict/list в ключевой колонке: равные структуры (разный порядок вставки) -> один ключ
        rows = [{"k": {"z": 1, "y": 2}}, {"k": {"y": 2, "z": 1}}, {"k": {"z": 9}}]
        out = dedup_rows(rows, ["k"])
        self.assertEqual(out, [{"k": {"z": 1, "y": 2}}, {"k": {"z": 9}}])

    def test_list_values(self):
        rows = [{"k": [1, 2]}, {"k": [1, 2]}, {"k": [2, 1]}]
        out = dedup_rows(rows, ["k"])
        self.assertEqual(out, [{"k": [1, 2]}, {"k": [2, 1]}])

    def test_rows_untouched_no_column_unification(self):
        # в отличие от pandas.to_dict('records') не добавляем недостающие колонки как NaN
        rows = [{"a": 1, "x": 10}, {"a": 1, "y": 20}, {"a": 2}]
        out = dedup_rows(rows, ["a"])
        self.assertEqual(out, [{"a": 1, "x": 10}, {"a": 2}])

    def test_in_place_returns_same_list(self):
        rows = [{"a": 1}, {"a": 1}, {"a": 2}]
        out = dedup_rows(rows, ["a"])
        self.assertIs(out, rows)  # тот же объект (in-place), не копия
        self.assertEqual(len(rows), 2)

    def test_empty_input(self):
        self.assertEqual(dedup_rows([], ["a"]), [])

    def test_all_unique(self):
        rows = [{"a": 1}, {"a": 2}, {"a": 3}]
        self.assertEqual(dedup_rows(rows, ["a"]), [{"a": 1}, {"a": 2}, {"a": 3}])


if __name__ == "__main__":
    unittest.main()
