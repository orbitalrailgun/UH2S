"""Повторы HTTP-запросов для коннекторов на `requests` — поверх общего `retry_call`.

Повторяются транзиентные неудачи: сетевые ошибки и таймауты, а также ответы с кодами из
`retry_statuses` (по умолчанию 429/502/503/504). На 4xx (кроме 429) повторов нет — это ошибка запроса,
её повторение только удлиняет прогон.

Если сервер прислал `Retry-After` (Jira отдаёт его при 429), пауза берётся из заголовка, а не из
экспоненциального backoff — иначе повтор придёт раньше, чем разрешено, и снова получит 429.

Сообщение об ошибке собирается диагностикой ответа (статус, request-id, тело — усечённое, с
замаскированными кредами), чтобы после исчерпания попыток было видно причину, а не только код.
"""
import syslog

from app.logging import get_log_message, logger_log
from app.sources.additional.elastic2python import ERROR_BODY_LIMIT, _response_detail
from app.sources.additional.retry import RetryableError, retry_call

DEFAULT_RETRY_STATUSES = (429, 502, 503, 504)
# верхняя граница уважения к Retry-After: сервер не должен подвесить прогон на часы
RETRY_AFTER_MAX_SECONDS = 300


def parse_retry_after(response):
    """Значение `Retry-After` в секундах (только числовая форма) или None.

    HTTP-date форма встречается редко и её игнорируем — тогда сработает обычный backoff."""
    try:
        raw = (response.headers or {}).get("Retry-After")
    except BaseException:
        return None
    if raw in (None, ""):
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, RETRY_AFTER_MAX_SECONDS)


def retry_config(source, current_state=None, func_name=""):
    """Параметры повторов из конфига источника: max_retries, retry_backoff_seconds, retry_on_status.

    Возвращает dict для `request_with_retry` (включая on_retry-логгер, если задан current_state)."""
    source = source if isinstance(source, dict) else {}
    try:
        attempts = max(1, int(source.get("max_retries", 2)) + 1)
    except (TypeError, ValueError):
        attempts = 3
    try:
        backoff = float(source.get("retry_backoff_seconds", 0.5))
    except (TypeError, ValueError):
        backoff = 0.5
    statuses = source.get("retry_on_status") or DEFAULT_RETRY_STATUSES
    try:
        statuses = tuple(int(code) for code in statuses)
    except (TypeError, ValueError):
        statuses = DEFAULT_RETRY_STATUSES
    config = {"attempts": attempts, "backoff": backoff, "retry_statuses": statuses,
              "error_body_limit": int(source.get("error_body_limit", ERROR_BODY_LIMIT) or ERROR_BODY_LIMIT)}
    if current_state is not None:
        config["on_retry"] = make_retry_logger(current_state, func_name)
    return config


def make_retry_logger(current_state, func_name=""):
    """Логгер попыток (WARNING): номер попытки, задержка и диагностика ответа."""
    def on_retry(attempt, error, delay):
        status = getattr(error, "status", None)
        detail = f"{type(error).__name__}: {error}"
        if status:
            detail = f"status {status} | {detail}"
        logger_log(syslog.LOG_WARNING, get_log_message(
            f"http retry attempt {attempt} after {delay:.2f}s ({detail})", func_name or "request_with_retry",
            current_state))
    return on_retry


def request_with_retry(request_fn, attempts=3, backoff=0.5, retry_statuses=DEFAULT_RETRY_STATUSES,
                       on_retry=None, error_body_limit=ERROR_BODY_LIMIT):
    """Выполнить HTTP-запрос с повторами. request_fn() -> response (requests.Response).

    Возвращает ответ: успешный, либо последний с не-повторяемым кодом (проверку кода делает вызывающий,
    как и раньше). Исключение пробрасывается, если попытки исчерпаны."""
    import requests
    retryable = (RetryableError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)

    def attempt():
        response = request_fn()
        status = getattr(response, "status_code", None)
        if status in retry_statuses:
            raise RetryableError(_response_detail(response, error_body_limit), status,
                                 retry_after=parse_retry_after(response))
        return response

    return retry_call(attempt, attempts=attempts, backoff=backoff,
                      retryable_exceptions=retryable, on_retry=on_retry)
