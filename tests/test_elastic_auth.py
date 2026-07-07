"""Офлайн-тесты выбора метода аутентификации elastic_requests (_build_auth_header) — без сети."""
import base64
import unittest

from app.sources.additional.elastic2python import (_build_auth_header, _console_proxy_headers,
                                                   _extract_body_error, _normalize_to_records)


class TestElasticAuthHeader(unittest.TestCase):
    def test_api_key_default(self):
        # по умолчанию (auth_type None/api_key) — прежнее поведение: ApiKey <value>
        self.assertEqual(_build_auth_header(None, None, "SECRET"), "ApiKey SECRET")
        self.assertEqual(_build_auth_header("api_key", None, "SECRET"), "ApiKey SECRET")
        self.assertEqual(_build_auth_header("ApiKey", None, "SECRET"), "ApiKey SECRET")

    def test_basic_auth(self):
        header = _build_auth_header("basic_auth", "user1", "pass1")
        self.assertTrue(header.startswith("Basic "))
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
        self.assertEqual(decoded, "user1:pass1")

    def test_http_auth_alias(self):
        # алиас http_auth (как в opensearch) даёт тот же Basic
        header = _build_auth_header("http_auth", "u", "p")
        self.assertEqual(base64.b64decode(header.split(" ", 1)[1]).decode(), "u:p")

    def test_unknown_auth_type_raises(self):
        with self.assertRaises(ValueError):
            _build_auth_header("kerberos", "u", "p")


class TestConsoleProxyHeaders(unittest.TestCase):
    def test_sends_both_xsrf_flavors(self):
        # console-proxy идентичен у Kibana и OpenSearch Dashboards, но требует разный xsrf-заголовок;
        # шлём оба, чтобы один конфиг работал в обеих системах.
        headers = _console_proxy_headers("agent/1.0", "ApiKey SECRET")
        self.assertEqual(headers["kbn-xsrf"], "reporting")
        self.assertEqual(headers["osd-xsrf"], "true")
        self.assertEqual(headers["Authorization"], "ApiKey SECRET")


class TestExtractBodyError(unittest.TestCase):
    def test_opensearch_dashboards_format(self):
        # OSD: statusCode + error + message (раньше давало "status None: Bad Request")
        status, reason = _extract_body_error(
            {"statusCode": 400, "error": "Bad Request", "message": "Request must contain a osd-xsrf header."})
        self.assertEqual(status, 400)
        self.assertIn("osd-xsrf", reason)

    def test_elastic_format(self):
        status, reason = _extract_body_error({"error": {"reason": "parse_exception"}, "status": 400})
        self.assertEqual(status, 400)
        self.assertEqual(reason, "parse_exception")

    def test_missing_status_is_none(self):
        status, _ = _extract_body_error({"error": "boom"})
        self.assertIsNone(status)


class TestNormalizeToRecords(unittest.TestCase):
    def test_cat_indices_list_passthrough(self):
        # _cat/indices?format=json -> список готовых dict, возвращаем как есть
        rows = _normalize_to_records([{"index": "logs-1", "health": "green"}, {"index": "logs-2"}])
        self.assertEqual(rows, [{"index": "logs-1", "health": "green"}, {"index": "logs-2"}])

    def test_aliases_dict_to_rows(self):
        # _aliases -> {index: {...}} нормализуем в строки с ключом name
        rows = _normalize_to_records({"logs-1": {"aliases": {"cur": {}}}})
        self.assertEqual(rows, [{"name": "logs-1", "aliases": {"cur": {}}}])

    def test_scalar_list(self):
        self.assertEqual(_normalize_to_records(["a", "b"]), [{"value": "a"}, {"value": "b"}])

    def test_other_is_empty(self):
        self.assertEqual(_normalize_to_records(42), [])


class TestListIndicesRegistered(unittest.TestCase):
    def test_engine_map_has_list_indices(self):
        from app.engine import ENGINE_SOURCES_AND_FUNCTIONS_MAP as M
        self.assertIn("list_indices", M["elastic_requests"]["functions"])


if __name__ == "__main__":
    unittest.main()
