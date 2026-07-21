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


if __name__ == "__main__":
    unittest.main()
