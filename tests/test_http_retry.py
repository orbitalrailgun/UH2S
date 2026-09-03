"""Офлайн-тесты повторов HTTP-запросов (app/sources/additional/http_retry) и их применения в jira_sm.

До этого ретраи были только в elastic_requests и llm_source: одиночный 502/таймаут от Jira ронял весь
прогон. Сеть не нужна — requests подменяется заглушкой, сон в retry_call перехватывается."""
import sys
import types
import unittest

from app.sources.additional.http_retry import (DEFAULT_RETRY_STATUSES, RETRY_AFTER_MAX_SECONDS,
                                               parse_retry_after, request_with_retry, retry_config)
from app.sources.additional.retry import RetryableError, retry_call

CS = {"app_name": "t", "app_version": "0", "username": "u"}


class FakeResponse:
    def __init__(self, status_code=200, text="body", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.reason = ""
        self.url = "https://jira.example.ru/rest/api/2/search"

    def json(self):
        return {}


def install_fake_requests():
    """Заглушка requests с настоящими классами исключений (их смотрит request_with_retry)."""
    module = types.ModuleType("requests")
    module.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError, Timeout=TimeoutError)
    sys.modules["requests"] = module
    return module


class RequestsStubCase(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("requests")
        install_fake_requests()

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = self._saved


class TestParseRetryAfter(unittest.TestCase):
    def test_numeric_header(self):
        self.assertEqual(parse_retry_after(FakeResponse(headers={"Retry-After": "7"})), 7.0)
        self.assertEqual(parse_retry_after(FakeResponse(headers={"Retry-After": " 2.5 "})), 2.5)

    def test_absent_or_unparsable(self):
        self.assertIsNone(parse_retry_after(FakeResponse()))
        self.assertIsNone(parse_retry_after(FakeResponse(headers={"Retry-After": ""})))
        # HTTP-date форму игнорируем — сработает обычный backoff
        self.assertIsNone(parse_retry_after(FakeResponse(headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})))
        self.assertIsNone(parse_retry_after(FakeResponse(headers={"Retry-After": "-5"})))

    def test_capped(self):
        self.assertEqual(parse_retry_after(FakeResponse(headers={"Retry-After": "100000"})),
                         RETRY_AFTER_MAX_SECONDS)


class TestRetryConfig(unittest.TestCase):
    def test_defaults(self):
        config = retry_config({})
        self.assertEqual((config["attempts"], config["backoff"]), (3, 0.5))
        self.assertEqual(config["retry_statuses"], DEFAULT_RETRY_STATUSES)
        self.assertNotIn("on_retry", config)

    def test_from_source_config(self):
        config = retry_config({"max_retries": 5, "retry_backoff_seconds": 2, "retry_on_status": [503]})
        self.assertEqual((config["attempts"], config["backoff"], config["retry_statuses"]), (6, 2.0, (503,)))

    def test_broken_values_fall_back(self):
        config = retry_config({"max_retries": "x", "retry_backoff_seconds": "y", "retry_on_status": "nonsense"})
        self.assertEqual((config["attempts"], config["backoff"]), (3, 0.5))
        self.assertEqual(config["retry_statuses"], DEFAULT_RETRY_STATUSES)

    def test_logger_added_with_state(self):
        config = retry_config({}, CS, "func")
        self.assertTrue(callable(config["on_retry"]))


class TestRequestWithRetry(RequestsStubCase):
    def test_transient_status_is_retried_then_succeeds(self):
        responses = [FakeResponse(502), FakeResponse(503), FakeResponse(200, "ok")]
        calls = []

        def request_fn():
            calls.append(1)
            return responses[len(calls) - 1]

        result = request_with_retry(request_fn, attempts=3, backoff=0)
        self.assertEqual((result.status_code, len(calls)), (200, 3))

    def test_non_retryable_status_returned_immediately(self):
        calls = []

        def request_fn():
            calls.append(1)
            return FakeResponse(404, "not found")

        result = request_with_retry(request_fn, attempts=3, backoff=0)
        self.assertEqual((result.status_code, len(calls)), (404, 1))   # 4xx не повторяем

    def test_attempts_exhausted_raises_with_diagnostics(self):
        def request_fn():
            return FakeResponse(502, '{"message":"Bad Gateway from proxy"}',
                                headers={"x-request-id": "req-1"})

        with self.assertRaises(RetryableError) as caught:
            request_with_retry(request_fn, attempts=2, backoff=0)
        message = str(caught.exception)
        self.assertIn("HTTP 502", message)
        self.assertIn("Bad Gateway from proxy", message)
        self.assertIn("x-request-id=req-1", message)
        self.assertEqual(caught.exception.status, 502)

    def test_network_errors_are_retried(self):
        calls = []

        def request_fn():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("connection reset")
            if len(calls) == 2:
                raise TimeoutError("read timeout")
            return FakeResponse(200)

        result = request_with_retry(request_fn, attempts=3, backoff=0)
        self.assertEqual((result.status_code, len(calls)), (200, 3))

    def test_retry_after_defines_the_pause(self):
        sleeps = []
        responses = [FakeResponse(429, headers={"Retry-After": "9"}), FakeResponse(200)]
        calls = []

        def request_fn():
            calls.append(1)
            return responses[len(calls) - 1]

        # request_with_retry использует retry_call; проверяем задержку через его sleep-хук
        def attempt():
            response = request_fn()
            if response.status_code in (429,):
                raise RetryableError("429", 429, retry_after=parse_retry_after(response))
            return response

        retry_call(attempt, attempts=2, backoff=0.5, sleep=sleeps.append)
        self.assertEqual(sleeps, [9.0])       # пауза от сервера, не экспоненциальный backoff

    def test_on_retry_is_called_per_attempt(self):
        events = []
        responses = [FakeResponse(503), FakeResponse(200)]
        calls = []

        def request_fn():
            calls.append(1)
            return responses[len(calls) - 1]

        request_with_retry(request_fn, attempts=2, backoff=0,
                           on_retry=lambda attempt, error, delay: events.append((attempt, error.status)))
        self.assertEqual(events, [(1, 503)])


class TestJiraSmRetries(RequestsStubCase):
    """jira_sm: одиночный 502 больше не роняет шаг, 404 не повторяется."""

    def setUp(self):
        super().setUp()
        self.source = {"url": "https://jira.example.ru", "key": {"value": "token"},
                       "verify": False, "timeout": 5, "retry_backoff_seconds": 0, "max_retries": 2}

    def _install_sequence(self, responses):
        calls = []

        def get(url, headers=None, params=None, verify=None, timeout=None, **kwargs):
            calls.append(url)
            return responses[min(len(calls) - 1, len(responses) - 1)]

        sys.modules["requests"].get = get
        sys.modules["requests"].post = get
        return calls

    def _json_response(self, payload, status_code=200):
        response = FakeResponse(status_code)
        response.json = lambda: payload
        return response

    def test_transient_502_then_success(self):
        from app.sources.jira_sm import execute_jira_get_cmdb_history
        calls = self._install_sequence([FakeResponse(502), self._json_response(
            {"metadata": {"total": 1}, "results": [{"type": "AUDIT", "occurredAt": "2026-01-01T00:00:00"}]})])
        ok, info, _func, rows = execute_jira_get_cmdb_history({"object_key": "HAM-1"}, self.source, {}, CS)
        self.assertTrue(ok, info)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(calls), 2)          # первая попытка 502, вторая успешная

    def test_404_is_not_retried(self):
        from app.sources.jira_sm import execute_jira_get_cmdb_history
        calls = self._install_sequence([FakeResponse(404, "no such asset")])
        ok, info, _func, _rows = execute_jira_get_cmdb_history({"object_key": "HAM-404"}, self.source, {}, CS)
        self.assertFalse(ok)
        self.assertIn("http 404", info)
        self.assertEqual(len(calls), 1)

    def test_exhausted_retries_report_diagnostics(self):
        from app.sources.jira_sm import execute_jira_search_issues
        self._install_sequence([FakeResponse(503, '{"message":"cluster overloaded"}')])
        ok, info, _func, _rows = execute_jira_search_issues({"jql": "project = X"}, self.source, {}, CS)
        self.assertFalse(ok)
        self.assertIn("HTTP 503", info)
        self.assertIn("cluster overloaded", info)

    def test_search_cmdb_retries_too(self):
        from app.sources.jira_sm import execute_jira_search_cmdb
        calls = self._install_sequence([FakeResponse(504), self._json_response({"objectEntries": []})])
        ok, info, _func, _rows = execute_jira_search_cmdb({"aql": "objectType = \"Laptop\""},
                                                          self.source, {}, CS)
        self.assertTrue(ok, info)
        self.assertEqual(len(calls), 2)

    def test_registry_documents_retry_params(self):
        from app.engine import ENGINE_SOURCES_AND_FUNCTIONS_MAP as sources_map
        optional = sources_map["jira_sm"]["unrequired"]
        for name in ("max_retries", "retry_backoff_seconds", "retry_on_status", "error_body_limit"):
            self.assertIn(name, optional, name)


if __name__ == "__main__":
    unittest.main()
