"""Переиспользуемый помощник повторов запросов (ретраи) — чистый stdlib.

retry_call(fn, ...) повторяет вызов fn():
  - если fn выбросил исключение из retryable_exceptions, ИЛИ
  - если should_retry_result(result) вернул True (напр. «плохой» HTTP-статус).
Между попытками — экспоненциальная задержка backoff * 2**(n-1) (но не больше max_backoff),
плюс случайный джиттер. Не повторяемые исключения пробрасываются сразу.

Подходит для любых коннекторов (elastic/opensearch и др.).
"""

import random
import time


class RetryableError(Exception):
    """Сигнал «повторяемой» ошибки (напр. транзиентный HTTP-статус 429/503).

    retry_after — пауза (сек), которую назвал сервер (заголовок Retry-After): если она задана,
    retry_call ждёт именно её, а не экспоненциальный backoff, иначе повтор придёт раньше
    разрешённого и снова получит 429."""
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def retry_call(fn, attempts=3, backoff=0.5, max_backoff=30.0, jitter=0.2,
               retryable_exceptions=(RetryableError,), should_retry_result=None,
               sleep=time.sleep, on_retry=None):
    """Вызвать fn() с повторами.

    attempts            — общее число попыток (включая первую), >= 1.
    backoff             — базовая задержка (сек); n-я пауза = backoff*2**(n-1), <= max_backoff.
    jitter              — доля случайного разброса задержки [0..1].
    retryable_exceptions— кортеж классов исключений, при которых повторяем (если у исключения есть
                          атрибут retry_after — пауза берётся из него, см. RetryableError).
    should_retry_result — callable(result)->bool: повторить по «плохому» результату (опц.).
    sleep / on_retry    — для тестов/логирования (on_retry(attempt_index, error_or_result, delay)).
    Возвращает результат fn() либо пробрасывает последнее исключение."""
    attempts = max(1, int(attempts))
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
            if should_retry_result is not None and should_retry_result(result) and attempt < attempts:
                delay = _delay(attempt, backoff, max_backoff, jitter)
                if on_retry:
                    on_retry(attempt, result, delay)
                sleep(delay)
                continue
            return result
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt >= attempts:
                raise
            delay = _delay(attempt, backoff, max_backoff, jitter)
            server_delay = getattr(exc, "retry_after", None)     # Retry-After приоритетнее backoff
            if server_delay is not None:
                try:
                    delay = max(0.0, float(server_delay))
                except (TypeError, ValueError):
                    pass
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    # сюда попадаем, только если последний повтор был по результату
    if last_exc is not None:
        raise last_exc
    return result


def _delay(attempt, backoff, max_backoff, jitter):
    base = min(backoff * (2 ** (attempt - 1)), max_backoff)
    if jitter:
        base = base * (1.0 + random.uniform(-jitter, jitter))
    return max(0.0, base)
