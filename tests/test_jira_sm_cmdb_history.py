"""Офлайн-тесты истории (audit log) объекта CMDB JSM — jira_sm:get_cmdb_history.

Фикстура — реальный ответ Insight AM `/rest/insight-am/1/assets/{key}/audits`: блок metadata
(count/offset/limit/total) и results с вложенным author."""
import sys
import types
import unittest

from app.sources.jira_sm import _audit_in_period, _unfold_cmdb_audit, execute_jira_get_cmdb_history

AUDIT_NEW = {
    "type": "AUDIT", "action": "UPDATED", "id": 246612228,
    "title": "Изменение поля «Description»",
    "message": "Значение поля «Description» изменено на «ООО \"БИЗОН\"» (было: «ООО «БИЗон»»)",
    "author": {"key": "JIRAUSER50701", "name": "tech-cmdb-kafka", "displayName": "tech-cmdb-kafka",
               "avatarUrl": "https://jirasm.example.ru/secure/useravatar?avatarId=10122", "active": True},
    "occurredAt": "2026-05-11T02:46:36",
}
AUDIT_OLD = {
    "type": "AUDIT", "action": "UPDATED", "id": 70970585,
    "title": "Изменение поля «Description»",
    "message": "Значение поля «Description» изменено на «ООО «БИЗон»» (было: «ООО \"БИЗОН\"»)",
    "author": {"key": "JIRAUSER10587", "name": "tech-jira", "displayName": "Робот поддержки Jira",
               "avatarUrl": "https://jirasm.example.ru/secure/useravatar?ownerId=JIRAUSER10587", "active": True},
    "occurredAt": "2023-10-17T14:11:11",
}


class TestUnfoldAudit(unittest.TestCase):
    def test_author_is_flattened_without_avatar(self):
        row = _unfold_cmdb_audit(AUDIT_NEW, "HAM-2727707")
        self.assertEqual(row["objectKey"], "HAM-2727707")     # для склейки истории нескольких объектов
        self.assertEqual(row["author_displayName"], "tech-cmdb-kafka")
        self.assertEqual(row["author_key"], "JIRAUSER50701")
        self.assertEqual(row["author_name"], "tech-cmdb-kafka")
        self.assertTrue(row["author_active"])
        self.assertNotIn("author", row)
        self.assertFalse(any("avatar" in key.lower() for key in row))

    def test_core_fields_kept(self):
        row = _unfold_cmdb_audit(AUDIT_NEW, "HAM-1")
        self.assertEqual(row["occurredAt"], "2026-05-11T02:46:36")
        self.assertEqual((row["type"], row["action"], row["id"]), ("AUDIT", "UPDATED", 246612228))
        self.assertEqual(row["title"], "Изменение поля «Description»")
        self.assertIn("БИЗОН", row["message"])

    def test_unknown_fields_are_preserved(self):
        row = _unfold_cmdb_audit({**AUDIT_NEW, "objectId": 5, "extra": {"a": 1}}, "HAM-1")
        self.assertEqual(row["objectId"], 5)
        self.assertEqual(row["extra"], {"a": 1})

    def test_non_dict_entry(self):
        self.assertEqual(_unfold_cmdb_audit("junk", "HAM-1"), {"objectKey": "HAM-1", "value": "junk"})


class TestAuditPeriod(unittest.TestCase):
    def test_since_and_until(self):
        self.assertTrue(_audit_in_period(AUDIT_NEW, None, None))
        self.assertTrue(_audit_in_period(AUDIT_NEW, "2026-01-01", None))
        self.assertFalse(_audit_in_period(AUDIT_OLD, "2026-01-01", None))
        self.assertTrue(_audit_in_period(AUDIT_OLD, None, "2024-01-01"))
        self.assertFalse(_audit_in_period(AUDIT_NEW, None, "2024-01-01"))

    def test_entry_without_date_is_kept(self):
        self.assertTrue(_audit_in_period({"title": "x"}, "2026-01-01", "2026-12-31"))


class FakeRequests:
    """Заглушка requests: отдаёт страницы ответов по порядку и запоминает параметры вызовов."""

    def __init__(self, pages, status_code=200):
        self.pages = list(pages)
        self.status_code = status_code
        self.calls = []

    def get(self, url, headers=None, params=None, verify=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {})})
        payload = self.pages.pop(0) if self.pages else {"metadata": {"total": 0}, "results": []}
        response = types.SimpleNamespace(status_code=self.status_code, text="error body")
        response.json = lambda: payload
        return response


class TestGetCmdbHistory(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("requests")
        self.source = {"url": "https://jirasm.example.ru/", "key": {"value": "token"},
                       "verify": False, "timeout": 5}
        self.state = {"app_name": "t", "app_version": "0", "username": "u"}

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = self._saved

    def _install(self, pages, status_code=200):
        fake = FakeRequests(pages, status_code)
        module = types.ModuleType("requests")
        module.get = fake.get
        sys.modules["requests"] = module
        return fake

    def _page(self, results, total=None, offset=0, limit=10):
        return {"metadata": {"count": len(results), "offset": offset, "limit": limit,
                             "total": (len(results) if total is None else total)},
                "results": results}

    def test_real_response_becomes_flat_rows(self):
        fake = self._install([self._page([AUDIT_NEW, AUDIT_OLD], total=2)])
        ok, info, func, rows = execute_jira_get_cmdb_history(
            {"object_key": "HAM-2727707", "limit": 10,
             "criteria": "Изменение поля «Description»"}, self.source, {}, self.state)
        self.assertTrue(ok)
        self.assertEqual((info, func), ("2 of 2", "execute_jira_get_cmdb_history"))
        self.assertEqual([row["author_displayName"] for row in rows],
                         ["tech-cmdb-kafka", "Робот поддержки Jira"])
        self.assertEqual([row["occurredAt"] for row in rows],
                         ["2026-05-11T02:46:36", "2023-10-17T14:11:11"])

    def test_request_url_and_params(self):
        fake = self._install([self._page([AUDIT_NEW], total=1)])
        execute_jira_get_cmdb_history({"object_key": "HAM-2727707", "limit": 10,
                                       "criteria": "Изменение поля «Description»"},
                                      self.source, {}, self.state)
        call = fake.calls[0]
        self.assertEqual(call["url"], "https://jirasm.example.ru/rest/insight-am/1/assets/HAM-2727707/audits")
        self.assertEqual(call["params"], {"limit": 10, "offset": 0, "order": "MOST_RECENT",
                                          "type": "AUDIT", "criteria": "Изменение поля «Description»"})

    def test_criteria_and_type_omitted_when_empty(self):
        fake = self._install([self._page([AUDIT_NEW], total=1)])
        execute_jira_get_cmdb_history({"object_key": "HAM-1", "type": ""}, self.source, {}, self.state)
        self.assertNotIn("criteria", fake.calls[0]["params"])
        self.assertNotIn("type", fake.calls[0]["params"])

    def test_order_can_be_reversed(self):
        fake = self._install([self._page([AUDIT_OLD], total=1)])
        execute_jira_get_cmdb_history({"object_key": "HAM-1", "order": "LEAST_RECENT"},
                                      self.source, {}, self.state)
        self.assertEqual(fake.calls[0]["params"]["order"], "LEAST_RECENT")

    def test_pagination_walks_offset_until_total(self):
        fake = self._install([self._page([AUDIT_NEW] * 100, total=150, limit=100),
                              self._page([AUDIT_OLD] * 50, total=150, offset=100, limit=100)])
        ok, info, _func, rows = execute_jira_get_cmdb_history(
            {"object_key": "HAM-1", "limit": 150}, self.source, {}, self.state)
        self.assertTrue(ok)
        self.assertEqual(len(rows), 150)
        self.assertEqual([call["params"]["offset"] for call in fake.calls], [0, 100])
        self.assertEqual(info, "150 of 150")

    def test_limit_is_respected(self):
        self._install([self._page([AUDIT_NEW, AUDIT_OLD], total=2)])
        ok, _info, _func, rows = execute_jira_get_cmdb_history(
            {"object_key": "HAM-1", "limit": 1}, self.source, {}, self.state)
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)

    def test_since_filters_client_side(self):
        self._install([self._page([AUDIT_NEW, AUDIT_OLD], total=2)])
        ok, _info, _func, rows = execute_jira_get_cmdb_history(
            {"object_key": "HAM-1", "since": "2026-01-01"}, self.source, {}, self.state)
        self.assertTrue(ok)
        self.assertEqual([row["id"] for row in rows], [246612228])

    def test_raw_returns_original_entries(self):
        self._install([self._page([AUDIT_NEW], total=1)])
        ok, _info, _func, rows = execute_jira_get_cmdb_history(
            {"object_key": "HAM-1", "raw": True}, self.source, {}, self.state)
        self.assertTrue(ok)
        self.assertEqual(rows[0]["author"]["displayName"], "tech-cmdb-kafka")

    def test_empty_history(self):
        self._install([self._page([], total=0)])
        ok, info, _func, rows = execute_jira_get_cmdb_history({"object_key": "HAM-1"}, self.source, {}, self.state)
        self.assertTrue(ok)
        self.assertEqual((rows, info), ([], "0 of 0"))

    def test_object_key_is_required(self):
        self._install([])
        ok, info, _func, rows = execute_jira_get_cmdb_history({}, self.source, {}, self.state)
        self.assertFalse(ok)
        self.assertIn("object_key is required", info)
        self.assertEqual(rows, [])

    def test_http_error_reports_status_and_body(self):
        self._install([self._page([])], status_code=404)
        ok, info, _func, rows = execute_jira_get_cmdb_history({"object_key": "HAM-404"}, self.source, {}, self.state)
        self.assertFalse(ok)
        self.assertIn("http 404", info)
        self.assertIn("error body", info)
        self.assertEqual(rows, [])

    def test_proxies_from_source_are_passed(self):
        fake = FakeRequests([self._page([AUDIT_NEW], total=1)])
        module = types.ModuleType("requests")
        captured = {}

        def get(url, headers=None, params=None, verify=None, timeout=None, **kwargs):
            captured.update(kwargs)
            return fake.get(url, headers=headers, params=params, verify=verify, timeout=timeout)

        module.get = get
        sys.modules["requests"] = module
        source = dict(self.source, proxies={"http": "", "https": ""})
        execute_jira_get_cmdb_history({"object_key": "HAM-1"}, source, {}, self.state)
        self.assertEqual(captured.get("proxies"), {"http": "", "https": ""})


class TestRegistry(unittest.TestCase):
    def test_function_is_registered_with_params(self):
        from app.engine import ENGINE_SOURCES_AND_FUNCTIONS_MAP as sources_map
        spec = sources_map["jira_sm"]["functions"]["get_cmdb_history"]
        self.assertIn("object_key", spec["required"])
        for name in ("limit", "criteria", "order", "type", "since", "until", "audits_path", "raw"):
            self.assertIn(name, spec["unrequired"], name)


if __name__ == "__main__":
    unittest.main()
