"""Тесты приведения объектов CMDB JSM (Insight/Assets) к таблице — офлайн.

Фикстуры повторяют форму реального ответа Insight `/rest/insight/1.0/iql/objects?includeAttributes=true`:
в атрибутах нет имени (только `objectTypeAttributeId`), значения бывают простыми, статусными,
ссылочными и мультизначными, а в одной выборке встречаются объекты разных типов с разными id атрибутов.
"""
import sys
import types
import unittest

from app.sources.additional.cmdb import cmdb_objects_to_long, cmdb_objects_to_table
from app.sources.jira_sm import _cmdb_api_base, _cmdb_shape, execute_jira_search_cmdb


def _attr(attr_id, values):
    return {"id": attr_id * 10, "objectTypeAttributeId": attr_id, "objectAttributeValues": values}


def _simple(value, display=None):
    return {"value": value, "displayValue": display if display is not None else value,
            "referencedType": False, "searchValue": value}


def _ref(object_id, label, display=None):
    return {"displayValue": display or label, "referencedType": True, "searchValue": f"HAM-{object_id}",
            "referencedObject": {"id": object_id, "label": label, "objectKey": f"HAM-{object_id}",
                                 "avatar": {"url16": "https://jira/icon.png?size=16"},
                                 "objectType": {"id": 1070, "name": "Software", "objectSchemaId": 1},
                                 "created": "2023-01-31T14:42:02.383Z", "hasAvatar": False,
                                 "timestamp": 1785855980559, "_links": {"self": "https://jira/obj"}}}


LAPTOP = {
    "id": 5762496, "label": "Apple-603", "objectKey": "HAM-5762496", "name": "Apple-603",
    "objectType": {"id": 334, "name": "Laptop", "objectSchemaId": 1, "icon": {"id": 104, "name": "Laptop"}},
    "created": "2023-01-31T14:42:02.383Z", "updated": "2026-08-04T15:06:20.559Z",
    "hasAvatar": False, "timestamp": 1785855980559, "archived": False,
    "avatar": {"url16": "https://jira/objecttype/334/icon.png?size=16"},
    "_links": {"self": "https://jira/secure/ShowObject.jspa?id=5762496"},
    "attributes": [
        _attr(2539, [_simple("HAM-5762496")]),
        _attr(2540, [_simple("Apple-603")]),
        _attr(2542, [_simple("2026-08-04T15:06:20.480Z", "04/авг/26 18:06")]),
        _attr(2544, [{"displayValue": "Списано", "referencedType": False,
                      "status": {"id": 131, "name": "Списано", "category": 0}}]),
        _attr(2546, [_ref(35524968, "Ноутбук APPLE A2442 MKGP3LL/A")]),
        _attr(2547, [_simple("VL2NPN7G4J")]),
        _attr(9629, [_ref(1, "Image Playground"), _ref(2, "Safari"), _ref(3, "Xcode")]),
        _attr(99999, [_simple("нет в карте имён")]),
        _attr(2550, []),   # атрибут без значений — колонки не даёт
    ],
}

AIRWATCH = {
    "id": 36399994, "label": "sc-mac-00665", "objectKey": "HAM-36399994", "name": "sc-mac-00665",
    "objectType": {"id": 1019, "name": "Airwatch", "objectSchemaId": 1},
    "created": "2026-03-18T05:28:01.914Z", "updated": "2026-08-03T20:55:26.016Z", "archived": False,
    "_links": {"self": "https://jira/secure/ShowObject.jspa?id=36399994"},
    "attributes": [
        _attr(8800, [_simple("HAM-36399994")]),
        _attr(8801, [_simple("sc-mac-00665")]),
        _attr(8806, [_ref(30792, "Иванов Дмитрий Владимирович")]),
        _attr(14471, [_simple("AppleOsX 15.3.1")]),
    ],
}

# карта id -> имя атрибута: как в блоке objectTypeAttributes ответа (тип Laptop) и /objecttype/1019/attributes
LAPTOP_NAMES = {"2539": "Key", "2540": "Name", "2542": "Updated", "2544": "Status",
                "2546": "Model", "2547": "Serial Number", "9629": "Installed Software", "2550": "Comment"}
AIRWATCH_NAMES = {"8800": "Key", "8801": "Name", "8806": "Owner", "14471": "OS"}


class TestCmdbTable(unittest.TestCase):
    def test_columns_named_by_attribute(self):
        row = cmdb_objects_to_table([LAPTOP], LAPTOP_NAMES)[0]
        self.assertEqual(row["Key"], "HAM-5762496")
        self.assertEqual(row["Name"], "Apple-603")
        self.assertEqual(row["Serial Number"], "VL2NPN7G4J")
        self.assertFalse(any(k.startswith("attributes_") for k in row))

    def test_object_metadata_without_noise(self):
        row = cmdb_objects_to_table([LAPTOP], LAPTOP_NAMES)[0]
        self.assertEqual(row["objectKey"], "HAM-5762496")
        self.assertEqual(row["id"], 5762496)
        self.assertEqual(row["objectType"], "Laptop")
        self.assertEqual(row["objectTypeId"], 334)
        self.assertEqual(row["url"], "https://jira/secure/ShowObject.jspa?id=5762496")
        for noise in ("avatar", "timestamp", "hasAvatar", "objectType_icon_id", "name"):
            self.assertNotIn(noise, row)

    def test_status_reference_and_date_values(self):
        row = cmdb_objects_to_table([LAPTOP], LAPTOP_NAMES)[0]
        self.assertEqual(row["Status"], "Списано")                              # status.name
        self.assertEqual(row["Model"], "Ноутбук APPLE A2442 MKGP3LL/A")         # referencedObject.label
        self.assertEqual(row["Updated"], "2026-08-04T15:06:20.480Z")            # машинный value, не displayValue

    def test_multivalue_joined_instead_of_column_explosion(self):
        row = cmdb_objects_to_table([LAPTOP], LAPTOP_NAMES)[0]
        self.assertEqual(row["Installed Software"], "Image Playground; Safari; Xcode")
        self.assertLess(len(row), 20)

    def test_max_values_marks_truncation(self):
        row = cmdb_objects_to_table([LAPTOP], LAPTOP_NAMES, max_values=2)[0]
        self.assertEqual(row["Installed Software"], "Image Playground; Safari; … +1")

    def test_empty_attribute_gives_no_column(self):
        self.assertNotIn("Comment", cmdb_objects_to_table([LAPTOP], LAPTOP_NAMES)[0])

    def test_unknown_attribute_id_falls_back_to_attr_id(self):
        self.assertEqual(cmdb_objects_to_table([LAPTOP], LAPTOP_NAMES)[0]["attr_99999"], "нет в карте имён")

    def test_different_object_types_share_columns_by_name(self):
        names = dict(LAPTOP_NAMES, **AIRWATCH_NAMES)
        rows = cmdb_objects_to_table([LAPTOP, AIRWATCH], names)
        self.assertEqual([r["Key"] for r in rows], ["HAM-5762496", "HAM-36399994"])
        self.assertEqual([r["Name"] for r in rows], ["Apple-603", "sc-mac-00665"])
        self.assertEqual(rows[1]["Owner"], "Иванов Дмитрий Владимирович")

    def test_duplicate_names_in_one_object_get_id_suffix(self):
        obj = {"id": 1, "objectType": {"id": 7, "name": "T"},
               "attributes": [_attr(11, [_simple("a")]), _attr(12, [_simple("b")])]}
        row = cmdb_objects_to_table([obj], {"11": "Name", "12": "Name"})[0]
        self.assertEqual(row["Name"], "a")
        self.assertEqual(row["Name [12]"], "b")

    def test_attribute_named_like_metadata_does_not_overwrite_it(self):
        obj = {"id": 1, "objectKey": "K-1", "objectType": {"id": 7, "name": "T"},
               "attributes": [_attr(11, [_simple("attr value")])]}
        row = cmdb_objects_to_table([obj], {"11": "objectKey"})[0]
        self.assertEqual(row["objectKey"], "K-1")
        self.assertEqual(row["objectKey [11]"], "attr value")

    def test_object_without_attributes_block_keeps_other_fields(self):
        # у некоторых эндпоинтов/версий Insight объект приходит без блока attributes — данные не теряем
        obj = {"id": 5, "objectKey": "HAM-5", "objectType": "Laptop", "name": "srv-1",
               "avatar": {"url16": "x"}, "timestamp": 1, "extra": {"nested": "v"}}
        row = cmdb_objects_to_table([obj])[0]
        self.assertEqual(row["objectKey"], "HAM-5")
        self.assertEqual(row["name"], "srv-1")
        self.assertEqual(row["extra_nested"], "v")
        self.assertNotIn("avatar_url16", row)
        self.assertNotIn("timestamp", row)

    def test_long_form_keeps_object_without_attributes(self):
        rows = cmdb_objects_to_long([{"id": 5, "objectKey": "HAM-5", "name": "srv-1"}])
        self.assertEqual(rows[0]["name"], "srv-1")
        self.assertNotIn("attribute", rows[0])

    def test_names_from_object_itself_assets_shape(self):
        # Assets кладёт в атрибут вложенный objectTypeAttribute с именем — карта не нужна
        obj = {"id": 1, "objectType": {"id": 7, "name": "T"},
               "attributes": [{"objectTypeAttribute": {"id": 55, "name": "Hostname"},
                               "objectAttributeValues": [_simple("host-1")]}]}
        self.assertEqual(cmdb_objects_to_table([obj])[0]["Hostname"], "host-1")


class TestCmdbLong(unittest.TestCase):
    def test_row_per_value(self):
        rows = cmdb_objects_to_long([LAPTOP], LAPTOP_NAMES)
        software = [r for r in rows if r["attribute"] == "Installed Software"]
        self.assertEqual([r["value"] for r in software], ["Image Playground", "Safari", "Xcode"])
        self.assertEqual([r["value_index"] for r in software], [0, 1, 2])
        self.assertEqual(software[0]["ref_objectKey"], "HAM-1")
        self.assertTrue(all(r["objectKey"] == "HAM-5762496" for r in rows))

    def test_no_rows_for_empty_attribute(self):
        self.assertFalse([r for r in cmdb_objects_to_long([LAPTOP], LAPTOP_NAMES) if r["attribute"] == "Comment"])


class TestCmdbShapeAndBase(unittest.TestCase):
    def test_default_shape_is_table(self):
        self.assertEqual(_cmdb_shape({}), "table")

    def test_legacy_flags(self):
        self.assertEqual(_cmdb_shape({"flatten": True}), "flat")
        self.assertEqual(_cmdb_shape({"raw": "true"}), "raw")
        self.assertEqual(_cmdb_shape({"flatten": True, "shape": "table"}), "table")

    def test_shape_aliases(self):
        self.assertEqual(_cmdb_shape({"shape": "LONG"}), "long")
        self.assertEqual(_cmdb_shape({"shape": "tidy"}), "long")
        self.assertEqual(_cmdb_shape({"shape": "flatten"}), "flat")
        self.assertEqual(_cmdb_shape({"shape": "мусор"}), "table")

    def test_api_base_from_search_path(self):
        self.assertEqual(_cmdb_api_base("/rest/insight/1.0/iql/objects"), "/rest/insight/1.0")
        self.assertEqual(_cmdb_api_base("/rest/assets/1.0/aql/objects"), "/rest/assets/1.0")
        self.assertEqual(_cmdb_api_base(None), "/rest/insight/1.0")


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


class _FakeRequests:
    """Заглушка requests: страница поиска Insight + эндпоинт имён атрибутов типа объекта."""

    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None, verify=None, timeout=None, **kwargs):
        self.calls.append((url, params))
        self.last_kwargs = kwargs        # proxies и прочие сквозные параметры вызова
        if url.endswith("/iql/objects"):
            if (params or {}).get("page", 1) > 1:
                return _FakeResponse({"objectEntries": []})
            payload = {"objectEntries": [LAPTOP, AIRWATCH]}
            if (params or {}).get("includeTypeAttributes") == "true":
                payload["objectTypeAttributes"] = [{"id": int(k), "name": v} for k, v in LAPTOP_NAMES.items()]
            return _FakeResponse(payload)
        if url.endswith("/objecttype/1019/attributes"):
            return _FakeResponse([{"id": int(k), "name": v} for k, v in AIRWATCH_NAMES.items()])
        return _FakeResponse([], status_code=404)


class TestSearchCmdbConnector(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeRequests()
        self._saved = sys.modules.get("requests")
        module = types.ModuleType("requests")
        module.get = self.fake.get
        sys.modules["requests"] = module
        self.source = {"url": "https://jira.example.ru/", "key": {"value": "token"},
                       "verify": False, "timeout": 5}
        self.state = {"app_name": "uh2s-test", "app_version": "test", "username": "tester"}

    def tearDown(self):
        if self._saved is None:
            del sys.modules["requests"]
        else:
            sys.modules["requests"] = self._saved

    def _run(self, parameters):
        return execute_jira_search_cmdb(parameters, self.source, {}, self.state)

    def test_table_shape_by_default(self):
        ok, info, _, data = self._run({"aql": 'objectType = "Laptop"'})
        self.assertTrue(ok)
        self.assertEqual(info, "2")
        self.assertEqual([r["Key"] for r in data], ["HAM-5762496", "HAM-36399994"])
        self.assertEqual(data[0]["Status"], "Списано")
        self.assertEqual(data[1]["OS"], "AppleOsX 15.3.1")   # имя из /objecttype/1019/attributes

    def test_type_attributes_requested_and_missing_names_fetched(self):
        self._run({"aql": "x"})
        search_calls = [p for u, p in self.fake.calls if u.endswith("/iql/objects")]
        self.assertTrue(all(p["includeTypeAttributes"] == "true" for p in search_calls))
        self.assertTrue(any(u.endswith("/rest/insight/1.0/objecttype/1019/attributes") for u, _ in self.fake.calls))

    def test_resolve_names_false_skips_extra_requests(self):
        ok, _, _, data = self._run({"aql": "x", "resolve_names": False})
        self.assertTrue(ok)
        self.assertFalse(any("objecttype" in u for u, _ in self.fake.calls))
        self.assertEqual(data[1]["attr_8800"], "HAM-36399994")   # имён для типа 1019 нет -> attr_<id>

    def test_long_shape(self):
        ok, _, _, data = self._run({"aql": "x", "shape": "long"})
        self.assertTrue(ok)
        self.assertTrue(all({"attribute", "value", "objectKey"} <= set(r) for r in data))
        self.assertEqual(len([r for r in data if r["attribute"] == "Installed Software"]), 3)

    def test_raw_and_flat_shapes_unchanged(self):
        ok, _, _, raw = self._run({"aql": "x", "shape": "raw"})
        self.assertTrue(ok)
        self.assertEqual(raw[0]["objectType"]["name"], "Laptop")
        ok, _, _, flat = self._run({"aql": "x", "flatten": True})
        self.assertTrue(ok)
        self.assertEqual(flat[0]["attributes_0_objectAttributeValues_0_value"], "HAM-5762496")

    def test_limit_applies_to_objects(self):
        ok, info, _, data = self._run({"aql": "x", "limit": 1})
        self.assertTrue(ok)
        self.assertEqual((info, len(data)), ("1", 1))

    def test_http_error_returns_failure(self):
        self.fake.get = lambda *a, **k: _FakeResponse({}, status_code=403)
        sys.modules["requests"].get = self.fake.get
        ok, info, _, data = self._run({"aql": "x"})
        self.assertFalse(ok)
        self.assertIn("403", info)
        self.assertEqual(data, [])


if __name__ == "__main__":
    unittest.main()
