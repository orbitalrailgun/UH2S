"""Тесты парсера скриптов Harvester (app/engine.py).

Покрывают все команды DSL и краевые случаи, включая баги из PARSING.md:
  P1 — запятые в значениях параметров (списки/SQL),
  P2 — символ '|' внутри значений (SQLite '||'),
  P3 — разбор 'AS' в APPLY,
  P5 — удаление комментариев.

Запуск:  python3 -m unittest discover -s tests -t .
Эти тесты не требуют БД и сторонних пакетов (command_parser работает офлайн).
"""
import unittest

from app.engine import command_parser, get_variable_type, execute_calc

try:
    from app.engine import split_top_level
except ImportError:  # появится после внедрения Fix #1
    split_top_level = None

CS = {"app_name": "test", "app_version": "0", "username": "tester"}


def parse(text):
    return command_parser(text, CS)


def one(text):
    """Распарсить скрипт из одной команды и вернуть её dict."""
    cmds = parse(text)
    assert len(cmds) == 1, f"expected 1 command, got {len(cmds)}: {cmds}"
    return cmds[0]


class TestGetVariableType(unittest.TestCase):
    def test_int(self):
        self.assertEqual(get_variable_type("5", CS)[3], ("integer", 5))

    def test_float(self):
        self.assertEqual(get_variable_type("3.14", CS)[3], ("float", 3.14))

    def test_string_quoted(self):
        self.assertEqual(get_variable_type('"hi"', CS)[3], ("string", "hi"))

    def test_bool(self):
        self.assertEqual(get_variable_type("true", CS)[3], ("boolean", True))
        self.assertEqual(get_variable_type("False", CS)[3], ("boolean", False))

    def test_list(self):
        self.assertEqual(get_variable_type("[1, 2, 3]", CS)[3], ("list", [1, 2, 3]))

    def test_dict(self):
        self.assertEqual(get_variable_type('{"a": 1, "b": 2}', CS)[3], ("dict", {"a": 1, "b": 2}))

    def test_empty(self):
        self.assertEqual(get_variable_type("", CS)[3], ("string", ""))


class TestDef(unittest.TestCase):
    def test_float(self):
        c = one("DEF 3.14 AS pi")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["variable_name"], "pi")
        self.assertEqual(c["variable_type"], "float")
        self.assertEqual(c["variable_value"], 3.14)

    def test_string_with_comma(self):
        c = one('DEF "hello, world" AS greeting')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["variable_value"], "hello, world")

    def test_list(self):
        c = one("DEF [1, 2, 3] AS nums")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["variable_value"], [1, 2, 3])

    def test_dict(self):
        c = one('DEF {"a": 1, "b": 2} AS conf')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["variable_value"], {"a": 1, "b": 2})


class TestGetBasic(unittest.TestCase):
    def test_simple(self):
        c = one('GET netbox:finder(target="1.2.3.4", fast_flag=false) AS net')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["command"], "GET")
        self.assertEqual(c["source"], "netbox")
        self.assertEqual(c["function"], "finder")
        self.assertEqual(c["data_name"], "net")
        self.assertEqual(c["parameters"], {"target": '"1.2.3.4"', "fast_flag": "false"})

    def test_no_params(self):
        c = one("GET src:func() AS out")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["parameters"], {})


class TestGetCommaInValue(unittest.TestCase):
    """P1: значения с запятыми не должны обрываться."""

    def test_sql_list(self):
        sql = "SELECT title, severity, status, date FROM alerts ORDER BY date DESC"
        c = one(f'GET sqlite3:query(queries=["{sql}"]) AS report')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["parameters"], {"queries": f'["{sql}"]'})

    def test_mixed_params(self):
        c = one('GET x:f(a=1, b="x, y", c=[1, 2, 3]) AS d')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["parameters"], {"a": "1", "b": '"x, y"', "c": "[1, 2, 3]"})

    def test_dict_value(self):
        c = one('GET x:f(filter={"_field": "status", "_value": "New"}) AS d')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["parameters"], {"filter": '{"_field": "status", "_value": "New"}'})


class TestGetPipeInValue(unittest.TestCase):
    """P2: '|' внутри значений не должен резать скрипт на команды."""

    def test_sqlite_concat(self):
        cmds = parse('GET db:query(queries=["SELECT a || b AS x FROM t"]) AS r')
        self.assertEqual(len(cmds), 1)
        self.assertTrue(cmds[0]["parsed"])
        self.assertEqual(cmds[0]["parameters"], {"queries": '["SELECT a || b AS x FROM t"]'})

    def test_normal_multicommand_still_splits(self):
        cmds = parse("DEF 1 AS a\n| DEF 2 AS b\n| GET s:f() AS c")
        self.assertEqual(len(cmds), 3)
        self.assertEqual([c["command"] for c in cmds], ["DEF", "DEF", "GET"])


class TestApply(unittest.TestCase):
    def test_basic_apply(self):
        c = one('GET APPLY:mydata(col1 AS a, col2 AS b):["a"] thesrc:thefunc(p=1) AS outdata')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["apply"]["data"], "mydata")
        self.assertEqual(c["apply"]["unique"], ["a"])
        self.assertEqual(
            c["apply"]["columns"],
            [{"column": "col1", "as": "a"}, {"column": "col2", "as": "b"}],
        )
        self.assertEqual(c["source"], "thesrc")
        self.assertEqual(c["function"], "thefunc")
        self.assertEqual(c["data_name"], "outdata")
        self.assertEqual(c["parameters"], {"p": "1"})

    def test_apply_column_with_as_substring(self):
        """P3: имя колонки с подстрокой 'AS' (например, GAS) не должно ломать разбор."""
        c = one("GET APPLY:d(GAS AS g):[] s:f() AS o")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["apply"]["columns"], [{"column": "GAS", "as": "g"}])


class TestCalcParse(unittest.TestCase):
    def test_calc_basic(self):
        c = one("CALC(a, b, PLUS) AS c")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["calc_x"], "a")
        self.assertEqual(c["calc_y"], "b")
        self.assertEqual(c["operation"], "PLUS")
        self.assertIsNone(c["calc_optional"])
        self.assertEqual(c["result_name"], "c")

    def test_calc_with_optional(self):
        c = one('CALC(ts, "%Y-%m-%d %H:%M:%S", DATETIME_FORMAT, "%d.%m.%Y") AS d')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["calc_x"], "ts")
        self.assertEqual(c["calc_y"], '"%Y-%m-%d %H:%M:%S"')
        self.assertEqual(c["operation"], "DATETIME_FORMAT")
        self.assertEqual(c["calc_optional"], '"%d.%m.%Y"')
        self.assertEqual(c["result_name"], "d")

    def test_calc_too_few_args(self):
        c = one("CALC(a, PLUS) AS c")
        self.assertFalse(c["parsed"])


CS_VARS = {"app_name": "test", "app_version": "0", "username": "tester"}


def calc(x, y, operation, optional=None, variables=None):
    command = {"calc_x": x, "calc_y": y, "operation": operation, "calc_optional": optional}
    return execute_calc(command, variables or {}, CS_VARS)


class TestCalcExec(unittest.TestCase):
    def test_plus(self):
        self.assertEqual(calc("2", "3", "PLUS")[3], 5)

    def test_minus_vars(self):
        self.assertEqual(calc("a", "b", "MINUS", variables={"a": 10, "b": 4})[3], 6)

    def test_mult(self):
        self.assertEqual(calc("2.5", "4", "MULT")[3], 10.0)

    def test_dev(self):
        self.assertEqual(calc("10", "4", "DEV")[3], 2.5)

    def test_dev_zero(self):
        self.assertFalse(calc("1", "0", "DEV")[0])

    def test_pow_with_y(self):
        self.assertEqual(calc("2", "10", "POW")[3], 1024)

    def test_pow_with_optional(self):
        self.assertEqual(calc("2", "0", "POW", optional="3")[3], 8)

    def test_math_non_numeric(self):
        self.assertFalse(calc('"x"', "1", "PLUS")[0])

    def test_trim(self):
        self.assertEqual(calc('"  hi  "', '""', "TRIM")[3], "hi")

    def test_concat(self):
        self.assertEqual(calc('"a"', '"b"', "CONCAT")[3], "ab")

    def test_concat_sep(self):
        self.assertEqual(calc('"a"', '"b"', "CONCAT", optional='"-"')[3], "a-b")

    def test_split(self):
        self.assertEqual(calc('"a,b,c"', '","', "SPLIT")[3], ["a", "b", "c"])

    def test_re_search_true(self):
        self.assertTrue(calc('"abc123"', '"\\d+"', "RE_SEARCH")[3])

    def test_re_substring(self):
        self.assertEqual(calc('"abc123def"', '"\\d+"', "RE_SUBSTRING")[3], "123")

    def test_datetime_format(self):
        r = calc('"2026-06-23 10:30:00"', '"%Y-%m-%d %H:%M:%S"', "DATETIME_FORMAT", optional='"%d.%m.%Y"')
        self.assertEqual(r[3], "23.06.2026")

    def test_datetime_to_unixtime_and_back(self):
        ts = calc('"2026-06-23 00:00:00"', '"%Y-%m-%d %H:%M:%S"', "DATETIME_TO_UNIXTIME")[3]
        self.assertIsInstance(ts, int)
        back = calc(str(ts), '"%Y-%m-%d %H:%M:%S"', "UNIXTIME_TO_DATETIME")[3]
        self.assertEqual(back, "2026-06-23 00:00:00")

    def test_unknown_op(self):
        self.assertFalse(calc("1", "2", "NOPE")[0])


class TestNotify(unittest.TestCase):
    def test_notify(self):
        c = one('NOTIFY mattermost("hello, world https://x/y")')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["notifier"], "mattermost")
        self.assertEqual(c["message"], "hello, world https://x/y")
        self.assertEqual(c["user"], "tester")


class TestComments(unittest.TestCase):
    def test_leading_comment(self):
        cmds = parse("/* header */ DEF 1 AS a")
        self.assertEqual(len(cmds), 1)
        self.assertTrue(cmds[0]["parsed"])
        self.assertEqual(cmds[0]["variable_name"], "a")

    def test_two_comments_same_line(self):
        """P5: два комментария в одной строке не должны вырезать код между ними."""
        c = one("DEF 5 /*a*/ AS /*b*/ x")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["variable_name"], "x")
        self.assertEqual(c["variable_value"], 5)


class TestPrint(unittest.TestCase):
    def test_print_text(self):
        c = one('PRINT("Отчёт по алертам")')
        self.assertEqual(c["command"], "PRINT")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["print_arg"], '"Отчёт по алертам"')

    def test_print_variable(self):
        c = one("PRINT(alerts)")
        self.assertEqual(c["command"], "PRINT")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["print_arg"], "alerts")

    def test_print_lowercase_keyword(self):
        c = one("print(x)")
        self.assertEqual(c["command"], "PRINT")
        self.assertTrue(c["parsed"])


class TestShow(unittest.TestCase):
    def test_show_table(self):
        c = one("SHOW(alerts, table)")
        self.assertEqual(c["command"], "SHOW")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["show_table"], "alerts")
        self.assertEqual(c["show_type"], "table")
        self.assertEqual(c["show_params"], "")

    def test_show_matplotlib_with_params(self):
        c = one('SHOW(alerts, matplotlib, {"kind": "bar", "x": "severity", "y": "count"})')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["show_table"], "alerts")
        self.assertEqual(c["show_type"], "matplotlib")
        self.assertEqual(c["show_params"], '{"kind": "bar", "x": "severity", "y": "count"}')

    def test_show_missing_type(self):
        c = one("SHOW(alerts)")
        self.assertFalse(c["parsed"])


class TestSave(unittest.TestCase):
    def test_save_xlsx(self):
        c = one("SAVE(alerts, xlsx)")
        self.assertEqual(c["command"], "SAVE")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["save_tables"], ["alerts"])
        self.assertEqual(c["save_format"], "xlsx")
        self.assertIsNone(c["save_filename"])

    def test_save_csv_in_zip(self):
        c = one("SAVE(by_sev, csv_in_zip)")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["save_format"], "csv_in_zip")

    def test_save_quoted_format(self):
        c = one('SAVE(alerts, "json_in_zip")')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["save_format"], "json_in_zip")

    def test_save_missing_format(self):
        c = one("SAVE(alerts)")
        self.assertFalse(c["parsed"])

    def test_save_group(self):
        c = one("SAVE([alerts, by_sev, hosts], xlsx)")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["save_tables"], ["alerts", "by_sev", "hosts"])
        self.assertEqual(c["save_format"], "xlsx")

    def test_save_with_as(self):
        c = one("SAVE(alerts, xlsx) AS report")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["save_tables"], ["alerts"])
        self.assertEqual(c["save_filename"], "report")

    def test_save_group_with_as(self):
        c = one('SAVE([a, b], csv_in_zip) AS "my data"')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["save_tables"], ["a", "b"])
        self.assertEqual(c["save_format"], "csv_in_zip")
        self.assertEqual(c["save_filename"], "my data")

    def test_save_empty_list(self):
        c = one("SAVE([], xlsx)")
        self.assertFalse(c["parsed"])


class TestScriptCallParse(unittest.TestCase):
    def test_script_call(self):
        c = one('GET script:my_report(target="1.2.3.4", limit=1000) AS result')
        self.assertTrue(c["parsed"])
        self.assertEqual(c["source"], "script")        # зарезервированное слово
        self.assertEqual(c["function"], "my_report")    # имя SCRIPT-объекта
        self.assertEqual(c["data_name"], "result")
        self.assertEqual(c["parameters"], {"target": '"1.2.3.4"', "limit": "1000"})


class TestInjectedVariables(unittest.TestCase):
    def test_injected_overrides_def(self):
        from engine import commands_executor
        cmds = command_parser("DEF 1 AS x | CALC(x, 2, PLUS) AS y", CS)
        result = commands_executor(cmds, {**CS, "roles": [], "processes": 1}, {"x": 10})
        self.assertTrue(result[0])
        variables = result[3][0]
        self.assertEqual(variables["x"], 10)   # переданный параметр перекрывает DEF 1
        self.assertEqual(variables["y"], 12)   # CALC(x=10, 2, PLUS)


class TestApplyExec(unittest.TestCase):
    """Исполнение APPLY (построчный fan-out) на фейковом источнике, без БД."""

    def _apply_command(self, columns, parameters, unique, function_object):
        return {
            "command": "GET",
            "apply": {"data": "src", "columns": columns, "unique": unique},
            "parameters": parameters,
            "function_object": function_object,
            "source_object": {"json": {}},
            "data_name": "out",
        }

    def test_fanout_and_dedup(self):
        from app.engine import run_apply_command
        fake = lambda params, sj, dm, cs: (True, "1", "fake", [{"resolved": params["target"]}])
        cmd = self._apply_command([{"column": "ip", "as": "x"}], {"target": "%(x)s"}, ["resolved"], fake)
        data_map = {"src": [{"ip": "1.1.1.1"}, {"ip": "2.2.2.2"}, {"ip": "1.1.1.1"}]}
        res = run_apply_command(cmd, data_map, CS)
        self.assertTrue(res[0])
        self.assertEqual(res[3], [
            {"resolved": "1.1.1.1", "applied_x": "1.1.1.1"},
            {"resolved": "2.2.2.2", "applied_x": "2.2.2.2"},
        ])

    def test_multi_column(self):
        from app.engine import run_apply_command
        fake = lambda params, sj, dm, cs: (True, "1", "fake", [{"r": params["q"]}])
        cmd = self._apply_command(
            [{"column": "ip", "as": "x"}, {"column": "name", "as": "y"}],
            {"q": "%(x)s-%(y)s"}, [], fake)
        data_map = {"src": [{"ip": "1", "name": "a"}, {"ip": "2", "name": "b"}]}
        res = run_apply_command(cmd, data_map, CS)
        self.assertTrue(res[0])
        self.assertEqual(res[3], [
            {"r": "1-a", "applied_x": "1", "applied_y": "a"},
            {"r": "2-b", "applied_x": "2", "applied_y": "b"},
        ])

    def test_empty_data(self):
        from app.engine import run_apply_command
        fake = lambda params, sj, dm, cs: (True, "1", "fake", [{"r": 1}])
        cmd = self._apply_command([{"column": "ip", "as": "x"}], {"q": "%(x)s"}, [], fake)
        res = run_apply_command(cmd, {"src": []}, CS)
        self.assertTrue(res[0])
        self.assertEqual(res[3], [])

    def test_missing_column(self):
        from app.engine import run_apply_command
        fake = lambda params, sj, dm, cs: (True, "1", "fake", [{"r": 1}])
        cmd = self._apply_command([{"column": "absent", "as": "x"}], {"q": "%(x)s"}, [], fake)
        res = run_apply_command(cmd, {"src": [{"ip": "1"}]}, CS)
        self.assertFalse(res[0])

    def test_source_error_aborts(self):
        from app.engine import run_apply_command
        fake = lambda params, sj, dm, cs: (False, "boom", "fake", [])
        cmd = self._apply_command([{"column": "ip", "as": "x"}], {"q": "%(x)s"}, [], fake)
        res = run_apply_command(cmd, {"src": [{"ip": "1"}]}, CS)
        self.assertFalse(res[0])


class TestApplyScriptExec(unittest.TestCase):
    """APPLY поверх вызова скрипта: под-скрипт прогоняется на каждую строку (офлайн, без БД)."""

    def test_apply_over_script(self):
        from engine import run_apply_script_command
        # под-скрипт: удваивает строковый параметр target через CONCAT, возвращает переменную
        body = 'DEF "z" AS target | CALC(target, target, CONCAT) AS doubled'
        sub_commands = command_parser(body, CS)
        command = {
            "command": "GET",
            "apply": {"data": "src", "columns": [{"column": "v", "as": "x"}], "unique": []},
            "parameters": {"target": "%(x)s"},
            "script_object": {"name": "s", "roles": ["default"], "json": {"script": body, "return": "doubled"}},
            "sub_commands": sub_commands,
            "data_name": "out",
        }
        data_map = {"src": [{"v": "ab"}, {"v": "cd"}]}
        res = run_apply_script_command(command, data_map, {**CS, "roles": [], "processes": 1})
        self.assertTrue(res[0], res[1])
        self.assertEqual(res[3], [
            {"value": "abab", "applied_x": "ab"},
            {"value": "cdcd", "applied_x": "cd"},
        ])


class TestJiraUnfold(unittest.TestCase):
    def test_unfold_issue(self):
        from app.sources.jira_sm import _unfold_issue
        issue = {
            "id": "10001", "key": "SD-123",
            "fields": {
                "summary": "Disk full",
                "status": {"name": "Open", "id": "1"},
                "assignee": {"displayName": "Ivan"},
                "labels": ["db", "prod"],
            },
        }
        flat = _unfold_issue(issue)
        self.assertEqual(flat["id"], "10001")
        self.assertEqual(flat["key"], "SD-123")
        self.assertEqual(flat["summary"], "Disk full")          # fields поднят наверх
        self.assertEqual(flat["status_name"], "Open")           # вложенный объект уплощён
        self.assertEqual(flat["assignee_displayName"], "Ivan")
        self.assertEqual(flat["labels_0"], "db")                # список -> индексы
        self.assertNotIn("fields", flat)

    def test_unfold_collapses_collections(self):
        from app.sources.jira_sm import _unfold_issue
        issue = {"key": "SD-1", "fields": {"summary": "x",
                 "comment": {"total": 3, "comments": [{"body": "a"}, {"body": "b"}, {"body": "c"}]},
                 "worklog": {"total": 2, "worklogs": [{"id": "1"}, {"id": "2"}]},
                 "attachment": [{"filename": "a.txt"}, {"filename": "b.txt"}],
                 "issuelinks": [{"id": "1"}]}}
        flat = _unfold_issue(issue)
        self.assertEqual(flat["comment_count"], 3)
        self.assertEqual(flat["worklog_count"], 2)
        self.assertEqual(flat["attachment_count"], 2)
        self.assertEqual(flat["issuelinks_count"], 1)
        for noisy in ("comment_comments_0_body", "attachment_0_filename", "issuelinks_0_id", "worklog_worklogs_0_id"):
            self.assertNotIn(noisy, flat)

    def test_unfold_resolves_customfield_names(self):
        from app.sources.jira_sm import _unfold_issue
        names = {"customfield_10010": "Sprint", "customfield_10020": "Severity"}
        issue = {"key": "SD-1", "names": names, "fields": {
            "summary": "x", "customfield_10010": "Sprint 42",
            "customfield_10020": {"value": "High"}, "customfield_99999": "z"}}
        flat = _unfold_issue(issue, names)
        self.assertEqual(flat["Sprint"], "Sprint 42")          # customfield -> человекочитаемое имя
        self.assertEqual(flat["Severity_value"], "High")       # объектное значение -> имя + значение
        self.assertEqual(flat["customfield_99999"], "z")       # без маппинга — как есть
        self.assertNotIn("customfield_10010", flat)


class TestSourcesCatalog(unittest.TestCase):
    def test_catalog_lists_required_and_optional(self):
        from app.engine import describe_sources_catalog
        catalog = describe_sources_catalog()
        self.assertIn("irp_thehive:get_alerts | обязательные: filter, limit", catalog)
        self.assertIn("jira_sm:search_issues | обязательные: jql", catalog)
        self.assertIn("netbox:search_cidr_by_ip | обязательные: target", catalog)
        # незарегистрированные/заглушечные функции не попадают
        self.assertNotIn("teleport:", catalog)

    def test_list_source_types(self):
        from app.engine import list_source_types
        types = list_source_types()
        self.assertIn("jira_sm", types)
        self.assertIn("irp_thehive", types)
        self.assertNotIn("teleport", types)   # без зарегистрированных функций

    def test_describe_source_functions(self):
        from app.engine import describe_source_functions
        text = describe_source_functions("jira_sm")
        # формат: "<function> | обязательные: <name>:<type>=<example>, ..."
        self.assertIn("search_issues | обязательные: jql:string=", text)
        self.assertIn("конфиг source-объекта", text)
        self.assertIn("не найден", describe_source_functions("does_not_exist"))

    def test_describe_source_functions_struct(self):
        from app.engine import describe_source_functions_struct
        spec = describe_source_functions_struct("sqlite3_im")
        self.assertEqual(spec["source_type"], "sqlite3_im")
        query_fn = next(f for f in spec["functions"] if f["function"] == "query")
        # параметр queries: тип list + пример из карты
        self.assertEqual(query_fn["required"]["queries"]["type"], "list")
        self.assertIsInstance(query_fn["required"]["queries"]["example"], list)
        self.assertIn("error", describe_source_functions_struct("does_not_exist"))


class TestAnalyzer(unittest.TestCase):
    def _state(self):
        return {"app_name": "test", "app_version": "0", "username": "tester",
                "main_session_id": "m", "user_session_id": "s"}

    def test_build_execution_mermaid(self):
        from app.analyzer import build_execution_mermaid
        script = ('DEF 1000 AS lim | GET jira_sm:search_issues(jql="project = SD", limit=%(lim)i) AS issues '
                  '| GET sqlite3:query(queries=["SELECT * FROM issues"]) AS agg | PRINT(agg) | SAVE(agg, xlsx) AS report')
        graph = build_execution_mermaid(script, self._state())
        self.assertIn("flowchart TD", graph)
        self.assertIn("DEF lim = 1000", graph)
        self.assertIn("issues ⟵ jira_sm:search_issues", graph)
        self.assertIn("agg ⟵ sqlite3:query", graph)
        self.assertIn("PRINT agg", graph)
        self.assertIn("SAVE", graph)
        # рёбра подписаны именем передаваемых данных: lim -> search_issues, issues -> agg (SQL FROM)
        self.assertIn('-->|"lim"|', graph)
        self.assertIn('-->|"issues"|', graph)
        self.assertIn("classDef", graph)

    def test_build_execution_mermaid_empty(self):
        from app.analyzer import build_execution_mermaid
        graph = build_execution_mermaid("", self._state())
        self.assertIn("flowchart TD", graph)


class TestLlmContext(unittest.TestCase):
    def test_context_window(self):
        from app.llm import llm_context_window
        self.assertEqual(llm_context_window({"context_window": 16384}), 16384)
        self.assertEqual(llm_context_window({}), 8192)            # дефолт
        self.assertEqual(llm_context_window({"context_window": "x"}), 8192)
        self.assertEqual(llm_context_window({"context_window": 0}), 8192)

    def test_estimate_and_truncate(self):
        from app.llm import llm_estimate_tokens, llm_truncate_to_tokens
        self.assertEqual(llm_estimate_tokens("a" * 300), 100)     # ~3 симв/токен
        self.assertEqual(llm_estimate_tokens(""), 0)
        self.assertTrue(llm_truncate_to_tokens("x" * 100, 10).endswith("…[truncated]"))
        self.assertEqual(llm_truncate_to_tokens("short", 1000), "short")

    def test_build_messages_budget(self):
        from app.llm import llm_build_messages
        conversation = [
            {"role": "user", "content": "a" * 6000},      # ~2000 токенов — должно отброситься
            {"role": "assistant", "content": "b" * 60},
            {"role": "user", "content": "c" * 60},
        ]
        messages = llm_build_messages("SYS", conversation, 2048)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["content"], "c" * 60)        # свежее сохранено
        self.assertFalse(any(m["content"] == "a" * 6000 for m in messages))  # старое огромное отброшено


class TestSequentialOutput(unittest.TestCase):
    def test_get_then_print_then_show(self):
        script = (
            'GET sqlite3:query(queries=["SELECT a, b FROM t"]) AS report\n'
            '| PRINT("Результат:")\n'
            '| PRINT(report)\n'
            '| SHOW(report, table)'
        )
        cmds = parse(script)
        self.assertEqual([c["command"] for c in cmds], ["GET", "PRINT", "PRINT", "SHOW"])
        self.assertTrue(all(c["parsed"] for c in cmds))


@unittest.skipIf(split_top_level is None, "split_top_level ещё не внедрён (Fix #1)")
class TestSplitTopLevel(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(split_top_level("a,b,c", ","), ["a", "b", "c"])

    def test_brackets(self):
        self.assertEqual(split_top_level("a,[b,c],d", ","), ["a", "[b,c]", "d"])

    def test_braces(self):
        self.assertEqual(split_top_level('a,{"x":1,"y":2},b', ","), ["a", '{"x":1,"y":2}', "b"])

    def test_quotes(self):
        self.assertEqual(split_top_level('a,"b,c",d', ","), ["a", '"b,c"', "d"])

    def test_pipe_separator_in_brackets(self):
        self.assertEqual(split_top_level('["a || b"]|x', "|"), ['["a || b"]', "x"])

    def test_no_separator(self):
        self.assertEqual(split_top_level("abc", ","), ["abc"])


if __name__ == "__main__":
    unittest.main()
