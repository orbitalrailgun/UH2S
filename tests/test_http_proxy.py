"""Офлайн-тесты настройки прокси из конфига объекта (app/sources/additional/http_proxy) и её проброса
в requests у источников. Сеть не нужна: requests подменяется фейком, который запоминает kwargs вызова."""
import sys
import types
import unittest

from app.sources.additional.http_proxy import proxies_from_source, proxy_kwargs

# конфиг из задачи: пустые значения отключают прокси окружения, лишний ключ requests игнорирует
NO_PROXY = {"http": "", "https": "", "no": "pass"}


class TestProxiesFromSource(unittest.TestCase):
    def test_proxies_dict_passed_through_verbatim(self):
        self.assertEqual(proxies_from_source({"proxies": NO_PROXY}), {"http": "", "https": "", "no": "pass"})

    def test_explicit_proxy_addresses(self):
        config = {"proxies": {"http": "http://proxy:3128", "https": "http://proxy:3128"}}
        self.assertEqual(proxies_from_source(config), {"http": "http://proxy:3128", "https": "http://proxy:3128"})

    def test_proxy_shorthand_covers_both_schemes(self):
        self.assertEqual(proxies_from_source({"proxy": " http://proxy:3128 "}),
                         {"http": "http://proxy:3128", "https": "http://proxy:3128"})

    def test_no_proxy_flag_shorthand(self):
        self.assertEqual(proxies_from_source({"no_proxy": True}), {"http": "", "https": ""})

    def test_nothing_configured_returns_none(self):
        for config in ({}, {"verify": True}, {"proxies": {}}, {"proxy": "  "}, {"no_proxy": False}, None, "x"):
            self.assertIsNone(proxies_from_source(config), config)

    def test_none_values_become_empty_strings(self):
        self.assertEqual(proxies_from_source({"proxies": {"http": None}}), {"http": ""})

    def test_proxy_kwargs_is_empty_without_configuration(self):
        self.assertEqual(proxy_kwargs({}), {})                       # вызов остаётся буквально прежним
        self.assertEqual(proxy_kwargs({"proxies": NO_PROXY}), {"proxies": NO_PROXY})


class FakeResponse:
    status_code = 200
    text = "{}"
    reason = "OK"
    url = "https://host/api"
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class ProxyPassthroughCase(unittest.TestCase):
    """Базовый класс: подменяет requests и запоминает kwargs каждого вызова."""

    payload = {}

    def setUp(self):
        self.calls = []
        self._saved = sys.modules.get("requests")
        module = types.ModuleType("requests")

        def record(*args, **kwargs):
            self.calls.append(kwargs)
            return FakeResponse(self.payload)

        module.get = record
        module.post = record
        module.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError, Timeout=TimeoutError)
        sys.modules["requests"] = module

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = self._saved

    @property
    def state(self):
        return {"app_name": "t", "app_version": "0", "username": "u"}


class TestJiraProxyPassthrough(ProxyPassthroughCase):
    payload = {"issues": [], "total": 0}

    def _source(self, **extra):
        return {"url": "https://jira.local", "key": {"value": "token"}, "verify": False, "timeout": 5, **extra}

    def test_proxies_reach_requests(self):
        from app.sources.jira_sm import execute_jira_search_issues
        ok, _info, _fn, _data = execute_jira_search_issues(
            {"jql": "project = X"}, self._source(proxies=NO_PROXY), {}, self.state)
        self.assertTrue(ok)
        self.assertEqual(self.calls[0].get("proxies"), NO_PROXY)

    def test_no_proxies_key_when_not_configured(self):
        from app.sources.jira_sm import execute_jira_search_issues
        ok, _info, _fn, _data = execute_jira_search_issues({"jql": "project = X"}, self._source(), {}, self.state)
        self.assertTrue(ok)
        self.assertNotIn("proxies", self.calls[0])


class TestElasticProxyPassthrough(ProxyPassthroughCase):
    payload = {"aggregations": {}}

    def test_proxies_reach_requests(self):
        from app.sources.elastic_requests import execute_elastic_aggs
        source = {"key": {"value": "k"}, "max_retries": 0, "proxies": NO_PROXY}
        execute_elastic_aggs({"url": "https://kibana/x", "query": {}, "aggs": {}}, source, {}, self.state)
        self.assertEqual(self.calls[0].get("proxies"), NO_PROXY)

    def test_proxy_shorthand_reaches_requests(self):
        from app.sources.elastic_requests import execute_elastic_aggs
        source = {"key": {"value": "k"}, "max_retries": 0, "proxy": "http://proxy:3128"}
        execute_elastic_aggs({"url": "https://kibana/x", "query": {}, "aggs": {}}, source, {}, self.state)
        self.assertEqual(self.calls[0].get("proxies"),
                         {"http": "http://proxy:3128", "https": "http://proxy:3128"})

    def test_none_passed_when_not_configured(self):
        from app.sources.elastic_requests import execute_elastic_aggs
        source = {"key": {"value": "k"}, "max_retries": 0}
        execute_elastic_aggs({"url": "https://kibana/x", "query": {}, "aggs": {}}, source, {}, self.state)
        self.assertIsNone(self.calls[0].get("proxies"))   # как раньше: окружение учитывается


class TestNetboxProxyPassthrough(ProxyPassthroughCase):
    payload = {"results": [], "next": None}

    def test_proxies_reach_requests(self):
        from app.sources.netbox import execute_netbox_search_cidr_by_ipaddress
        source = {"url": "https://netbox.local", "key": {"value": "t"}, "verify": False, "timeout": 5,
                  "proxies": NO_PROXY}
        execute_netbox_search_cidr_by_ipaddress({"target": "10.0.0.1"}, source, {}, self.state)
        self.assertEqual(self.calls[0].get("proxies"), NO_PROXY)


class TestRegistryDocumentsProxies(unittest.TestCase):
    def test_requests_based_sources_expose_proxies_in_config(self):
        from app.engine import ENGINE_SOURCES_AND_FUNCTIONS_MAP as sources_map
        for source_type in ("elastic_requests", "jira_sm", "netbox", "irp_thehive", "youtrack",
                            "gitlab", "irp_iris", "manticoresearch", "llm"):
            with self.subTest(source_type=source_type):
                self.assertIn("proxies", sources_map[source_type].get("unrequired") or {})


if __name__ == "__main__":
    unittest.main()
