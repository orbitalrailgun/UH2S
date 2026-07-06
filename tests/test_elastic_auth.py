"""Офлайн-тесты выбора метода аутентификации elastic_requests (_build_auth_header) — без сети."""
import base64
import unittest

from app.sources.additional.elastic2python import _build_auth_header


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


if __name__ == "__main__":
    unittest.main()
