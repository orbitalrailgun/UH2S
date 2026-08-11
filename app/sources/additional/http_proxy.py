"""Параметры прокси для `requests` из конфигурации объекта (source/llm/notifier).

Зачем: по умолчанию `requests` подхватывает прокси из окружения (`HTTP_PROXY`/`HTTPS_PROXY`), и во
контурах, где часть систем доступна напрямую, запрос уходит в прокси и падает. Явный `proxies` в
конфиге объекта решает обе задачи — и задать прокси, и обойти его для конкретного источника.

Поддерживаемые ключи в json объекта:
  "proxies": {"http": "http://proxy:3128", "https": "http://proxy:3128"}   — как есть в requests
  "proxies": {"http": "", "https": ""}     — пустые значения ОТКЛЮЧАЮТ прокси (переданное значение
                                             имеет приоритет над окружением, а пустая строка для
                                             requests означает «без прокси»)
  "proxy": "http://proxy:3128"             — сокращение: один адрес на http и https
  "no_proxy": true                         — сокращение для {"http": "", "https": ""}

Словарь `proxies` передаётся в requests БЕЗ фильтрации ключей: кроме http/https/no_proxy requests
понимает схемы вида "http://host" (per-host прокси), а незнакомые ключи просто игнорирует.
Если ничего не задано — возвращается None, и вызов идёт как раньше (окружение учитывается).
"""


def proxies_from_source(source):
    """dict для параметра `proxies` библиотеки requests или None, если в конфиге ничего не задано."""
    if not isinstance(source, dict):
        return None
    proxies = source.get("proxies")
    if isinstance(proxies, dict) and proxies:
        return {str(key): ("" if value is None else str(value)) for key, value in proxies.items()}
    single = source.get("proxy")
    if isinstance(single, str) and single.strip():
        return {"http": single.strip(), "https": single.strip()}
    if source.get("no_proxy") is True:
        return {"http": "", "https": ""}
    return None


def proxy_kwargs(source):
    """`{"proxies": {...}}` для распаковки в вызов requests, либо пустой dict.

    Пустой dict — чтобы у источников без настройки прокси вызов остался буквально прежним."""
    proxies = proxies_from_source(source)
    return {"proxies": proxies} if proxies is not None else {}
