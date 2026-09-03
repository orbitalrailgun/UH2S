"""Офлайн-тесты jira_sm:get_cmdb_object — один объект CMDB по id или ключу.

Учитываются формы реального Insight: объект может прийти С блоком attributes и БЕЗ него (тогда
атрибуты догружаются вторым запросом), а по ключу вместо числового id часть версий объект не отдаёт —
тогда работает фолбэк на поиск по AQL `objectKey = "..."`."""
import sys
import types
import unittest

from app.sources.jira_sm import _cmdb_path_identifier, execute_jira_get_cmdb_object

CS = {"app_name": "t", "app_version": "0", "username": "u"}


def _attr(attr_id, value):
    return {"id": attr_id * 10, "objectTypeAttributeId": attr_id,
            "objectAttributeValues": [{"value": value, "displayValue": value, "referencedType": False}]}


OBJECT_WITH_ATTRIBUTES = {
    "id": 5762496, "objectKey": "HAM-5762496", "label": "Apple-603",
    "objectType": {"id": 334, "name": "Laptop", "objectSchemaId": 1},
    "created": "2023-01-31T14:42:02.383Z", "updated": "2026-08-04T15:06:20.559Z", "archived": False,
    "attributes": [_attr(2539, "HAM-5762496"), _attr(2540, "Apple-603"), _attr(2547, "VL2NPN7G4J")],
}
OBJECT_WITHOUT_ATTRIBUTES = {key: value for key, value in OBJECT_WITH_ATTRIBUTES.items() if key != "attributes"}
ATTRIBUTE_NAMES = [{"id": 2539, "name": "Key"}, {"id": 2540, "name": "Name"},
                   {"id": 2547, "name": "Serial Number"}]


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or "response body"
        self.headers = {}
        self.reason = ""
        self.url = "https://jira.example.ru"

    def json(self):
        return self._payload


class TestPathIdentifier(unittest.TestCase):
    def test_valid_ids_and_keys(self):
        self.assertEqual(_cmdb_path_identifier("5762496"), (True, "5762496"))
        self.assertEqual(_cmdb_path_identifier(" HAM-2727707 "), (True, "HAM-2727707"))

    def test_path_traversal_and_spaces_rejected(self):
        for value in ("../../secure/admin", "HAM 1", "a/b", "", None, "?x=1"):
            ok, message = _cmdb_path_identifier(value)
            self.assertFalse(ok, value)
            self.assertIn("некорректный идентификатор", message)


class TestGetCmdbObject(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("requests")
        self.source = {"url": "https://jira.example.ru/", "key": {"value": "token"},
                       "verify": False, "timeout": 5, "retry_backoff_seconds": 0}
        self.calls = []

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = self._saved

    def _install(self, route):
        """route(url, params) -> FakeResponse; запоминаем последовательность запросов."""
        calls = self.calls

        def get(url, headers=None, params=None, verify=None, timeout=None, **kwargs):
            calls.append((url, dict(params or {})))
            return route(url, params or {})

        module = types.ModuleType("requests")
        module.get = get
        module.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError, Timeout=TimeoutError)
        sys.modules["requests"] = module

    def _run(self, parameters):
        return execute_jira_get_cmdb_object(parameters, self.source, {}, CS)

    def test_object_with_attributes_becomes_named_row(self):
        def route(url, params):
            if url.endswith("/object/5762496"):
                return FakeResponse(OBJECT_WITH_ATTRIBUTES)
            if url.endswith("/objecttype/334/attributes"):
                return FakeResponse(ATTRIBUTE_NAMES)
            return FakeResponse({}, 404)

        self._install(route)
        ok, info, func, rows = self._run({"object_id": "5762496"})
        self.assertTrue(ok, info)
        self.assertEqual((info, func), ("1", "execute_jira_get_cmdb_object"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["objectKey"], "HAM-5762496")
        self.assertEqual(row["objectType"], "Laptop")
        self.assertEqual(row["Serial Number"], "VL2NPN7G4J")     # имя атрибута, не attributes_2_...
        self.assertEqual(row["Name"], "Apple-603")
        self.assertFalse(any(key.startswith("attributes_") for key in row))

    def test_attributes_are_fetched_when_absent(self):
        def route(url, params):
            if url.endswith("/object/5762496"):
                return FakeResponse(dict(OBJECT_WITHOUT_ATTRIBUTES))
            if url.endswith("/object/5762496/attributes"):
                return FakeResponse(OBJECT_WITH_ATTRIBUTES["attributes"])
            if url.endswith("/objecttype/334/attributes"):
                return FakeResponse(ATTRIBUTE_NAMES)
            return FakeResponse({}, 404)

        self._install(route)
        ok, info, _func, rows = self._run({"object_id": "5762496"})
        self.assertTrue(ok, info)
        self.assertEqual(rows[0]["Key"], "HAM-5762496")
        self.assertTrue(any(url.endswith("/object/5762496/attributes") for url, _params in self.calls))

    def test_key_falls_back_to_aql_search(self):
        def route(url, params):
            if url.endswith("/object/HAM-5762496"):
                return FakeResponse({"errorMessages": ["not found"]}, 404)
            if url.endswith("/iql/objects"):
                return FakeResponse({"objectEntries": [OBJECT_WITH_ATTRIBUTES],
                                     "objectTypeAttributes": ATTRIBUTE_NAMES})
            return FakeResponse({}, 404)

        self._install(route)
        ok, info, _func, rows = self._run({"object_id": "HAM-5762496"})
        self.assertTrue(ok, info)
        self.assertEqual(rows[0]["objectKey"], "HAM-5762496")
        iql_calls = [params for url, params in self.calls if url.endswith("/iql/objects")]
        self.assertEqual(iql_calls[0]["iql"], 'objectKey = "HAM-5762496"')

    def test_numeric_id_does_not_use_the_fallback(self):
        def route(url, params):
            if url.endswith("/object/999"):
                return FakeResponse({}, 404)
            return FakeResponse({"objectEntries": [OBJECT_WITH_ATTRIBUTES]})

        self._install(route)
        ok, info, _func, rows = self._run({"object_id": "999"})
        self.assertTrue(ok, info)          # объекта нет — это не ошибка шага
        self.assertEqual((rows, info), ([], "0"))
        self.assertFalse(any(url.endswith("/iql/objects") for url, _params in self.calls))

    def test_shapes(self):
        def route(url, params):
            if url.endswith("/object/5762496"):
                return FakeResponse(OBJECT_WITH_ATTRIBUTES)
            if url.endswith("/objecttype/334/attributes"):
                return FakeResponse(ATTRIBUTE_NAMES)
            return FakeResponse({}, 404)

        self._install(route)
        _ok, _info, _func, raw_rows = self._run({"object_id": "5762496", "shape": "raw"})
        self.assertEqual(raw_rows[0]["objectType"]["name"], "Laptop")
        _ok, _info, _func, flat_rows = self._run({"object_id": "5762496", "shape": "flat"})
        self.assertEqual(flat_rows[0]["attributes_0_objectAttributeValues_0_value"], "HAM-5762496")
        _ok, _info, _func, long_rows = self._run({"object_id": "5762496", "shape": "long"})
        self.assertEqual({row["attribute"] for row in long_rows}, {"Key", "Name", "Serial Number"})

    def test_resolve_names_false_skips_extra_request(self):
        def route(url, params):
            if url.endswith("/object/5762496"):
                return FakeResponse(OBJECT_WITH_ATTRIBUTES)
            return FakeResponse({}, 404)

        self._install(route)
        ok, info, _func, rows = self._run({"object_id": "5762496", "resolve_names": False})
        self.assertTrue(ok, info)
        self.assertEqual(rows[0]["attr_2547"], "VL2NPN7G4J")     # имён нет -> attr_<id>
        self.assertFalse(any("objecttype" in url for url, _params in self.calls))

    def test_invalid_object_id_is_rejected_before_any_request(self):
        self._install(lambda url, params: FakeResponse({}, 200))
        ok, info, _func, rows = self._run({"object_id": "../../secure/admin"})
        self.assertFalse(ok)
        self.assertIn("некорректный идентификатор", info)
        self.assertEqual((rows, self.calls), ([], []))

    def test_missing_object_id(self):
        self._install(lambda url, params: FakeResponse({}, 200))
        ok, info, _func, _rows = self._run({})
        self.assertFalse(ok)
        self.assertIn("get_cmdb_object", info)

    def test_server_error_is_reported(self):
        self._install(lambda url, params: FakeResponse({}, 500, "internal error"))
        ok, info, _func, rows = self._run({"object_id": "5762496"})
        self.assertFalse(ok)
        self.assertIn("http 500", info)
        self.assertIn("internal error", info)
        self.assertEqual(rows, [])

    def test_transient_status_is_retried(self):
        responses = [FakeResponse({}, 502), FakeResponse(OBJECT_WITH_ATTRIBUTES)]
        state = {"n": 0}

        def route(url, params):
            if url.endswith("/object/5762496"):
                state["n"] += 1
                return responses[min(state["n"] - 1, 1)]
            return FakeResponse(ATTRIBUTE_NAMES)

        self._install(route)
        ok, info, _func, rows = self._run({"object_id": "5762496"})
        self.assertTrue(ok, info)
        self.assertEqual(state["n"], 2)          # 502 -> повтор -> успех
        self.assertEqual(len(rows), 1)

    def test_proxies_from_source_are_used(self):
        captured = {}

        def get(url, headers=None, params=None, verify=None, timeout=None, **kwargs):
            captured.update(kwargs)
            return FakeResponse(OBJECT_WITH_ATTRIBUTES)

        module = types.ModuleType("requests")
        module.get = get
        module.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError, Timeout=TimeoutError)
        sys.modules["requests"] = module
        source = dict(self.source, proxies={"http": "", "https": ""})
        execute_jira_get_cmdb_object({"object_id": "5762496"}, source, {}, CS)
        self.assertEqual(captured.get("proxies"), {"http": "", "https": ""})


class TestRegistry(unittest.TestCase):
    def test_function_is_registered(self):
        from app.engine import ENGINE_SOURCES_AND_FUNCTIONS_MAP as sources_map
        spec = sources_map["jira_sm"]["functions"]["get_cmdb_object"]
        self.assertIn("object_id", spec["required"])
        for name in ("shape", "sep", "max_values", "resolve_names", "cmdb_object_path", "cmdb_path"):
            self.assertIn(name, spec["unrequired"], name)


if __name__ == "__main__":
    unittest.main()
