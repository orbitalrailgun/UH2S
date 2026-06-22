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

from app.engine import command_parser, get_variable_type

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


class TestCalc(unittest.TestCase):
    def test_calc(self):
        c = one("CALC a + b AS c")
        self.assertTrue(c["parsed"])
        self.assertEqual(c["variable_name_1"], "a")
        self.assertEqual(c["operation"], "+")
        self.assertEqual(c["variable_name_2"], "b")
        self.assertEqual(c["result_name"], "c")


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
