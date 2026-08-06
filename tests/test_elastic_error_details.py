"""Офлайн-тесты диагностики ошибок elastic_requests: на 5xx (типично 502 от console-proxy) в сообщение
должны попадать тело ответа и служебные заголовки, а креды — маскироваться. Сеть не нужна: requests
подменяется фейком, ретраи выключены (max_retries=0), чтобы тесты не спали."""
import sys
import types
import unittest
import datetime

from app.sources.additional.elastic2python import (ERROR_BODY_LIMIT, _attempts_note, _redact,
                                                   _response_detail, data_taxi_aggs_requests,
                                                   data_taxi_list_requests)


class FakeResponse:
    def __init__(self, status_code=502, text="", headers=None, reason="", url="https://kibana/api/console/proxy",
                 elapsed_seconds=0.25, json_data=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.reason = reason
        self.url = url
        self.elapsed = datetime.timedelta(seconds=elapsed_seconds)
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class TestRedact(unittest.TestCase):
    def test_authorization_schemes_masked(self):
        self.assertEqual(_redact("Authorization: ApiKey aBc123=="), "Authorization: ***")
        self.assertIn("Bearer ***", _redact("token is Bearer eyJhbGciOi.J9"))
        self.assertIn("Basic ***", _redact("Basic dXNlcjpwYXNz"))

    def test_json_secret_fields_masked(self):
        text = '{"api_key": "s3cret", "password":"p@ss", "query": "keep me"}'
        redacted = _redact(text)
        self.assertNotIn("s3cret", redacted)
        self.assertNotIn("p@ss", redacted)
        self.assertIn("keep me", redacted)

    def test_url_credentials_masked(self):
        self.assertEqual(_redact("https://user:pass@es.local/_search"), "https://***:***@es.local/_search")

    def test_empty_input(self):
        self.assertEqual(_redact(None), "")


class TestResponseDetail(unittest.TestCase):
    def test_status_body_and_headers(self):
        resp = FakeResponse(status_code=502, reason="Bad Gateway",
                            text='{"statusCode":502,"error":"Bad Gateway","message":"socket hang up"}',
                            headers={"content-type": "application/json", "x-request-id": "abc-123"})
        detail = _response_detail(resp)
        self.assertIn("HTTP 502 Bad Gateway", detail)
        self.assertIn("socket hang up", detail)
        self.assertIn("x-request-id=abc-123", detail)
        self.assertIn("content-type=application/json", detail)
        self.assertIn("url=https://kibana/api/console/proxy", detail)
        self.assertIn("elapsed=0.25s", detail)

    def test_html_proxy_page_is_collapsed(self):
        resp = FakeResponse(text="<html>\n<head><title>502 Bad Gateway</title></head>\n<body>\n</body>\n</html>",
                            headers={"content-type": "text/html"})
        detail = _response_detail(resp)
        self.assertIn("502 Bad Gateway", detail)
        self.assertNotIn("\n", detail)

    def test_long_body_truncated_with_counter(self):
        resp = FakeResponse(text="x" * (ERROR_BODY_LIMIT + 500))
        detail = _response_detail(resp)
        self.assertIn(f"(+500 chars)", detail)
        self.assertLess(len(detail), ERROR_BODY_LIMIT + 400)

    def test_empty_body_is_stated(self):
        self.assertIn("body: (empty)", _response_detail(FakeResponse(text="")))

    def test_credentials_in_body_masked(self):
        resp = FakeResponse(text='{"message":"upstream rejected","headers":{"authorization":"ApiKey s3cret"}}')
        detail = _response_detail(resp)
        self.assertNotIn("s3cret", detail)

    def test_survives_response_without_optional_attributes(self):
        bare = types.SimpleNamespace(status_code=500, text="boom")
        detail = _response_detail(bare)
        self.assertIn("HTTP 500", detail)
        self.assertIn("boom", detail)


class TestAttemptsNote(unittest.TestCase):
    def test_note_only_when_retried(self):
        self.assertEqual(_attempts_note(0), "")
        self.assertEqual(_attempts_note(2), " [attempts=3]")


class FakeRequests:
    """Заглушка requests: любой запрос отдаёт заданный ответ; исключения запросов — реальных классов."""

    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError, Timeout=TimeoutError)

    def post(self, *args, **kwargs):
        self.calls += 1
        return self.response

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


class TestAggsRequestsErrorMessage(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("requests")

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = self._saved

    def _install(self, response):
        fake = FakeRequests(response)
        module = types.ModuleType("requests")
        module.post, module.get, module.exceptions = fake.post, fake.get, fake.exceptions
        sys.modules["requests"] = module
        return fake

    def test_502_message_carries_body_and_status(self):
        # регрессия: раньше сообщение было ровно "elastic2python aggs requests fail:status 502"
        response = FakeResponse(status_code=502, reason="Bad Gateway",
                                text='{"statusCode":502,"error":"Bad Gateway","message":"socket hang up"}',
                                headers={"content-type": "application/json", "x-request-id": "req-7"})
        self._install(response)
        ok, message, func, data = data_taxi_aggs_requests(
            "https://kibana/api/console/proxy", "uh/0", "secret-key", False, 5,
            {"match_all": {}}, {"a": {"terms": {"field": "f"}}}, max_retries=0)
        self.assertFalse(ok)
        self.assertEqual((func, data), ("data_taxi_aggs_requests", []))
        self.assertIn("HTTP 502 Bad Gateway", message)
        self.assertIn("socket hang up", message)
        self.assertIn("x-request-id=req-7", message)
        self.assertNotIn("secret-key", message)          # ключ источника не утекает в сообщение
        self.assertNotIn("[attempts=", message)          # ретраи выключены -> пометки нет

    def test_attempts_note_present_when_retries_configured(self):
        self._install(FakeResponse(status_code=503, text="upstream down"))
        sleeps = []
        ok, message, _func, _data = data_taxi_aggs_requests(
            "https://kibana/api/console/proxy", "uh/0", "k", False, 5, {}, {},
            max_retries=1, retry_backoff=0, on_retry=lambda *a: sleeps.append(a))
        self.assertFalse(ok)
        self.assertIn("[attempts=2]", message)
        self.assertIn("upstream down", message)
        self.assertEqual(len(sleeps), 1)                 # один повтор состоялся

    def test_body_error_with_reason_keeps_reason(self):
        # HTTP 200 с телом-ошибкой: осмысленный reason остаётся основным текстом
        response = FakeResponse(status_code=200, text='{"error":{"reason":"failed to parse date field"},"status":400}',
                                json_data={"error": {"reason": "failed to parse date field"}, "status": 400})
        self._install(response)
        ok, message, _func, _data = data_taxi_aggs_requests(
            "https://kibana/api/console/proxy", "uh/0", "k", False, 5, {}, {}, max_retries=0)
        self.assertFalse(ok)
        self.assertIn("failed to parse date field", message)

    def test_non_retryable_bad_status_reports_detail_not_raw_text(self):
        # 400 не входит в retry_statuses -> путь "fail response code": тело ограничено, а не целиком
        response = FakeResponse(status_code=400, reason="Bad Request", text="y" * (ERROR_BODY_LIMIT + 100))
        self._install(response)
        ok, message, _func, _data = data_taxi_aggs_requests(
            "https://kibana/api/console/proxy", "uh/0", "k", False, 5, {}, {}, max_retries=0)
        self.assertFalse(ok)
        self.assertIn("HTTP 400 Bad Request", message)
        self.assertIn("(+100 chars)", message)

    def test_list_requests_also_reports_detail(self):
        self._install(FakeResponse(status_code=502, reason="Bad Gateway", text="proxy error",
                                   headers={"content-type": "text/plain"}))
        ok, message, _func, _data = data_taxi_list_requests(
            "https://kibana/api/console/proxy", "uh/0", "k", False, 5, max_retries=0)
        self.assertFalse(ok)
        self.assertIn("HTTP 502 Bad Gateway", message)
        self.assertIn("proxy error", message)


if __name__ == "__main__":
    unittest.main()
