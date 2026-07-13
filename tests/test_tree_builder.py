"""Офлайн-тесты построителя дерева (app/tree_builder) для SHOW ... tree."""
import unittest

from app.tree_builder import build_tree, tree_to_text


def _ids(nodes):
    return [n["id"] for n in nodes]


class TestBuildTree(unittest.TestCase):
    def test_basic_hierarchy(self):
        rows = [
            {"pid": "1", "ppid": "", "name": "init"},
            {"pid": "100", "ppid": "1", "name": "bash"},
            {"pid": "200", "ppid": "100", "name": "python"},
        ]
        roots, meta = build_tree(rows, "ppid", "pid", title="name")
        self.assertEqual(_ids(roots), ["1"])
        self.assertEqual(roots[0]["label"], "init")
        self.assertEqual(_ids(roots[0]["children"]), ["100"])
        self.assertEqual(_ids(roots[0]["children"][0]["children"]), ["200"])
        self.assertEqual(meta["nodes"], 3)
        self.assertEqual(meta["roots"], 1)

    def test_title_defaults_to_id(self):
        roots, _ = build_tree([{"pid": "1", "ppid": ""}], "ppid", "pid")
        self.assertEqual(roots[0]["label"], "1")

    def test_description_join(self):
        rows = [{"pid": "1", "ppid": "", "user": "root", "cmd": "/sbin/init"}]
        roots, _ = build_tree(rows, "ppid", "pid", description_fields=["user", "cmd"], separator=" | ")
        self.assertEqual(roots[0]["description"], "root | /sbin/init")

    def test_description_skips_empty_fields(self):
        rows = [{"pid": "1", "ppid": "", "user": "root", "cmd": ""}]
        roots, _ = build_tree(rows, "ppid", "pid", description_fields=["user", "cmd"], separator=" | ")
        self.assertEqual(roots[0]["description"], "root")

    def test_forest_multiple_roots(self):
        rows = [{"pid": "1", "ppid": ""}, {"pid": "2", "ppid": ""}]
        roots, meta = build_tree(rows, "ppid", "pid")
        self.assertEqual(sorted(_ids(roots)), ["1", "2"])
        self.assertEqual(meta["roots"], 2)

    def test_missing_parent_becomes_root(self):
        rows = [{"pid": "300", "ppid": "999"}]
        roots, _ = build_tree(rows, "ppid", "pid")
        self.assertEqual(_ids(roots), ["300"])

    def test_duplicate_receive_skipped(self):
        rows = [{"pid": "1", "ppid": ""}, {"pid": "1", "ppid": ""}]
        roots, meta = build_tree(rows, "ppid", "pid")
        self.assertEqual(meta["nodes"], 1)
        self.assertEqual(meta["duplicates"], 1)

    def test_self_reference_is_root(self):
        rows = [{"pid": "5", "ppid": "5"}]
        roots, _ = build_tree(rows, "ppid", "pid")
        self.assertEqual(_ids(roots), ["5"])
        self.assertEqual(roots[0]["children"], [])

    def test_cycle_broken_no_infinite_loop(self):
        rows = [{"id": "a", "p": "b"}, {"id": "b", "p": "a"}]
        roots, meta = build_tree(rows, "p", "id")
        self.assertEqual(meta["cycles_broken"], 2)
        self.assertEqual(sorted(_ids(roots)), ["a", "b"])

    def test_skips_rows_without_id(self):
        rows = [{"pid": "", "ppid": ""}, {"pid": "1", "ppid": ""}]
        roots, meta = build_tree(rows, "ppid", "pid")
        self.assertEqual(_ids(roots), ["1"])
        self.assertEqual(meta["skipped_no_id"], 1)

    def test_numeric_values_coerced(self):
        # значения-числа приводятся к строкам, связь работает
        rows = [{"pid": 1, "ppid": None}, {"pid": 2, "ppid": 1}]
        roots, _ = build_tree(rows, "ppid", "pid")
        self.assertEqual(_ids(roots), ["1"])
        self.assertEqual(_ids(roots[0]["children"]), ["2"])


class TestTreeToText(unittest.TestCase):
    def test_indented_output(self):
        rows = [{"pid": "1", "ppid": "", "name": "init"}, {"pid": "2", "ppid": "1", "name": "child"}]
        roots, _ = build_tree(rows, "ppid", "pid", title="name")
        text = tree_to_text(roots)
        self.assertIn("- init", text)
        self.assertIn("  - child", text)


if __name__ == "__main__":
    unittest.main()
