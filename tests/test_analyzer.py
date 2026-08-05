"""Офлайн-тесты статического анализатора Harvester -> Mermaid (app/analyzer.build_execution_mermaid).
Покрывают фиксы схемы исполнения: нет ложного ребра при совпадении имени переменной с именем
функции, тёмный фон субграфа, direction TB внутри субграфа, ребро return -> вызывающий узел."""
import unittest

import app.analyzer as analyzer
from app.analyzer import build_execution_mermaid

CS = {"app_name": "t", "app_version": "0", "username": "u", "roles": ["fullmaster"]}


class TestBuildExecutionMermaid(unittest.TestCase):
    def test_no_false_edge_on_name_function_collision(self):
        # DEF ... AS query + GET duckdb_im:query(...) без %(query)s -> ребра быть НЕ должно
        body = 'DEF "q" AS query | GET duckdb_im:query(sql="select 1") AS data1 | PRINT data1'
        m = build_execution_mermaid(body, CS)
        edges = [l.strip() for l in m.splitlines() if "-->" in l]
        # единственное ребро — data1 -> PRINT; ложного query-ребра нет
        self.assertTrue(any('|"data1"|' in e for e in edges))
        self.assertFalse(any('|"query"|' in e for e in edges), edges)

    def test_real_injection_edge_kept(self):
        # реальная инъекция %(query)s -> ребро остаётся
        body = 'DEF "q" AS query | GET siem:aggs_query(q=%(query)s) AS raw_aggs | PRINT raw_aggs'
        m = build_execution_mermaid(body, CS)
        self.assertTrue(any('|"query"|' in l for l in m.splitlines()), m)

    def test_dark_cluster_theme(self):
        m = build_execution_mermaid('DEF 1 AS x', CS)
        self.assertIn("clusterBkg", m)
        self.assertIn("flowchart TD", m)

    def test_nested_script_subgraph_direction_and_return_edge(self):
        original = analyzer.get_actual_object_by_name

        def fake_get(name, types, current_state):
            sub = 'GET duckdb_im:query(sql="select 1") AS result | SHOW result, table'
            return True, "ok", "f", {"json": {"script": sub, "return": "result"}}

        analyzer.get_actual_object_by_name = fake_get
        try:
            m = build_execution_mermaid('GET script:myscript() AS code2token | SHOW code2token, table', CS)
        finally:
            analyzer.get_actual_object_by_name = original

        self.assertIn("subgraph", m)
        self.assertIn("direction TB", m)          # субграф читается сверху вниз
        self.assertIn('return: result', m)        # ребро return -> вызывающий узел

    def _with_fake_script(self, body, sub='GET aiproxy:query(user=%(user_id)s) AS activity'):
        original = analyzer.get_actual_object_by_name
        analyzer.get_actual_object_by_name = lambda name, types, cs: (
            True, "ok", "f", {"json": {"script": sub, "return": "activity"}})
        try:
            return build_execution_mermaid(body, CS)
        finally:
            analyzer.get_actual_object_by_name = original

    def test_nested_script_under_apply_is_expanded(self):
        # APPLY поверх вызова скрипта: узел помечен APPLY, тело скрипта развёрнуто в субграф,
        # есть рёбра «данные -> APPLY-узел» и «return -> вызывающий узел»
        body = ('GET script:get_owner(hostname="h") AS owner_data '
                '| GET APPLY:owner_data(account AS user_id) script:get_activity(user_id="%(user_id)s") AS aiproxy_data '
                '| SHOW (aiproxy_data, table)')
        m = self._with_fake_script(body)
        self.assertIn("APPLY owner_data(account→user_id)", m)
        self.assertIn('subgraph sg', m)
        self.assertEqual(m.count("script: "), 2)                  # оба вложенных скрипта развёрнуты
        apply_node = next(line.strip().split("[")[0] for line in m.splitlines() if "APPLY owner_data" in line)
        self.assertTrue(any('|"owner_data"|' in line and line.strip().endswith(apply_node)
                            for line in m.splitlines()), m)   # ребро owner_data -> APPLY-узел
        self.assertIn("return: activity", m)

    def test_apply_specifiers_shown_on_node(self):
        body = 'GET APPLY:src(ip AS x):["r"]:once dns:query(target=%(x)s) AS resolved'
        m = build_execution_mermaid(body, CS)
        self.assertIn("APPLY src(ip→x):once:unique[r]", m)

    def test_unparsed_command_marked_as_error(self):
        # опечатка в APPLY (нет закрывающей ]) — узел с причиной, а не мнимый source:func APPLY
        body = 'GET APPLY:src(ip AS x):[bad dns:query(target=%(x)s) AS resolved'
        m = build_execution_mermaid(body, CS)
        self.assertIn("⚠", m)
        self.assertIn("APPLY:", m)
        self.assertIn("classDef errc", m)
        self.assertNotIn("⟵ APPLY:", m)


if __name__ == "__main__":
    unittest.main()
