import syslog
from app.logging import get_log_message, logger_log, currentFuncName
from app.db import get_secret, create_ai_log_entry

# Объект типа llm (раздел Objects):
# {
#   "type": "ollama" | "openai",          # провайдер: Ollama API или OpenAI-совместимый
#   "url": "...",                          # см. ниже про точность url
#   "model": "llama3.2",                   # имя модели
#   "context_window": 8192,                # размер контекстного окна (токенов) — для бюджета промпта агента
#   "request_timeout": 60,                 # таймаут запроса, сек
#   "verify": true,                        # проверять TLS
#   "key": {"system": "...", "account": "..."}  # опц.: секрет -> Bearer (нужен для openai)
# }
#
# ВАЖНО про url:
#   ollama -> базовый URL без путей, напр. "http://host:11434"
#             (функции обращаются к {url}/api/tags, {url}/api/chat)
#   openai -> URL ДОЛЖЕН включать /v1, напр. "https://foundation-models.api.cloud.ru/v1"
#             (функции обращаются к {url}/models, {url}/chat/completions — без добавления /v1)


DEFAULT_CONTEXT_WINDOW = 8192


def llm_context_window(llm_json):
    """Размер контекстного окна (токены) из объекта llm; дефолт при отсутствии/ошибке."""
    try:
        context_window = int(llm_json.get("context_window", DEFAULT_CONTEXT_WINDOW))
        return context_window if context_window > 0 else DEFAULT_CONTEXT_WINDOW
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_WINDOW


def llm_estimate_tokens(text):
    """Грубая (консервативная) оценка числа токенов: ~3 символа/токен.

    Без внешнего токенайзера; для кириллицы токенов обычно больше, поэтому оценка
    намеренно завышена — безопаснее для бюджетирования (не превысить контекст)."""
    if not text:
        return 0
    return max(1, len(str(text)) // 3)


def llm_truncate_to_tokens(text, max_tokens):
    """Грубое усечение текста под бюджет токенов (по символам, ~3 симв/токен)."""
    if max_tokens is None or max_tokens <= 0:
        return ""
    text = str(text)
    max_chars = max_tokens * 3
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…[truncated]"


# Краткая справка по DSL для системного промпта агента (компактно, чтобы влезать в контекст).
PROJECT_DOC = """\
Команды разделяются '|'. Комментарии /* ... */. Результат шага именуется через AS.
- DEF <значение> AS <имя> — переменная (int/float/"строка"/true/false/[список]/{словарь}).
- CALC(X, Y, operation[, optional]) AS Z — PLUS/MINUS/MULT/DEV/POW; TRIM/CONCAT/SPLIT/RE_SEARCH/RE_SUBSTRING; DATETIME_FORMAT/UNIXTIME_TO_DATETIME/DATETIME_TO_UNIXTIME.
- GET <source>:<func>(параметры) AS data — вызов источника (коннектора).
- GET script:<имя>(параметры) AS data — вызов сохранённого скрипта; параметры перекрывают его DEF.
- GET APPLY:<data>(col AS x):[unique] <source:func | script:name>(... %(x)s ...) AS d — построчный fan-out.
- PRINT(имя | "текст") — markdown-вывод. SHOW(table, table|matplotlib[, {params}]) — таблица/график.
- SAVE(table | [t1,t2], xlsx|csv_in_zip|json_in_zip) [AS file] — скачивание.
- NOTIFY notifier("текст") — уведомление.
Подстановка переменных в параметры: %(имя)X, где X = s/i/f/b/l/d/x. Значения с запятыми задавать через DEF + %(v)d.
In-memory SQL поверх собранных данных: sqlite3_im:query(queries=[...]), duckdb_im:query(type="table", queries=[...]).
"""


def build_agent_system_prompt():
    """Лёгкий системный промпт агента: роль + протокол действий + краткая дока DSL.
    Каталог источников, объекты и историю агент получает ПО ЗАПРОСУ через действия
    (чтобы не раздувать контекст)."""
    return "\n\n".join([
        "Ты — ассистент-агент Universal Harvester. Помогаешь пользователю писать и отлаживать "
        "DSL-скрипты и объекты. Отвечай на русском, кратко и по делу. Не выдумывай имена объектов "
        "и параметры — проверяй их действиями ниже.",
        "# Действия (по ОДНОМУ блоку за ответ; результат вернётся сообщением «РЕЗУЛЬТАТ ДЕЙСТВИЯ [...]»)\n"
        "- ```list_sources``` — список типов источников.\n"
        "- ```get_source_functions\\n<тип>``` — параметры конфигурации и функций конкретного источника "
        "(обязательные/опц.).\n"
        "- ```list_objects\\n[тип]``` — сохранённые объекты (имя и тип); опц. фильтр по типу (source/script/notifier/llm).\n"
        "- ```search_objects\\n<текст>``` — поиск по содержимому объектов (в т.ч. тел скриптов).\n"
        "- ```get_object\\n<имя>``` — конфигурация источника или тело скрипта.\n"
        "- ```run\\n<скрипт>``` — выполнить скрипт как тест: вернутся статусы шагов и ВСЕ переменные и "
        "таблицы (list of dict). PRINT/SHOW/SAVE для этого НЕ нужны; SHOW/SAVE в ```run игнорируются.\n"
        "Сначала исследуй (list_sources/get_source_functions/list_objects/get_object), затем пиши и проверяй "
        "через ```run. Когда скрипт рабочий — приведи финал в блоке ```harvester``` (в нём можно PRINT/SHOW/SAVE "
        "для пользователя), без ```run. Если действие не нужно — просто ответь текстом.",
        "# Язык скриптов (DSL)\n" + PROJECT_DOC,
    ])


def llm_build_messages(system_prompt, conversation, context_window):
    """Собрать messages под бюджет контекста: system (усечён) + свежие реплики, сколько влезает."""
    reserve_for_answer = max(512, context_window // 4)
    budget = max(1024, context_window - reserve_for_answer)
    system_prompt = llm_truncate_to_tokens(system_prompt, max(256, budget // 2))

    used = llm_estimate_tokens(system_prompt)
    kept = []
    for message in reversed(conversation):
        message_tokens = llm_estimate_tokens(message.get("content", ""))
        if used + message_tokens > budget:
            break
        kept.append(message)
        used += message_tokens
    return [{"role": "system", "content": system_prompt}] + list(reversed(kept))


def _log_llm_request(current_state, level, model, provider, url, prompt_tokens, completion_tokens, duration_ms, note=""):
    """Структурное логирование LLM-запроса: модель, провайдер, токены вход/выход, длительность.
    Пользователь подставляется get_log_message из current_state['username']."""
    message = (f"llm request: model={model} provider={provider} "
               f"prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} duration_ms={duration_ms} {note}").strip()
    log = get_log_message(message, currentFuncName(), current_state)
    log["event"] = "llm_request"
    log["llm_model"] = model
    log["llm_provider"] = provider
    log["llm_url"] = url
    log["prompt_tokens"] = prompt_tokens
    log["completion_tokens"] = completion_tokens
    log["total_tokens"] = (prompt_tokens or 0) + (completion_tokens or 0)
    log["duration_ms"] = duration_ms
    logger_log(level, log)
    # запись в журнал ai_log (для раздела Settings → AI). Не роняем запрос при ошибке журналирования.
    try:
        create_ai_log_entry(current_state.get("username", "unknown"), model, provider,
                            prompt_tokens, completion_tokens, duration_ms, level != syslog.LOG_ERR, current_state)
    except BaseException:
        pass


def llm_chat(llm_json, messages, current_state):
    """Отправить чат-запрос к LLM. Возврат (ok: bool, content_or_error: str).

    ollama -> POST {url}/api/chat (options.num_ctx = context_window);
    openai -> POST {url}/chat/completions (url уже включает /v1).
    Каждый запрос логируется (модель, провайдер, токены вход/выход, длительность, пользователь)."""
    import requests
    import time
    provider = (llm_json.get("type") or "ollama").strip().lower()
    url = (llm_json.get("url") or "").rstrip("/")
    model = llm_json.get("model", "")
    timeout = llm_json.get("request_timeout", 120)
    verify = llm_json.get("verify", True)
    headers = _llm_headers(llm_json, current_state)

    if not url:
        return False, "в объекте llm не задан url"

    start = time.monotonic()
    try:
        if provider == "ollama":
            body = {"model": model, "messages": messages, "stream": False,
                    "options": {"num_ctx": llm_context_window(llm_json)}}
            response = requests.post(f"{url}/api/chat", json=body, headers=headers, verify=verify, timeout=timeout)
            duration_ms = int((time.monotonic() - start) * 1000)
            if response.status_code not in (200, 201):
                _log_llm_request(current_state, syslog.LOG_ERR, model, provider, url, None, None, duration_ms, f"http {response.status_code}")
                return False, f"ollama chat http {response.status_code}: {response.text[:300]}"
            payload = response.json()
            content = payload.get("message", {}).get("content", "")
            prompt_tokens = payload.get("prompt_eval_count")
            completion_tokens = payload.get("eval_count")

        elif provider in ("openai", "openai_compatible"):
            body = {"model": model, "messages": messages, "stream": False}
            response = requests.post(f"{url}/chat/completions", json=body, headers=headers, verify=verify, timeout=timeout)
            duration_ms = int((time.monotonic() - start) * 1000)
            if response.status_code not in (200, 201):
                _log_llm_request(current_state, syslog.LOG_ERR, model, provider, url, None, None, duration_ms, f"http {response.status_code}")
                return False, f"openai chat http {response.status_code}: {response.text[:300]}"
            payload = response.json()
            choices = payload.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            usage = payload.get("usage", {}) or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")

        else:
            return False, f"неизвестный тип llm '{provider}' (ollama | openai)"

        # если сервер не вернул счётчики токенов — оцениваем (консервативно)
        if prompt_tokens is None:
            prompt_tokens = sum(llm_estimate_tokens(m.get("content", "")) for m in messages)
        if completion_tokens is None:
            completion_tokens = llm_estimate_tokens(content)

        _log_llm_request(current_state, syslog.LOG_INFO, model, provider, url, prompt_tokens, completion_tokens, duration_ms)
        return True, content

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        _log_llm_request(current_state, syslog.LOG_ERR, model, provider, url, None, None, duration_ms, f"fail: {str(e)}")
        return False, f"llm chat fail: {str(e)}"


def _llm_resolve_key(llm_json, current_state):
    """Достать токен из секрета по llm_json['key'] = {system, account}; иначе пустая строка."""
    key = llm_json.get("key")
    if isinstance(key, dict) and "system" in key and "account" in key:
        get_secret_result = get_secret(key["system"], key["account"], current_state)
        if get_secret_result[0]:
            return get_secret_result[3]
    return ""


def _llm_headers(llm_json, current_state):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f'{current_state.get("app_name", "UH")}/{current_state.get("app_version", "0")}',
    }
    token = _llm_resolve_key(llm_json, current_state)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def llm_health_check(llm_json, current_state):
    """Проверка готовности LLM-объекта. Возврат (ok: bool, message: str).

    ollama  -> GET {url}/api/tags  (+ наличие модели);
    openai  -> GET {url}/v1/models (Bearer из key)."""
    import requests
    try:
        provider = (llm_json.get("type") or "ollama").strip().lower()
        url = (llm_json.get("url") or "").rstrip("/")
        model = llm_json.get("model", "")
        timeout = llm_json.get("request_timeout", 30)
        verify = llm_json.get("verify", True)
        headers = _llm_headers(llm_json, current_state)

        if not url:
            return False, "в объекте llm не задан url"

        if provider == "ollama":
            response = requests.get(f"{url}/api/tags", headers=headers, verify=verify, timeout=timeout)
            if response.status_code != 200:
                return False, f"ollama /api/tags http {response.status_code}"
            available = [m.get("name") or m.get("model") for m in response.json().get("models", [])]
            available = [m for m in available if m]
            if model and model not in available and not any(model in m for m in available):
                shown = ", ".join(available[:10]) if available else "—"
                return False, f"ollama доступна, но модель '{model}' не найдена (есть: {shown})"
            return True, f"ollama готова, модель '{model}'"

        if provider in ("openai", "openai_compatible"):
            # url уже включает /v1 -> обращаемся к {url}/models
            response = requests.get(f"{url}/models", headers=headers, verify=verify, timeout=timeout)
            if response.status_code != 200:
                return False, f"openai {url}/models http {response.status_code} ({response.text[:200]})"
            data = response.json().get("data", [])
            ids = [m.get("id") for m in data] if isinstance(data, list) else []
            if model and ids and model not in ids:
                return False, f"openai доступна, но модель '{model}' не в списке"
            return True, f"openai-совместимый сервер готов, модель '{model}'"

        return False, f"неизвестный тип llm '{provider}' (ollama | openai)"

    except Exception as e:
        error_message = f"health check fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message
