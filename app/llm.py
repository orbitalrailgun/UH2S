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

## Модель данных (важно)
- Каждая таблица данных (результат GET/LOAD) — это СПИСОК СЛОВАРЕЙ (list of dict), одна строка = один dict.
- Переменные (DEF) — скаляры/списки/словари; подставляются в параметры маркером %(имя)X.
- %(имя)X типизирует вставку: s=строка, i=int, f=float, b=bool, l=list, d=dict, x=сырьё без кавычек.
- Объединять/фильтровать/агрегировать РАЗНЫЕ таблицы удобно in-memory SQL: sqlite3_im/duckdb_im — они видят
  ранее собранные таблицы по их именам (AS) как SQL-таблицы.
- ВАЖНО (in-memory SQL): имена колонок часто содержат точки/дефисы/пробелы или спецсимволы
  (например custom_host.ip, custom_source.ip после разбора вложенных полей). Такие имена ОБЯЗАТЕЛЬНО брать в
  ДВОЙНЫЕ КАВЫЧКИ: "custom_host.ip". Без кавычек СУБД трактует точку как таблица.колонка и падает/возвращает
  пустоту. Псевдонимы задавай простыми (AS ip), тогда дальше кавычки не нужны. Сначала посмотри реальные имена
  колонок таблицы через ```run и только потом пиши SQL.
- КРИТИЧНО: параметр queries — это JSON-МАССИВ строк. Значит ВСЕ двойные кавычки ВНУТРИ SQL (кавычки
  идентификаторов "custom_host.ip") ОБЯЗАТЕЛЬНО экранировать как \\" — иначе JSON битый и запрос не
  распарсится. Пример элемента: "SELECT \\"custom_host.ip\\" AS ip FROM alerts". Одинарные кавычки строковых
  литералов SQL ('текст') экранировать НЕ нужно.

## Команды
- DEF <значение> AS <имя> — переменная (int/float/"строка"/true/false/[список]/{словарь}); можно %(имя)X внутри.
- CALC(X, Y, operation[, optional]) AS Z — PLUS/MINUS/MULT/DEV/POW; TRIM/CONCAT/SPLIT/RE_SEARCH/RE_SUBSTRING; DATETIME_FORMAT/UNIXTIME_TO_DATETIME/DATETIME_TO_UNIXTIME.
- GET <source>:<func>(параметры) AS data — вызов источника. ФУНКЦИИ И ПАРАМЕТРЫ уточняй через get_source_functions.
- GET script:<имя>(параметры) AS data — вызов сохранённого скрипта; параметры перекрывают его DEF.
- GET APPLY:<data>(<колонка> AS <x>):[<unique>] <source:func | script:name>(... %(x)s ...) AS d
    fan-out: для КАЖДОЙ строки таблицы <data> берётся <колонка> как переменная <x> и подставляется в вызов.
    <unique> — JSON-МАССИВ колонок для дедупликации результата ([] = без дедупа). На выходе к строкам
    добавляются столбцы applied_<x> (исходные значения). ВНИМАНИЕ: скобки [] обязательны.
- PRINT(имя | "текст") — markdown-вывод. SHOW(table, table|matplotlib|tree[, {params}]) — таблица/график/дерево.
  tree: {"transmit":"parent_id_col","receive":"node_id_col","title":"name_col","description":["f1","f2"]}.
- SAVE(table | [t1,t2], xlsx|csv_in_zip|json_in_zip) [AS file] — скачивание.
- LOAD(key[, ttl_ignore]) AS d / SAVE(d, storage[, ttl]) AS key — persistent-кэш (TTL).
- NOTIFY notifier("текст") — уведомление.

## Примеры
1) SQL-агрегация над собранной таблицей:
GET some_source:query(...) AS raw
| GET sqlite3_im:query(queries=["SELECT host, COUNT(*) AS cnt FROM raw GROUP BY host"]) AS agg
2) APPLY (правильный синтаксис — [] обязателен):
GET netbox:search(...) AS hosts
| GET APPLY:hosts(address AS ip):[] dns:query(target=%(ip)s) AS resolved
3) Переменная с запятыми — через DEF + %(v)l:
DEF ["a","b"] AS items | GET src:func(list=%(items)l) AS d
4) Колонки с точками в имени — В ДВОЙНЫХ КАВЫЧКАХ + простой псевдоним:
GET duckdb_im:query(queries=["SELECT ip FROM (
    SELECT \\"custom_host.ip\\" AS ip FROM alerts WHERE \\"custom_host.ip\\" IS NOT NULL
    UNION ALL SELECT \\"custom_source.ip\\" AS ip FROM alerts WHERE \\"custom_source.ip\\" IS NOT NULL
) GROUP BY ip"], type="table") AS unique_ips
"""


def build_agent_system_prompt(memory_context=None):
    """Лёгкий системный промпт агента: роль + протокол действий + краткая дока DSL.
    Каталог источников, объекты и историю агент получает ПО ЗАПРОСУ через действия
    (чтобы не раздувать контекст). memory_context — текст релевантных заметок из общей базы
    знаний (авто-инъекция); если задан, добавляется отдельной секцией «Память»."""
    sections = [
        "Ты — ассистент-агент Universal Harvester. Помогаешь пользователю писать и отлаживать "
        "DSL-скрипты и объекты. Отвечай на русском, кратко и по делу. НЕ выдумывай имена источников, "
        "функций и параметров — обязательно проверяй их действиями ниже перед использованием.",
        "# Порядок работы\n"
        "1) `list_objects` — посмотри, какие source/script РЕАЛЬНО доступны (это твои инструменты).\n"
        "2) Для нужного source узнай его тип (в скобках у list_objects) и вызови `get_source_functions <тип>` — "
        "это даёт ТОЧНЫЕ функции и параметры. НЕ вызывай функцию источника, не проверив её здесь.\n"
        "3) Пиши скрипт и проверяй через `run`. Когда рабочий — дай финал в ```harvester```.\n"
        "`list_sources` даёт лишь общий список ТИПОВ источников — обычно он НЕ нужен; начинай с `list_objects`.",
        "# Действия (по ОДНОМУ блоку за ответ; результат вернётся сообщением «РЕЗУЛЬТАТ ДЕЙСТВИЯ [...]»)\n"
        "- ```list_objects\\n[тип]``` — сохранённые объекты (имя и тип); опц. фильтр (source/script/notifier/llm). НАЧНИ С ЭТОГО.\n"
        "- ```get_source_functions\\n<тип>``` — точные функции и параметры источника данного ТИПА (обязательные/опц.).\n"
        "- ```get_object\\n<имя>``` — конфигурация источника или тело скрипта.\n"
        "- ```search_objects\\n<текст>``` — поиск по содержимому объектов (в т.ч. тел скриптов).\n"
        "- ```list_sources``` — общий список типов источников (обычно не нужно).\n"
        "- ```run\\n<скрипт>``` — выполнить скрипт как тест: вернутся статусы шагов и ВСЕ переменные и "
        "таблицы (list of dict). PRINT/SHOW/SAVE для этого НЕ нужны; SHOW/SAVE в ```run игнорируются.\n"
        "- ```save_object\\n{\"name\":\"...\",\"type\":\"script\",\"roles\":[...],\"json\":{\"script\":\"...\",\"return\":\"...\"}}``` "
        "— сохранить готовый скрипт как объект (создаётся новая версия, если объект есть). Требует ПОДТВЕРЖДЕНИЯ "
        "пользователя; используй только когда скрипт проверен через ```run и пользователь просил сохранить.\n"
        "- ```memory_save\\n{\"title\":\"...\",\"content\":\"...\",\"tags\":[...]}``` — записать заметку в ОБЩУЮ базу "
        "знаний (память команды). Сохраняется сразу, без подтверждения. Upsert по title (тот же заголовок → замена).\n"
        "- ```memory_search\\n<текст>``` — найти заметки по подстроке (title/content/tags).\n"
        "- ```memory_list``` — список заголовков всех заметок.\n"
        "- ```memory_get\\n<title|id>``` — полный текст заметки.\n"
        "- ```memory_delete\\n<title|id>``` — удалить устаревшую/ошибочную заметку.\n"
        "Сначала исследуй (list_objects/get_source_functions/get_object), затем пиши и проверяй "
        "через ```run. Когда скрипт рабочий — приведи финал в блоке ```harvester``` (в нём можно PRINT/SHOW/SAVE "
        "для пользователя), без ```run. Если действие не нужно — просто ответь текстом.",
        "# Память (база знаний команды)\n"
        "У тебя есть общая долговременная память (действия memory_*). КОНСПЕКТИРУЙ в неё переиспользуемые выводы: "
        "рабочие приёмы DSL, особенности и подводные камни источников, схемы/имена полей, готовые фрагменты запросов, "
        "устойчивые предпочтения пользователя. Пиши заметку memory_save, когда узнал что-то полезное на будущее "
        "(например, отладил нетривиальный запрос). Заметки короткие и конкретные; повторный memory_save с тем же title "
        "обновляет заметку. Перед сложной задачей полезно memory_search по теме. "
        "ЗАПРЕЩЕНО сохранять в память секреты, токены, пароли, ключи и персональные данные (PII) — память общая. "
        "Релевантные заметки могут быть уже подмешаны ниже в секции «Известные заметки».",
        "# Рецепт: исследовать источник и описать его данные в базе знаний\n"
        "Когда просят «исследуй источник и опиши данные»:\n"
        "1) `get_source_functions <тип>` — узнай доступные функции/параметры источника.\n"
        "2) Найди, ЧТО можно смотреть (перечни): у elastic_requests — `list_data_views` (data views / "
        "index patterns, настроенные в Kibana/OpenSearch Dashboards) и `list_indices` (индексы уровня ES); "
        "у SQL-источников — запрос к information_schema; у прочих — профильная перечисляющая функция.\n"
        "3) Возьми несколько сущностей (индексов/паттернов/таблиц) и через `run` вытащи МАЛЕНЬКУЮ выборку "
        "(size/limit=1..5) — по строкам увидишь реальные имена и типы полей.\n"
        "4) На каждую сущность сделай `memory_save`: title вида «<источник>: <индекс/паттерн/таблица>», в content — "
        "список ключевых полей с кратким смыслом, поле времени, характерные значения, рабочий пример запроса; "
        "tags — [имя источника, тип, домен]. Без секретов/PII (не вставляй реальные значения-идентификаторы).\n"
        "5) Кратко отчитайся пользователю, какие описания сохранил. Так следующая задача по этому источнику "
        "будет опираться на готовые заметки (они авто-подмешиваются по теме).",
        "# Рецепт: LLM-анализ и обогащение данных (объект типа llm как ИСТОЧНИК)\n"
        "Объект llm можно вызвать через GET, чтобы модель проанализировала уже собранные данные:\n"
        "- `GET <llm_объект>:line_analysis(data=\"tbl\", instructions=\"...\", [knowledge_base=true], [temp_notes=N]) AS out` — "
        "ПОСТРОЧНО: на каждую строку модель возвращает JSON, поля добавляются к строке (обогащение/триаж). "
        "Инструкция должна ЯВНО называть нужные поля и их тип (например: верни verdict — строка, accuracy — 0.0..1.0). "
        "`temp_notes=N` (целое>0) включает последовательный проход с эфемерными заметками прогона (поле `_note` видно "
        "на следующих строках, N — ширина буфера) — полезно для кластеризации/корреляции; 0 = выключено.\n"
        "- `GET <llm_объект>:data_analysis(data=\"tbl\", instructions=\"...\", [knowledge_base=true]) AS out` — "
        "весь набор одним вызовом -> `[{}]` (сводки/выводы/группировки).\n"
        "Когда что: нужно обогатить КАЖДУЮ строку новыми полями -> line_analysis; нужен общий вывод/сводка по "
        "набору -> data_analysis. `data` — имя таблицы строкой или список имён. `knowledge_base=true` — если "
        "полезны накопленные заметки. Для больших таблиц сначала урежь/агрегируй (sqlite3_im/pandas_im), "
        "иначе data_analysis упрётся в контекст. Имя llm-объекта бери из `list_objects` (тип llm).",
        "# Язык скриптов (DSL)\n" + PROJECT_DOC,
    ]
    if memory_context:
        sections.append("# Известные заметки (авто-подобранные из памяти по теме запроса)\n" + memory_context)
    return "\n\n".join(sections)


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


def _empty_usage():
    return {"prompt_tokens": 0, "completion_tokens": 0}


def llm_chat(llm_json, messages, current_state):
    """Отправить чат-запрос к LLM. Возврат (ok: bool, content_or_error: str, usage: dict),
    где usage = {prompt_tokens, completion_tokens} (для сессионного учёта; нули при ошибке).

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
        return False, "в объекте llm не задан url", _empty_usage()

    start = time.monotonic()
    try:
        if provider == "ollama":
            body = {"model": model, "messages": messages, "stream": False,
                    "options": {"num_ctx": llm_context_window(llm_json)}}
            response = requests.post(f"{url}/api/chat", json=body, headers=headers, verify=verify, timeout=timeout)
            duration_ms = int((time.monotonic() - start) * 1000)
            if response.status_code not in (200, 201):
                _log_llm_request(current_state, syslog.LOG_ERR, model, provider, url, None, None, duration_ms, f"http {response.status_code}")
                return False, f"ollama chat http {response.status_code}: {response.text[:300]}", _empty_usage()
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
                return False, f"openai chat http {response.status_code}: {response.text[:300]}", _empty_usage()
            payload = response.json()
            choices = payload.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            usage = payload.get("usage", {}) or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")

        else:
            return False, f"неизвестный тип llm '{provider}' (ollama | openai)", _empty_usage()

        # если сервер не вернул счётчики токенов — оцениваем (консервативно)
        if prompt_tokens is None:
            prompt_tokens = sum(llm_estimate_tokens(m.get("content", "")) for m in messages)
        if completion_tokens is None:
            completion_tokens = llm_estimate_tokens(content)

        _log_llm_request(current_state, syslog.LOG_INFO, model, provider, url, prompt_tokens, completion_tokens, duration_ms)
        return True, content, {"prompt_tokens": prompt_tokens or 0, "completion_tokens": completion_tokens or 0}

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        _log_llm_request(current_state, syslog.LOG_ERR, model, provider, url, None, None, duration_ms, f"fail: {str(e)}")
        return False, f"llm chat fail: {str(e)}", _empty_usage()


def llm_chat_stream(llm_json, messages, current_state, on_chunk):
    """Стриминговый чат: вызывает on_chunk(delta) по мере генерации. Возврат (ok, full_content, usage).
    ollama -> /api/chat NDJSON (message.content, done); openai -> /chat/completions SSE (choices[].delta.content).
    При любой ошибке возвращает (False, error, usage) — вызывающий код делает фолбэк на llm_chat."""
    import requests
    import json as _json
    import time
    provider = (llm_json.get("type") or "ollama").strip().lower()
    url = (llm_json.get("url") or "").rstrip("/")
    model = llm_json.get("model", "")
    timeout = llm_json.get("request_timeout", 120)
    verify = llm_json.get("verify", True)
    headers = _llm_headers(llm_json, current_state)
    if not url:
        return False, "в объекте llm не задан url", _empty_usage()

    start = time.monotonic()
    content_parts = []
    prompt_tokens = completion_tokens = None
    try:
        if provider == "ollama":
            body = {"model": model, "messages": messages, "stream": True,
                    "options": {"num_ctx": llm_context_window(llm_json)}}
            endpoint = f"{url}/api/chat"
        elif provider in ("openai", "openai_compatible"):
            body = {"model": model, "messages": messages, "stream": True}
            endpoint = f"{url}/chat/completions"
        else:
            return False, f"неизвестный тип llm '{provider}' (ollama | openai)", _empty_usage()

        with requests.post(endpoint, json=body, headers=headers, verify=verify, timeout=timeout, stream=True) as response:
            if response.status_code not in (200, 201):
                duration_ms = int((time.monotonic() - start) * 1000)
                _log_llm_request(current_state, syslog.LOG_ERR, model, provider, url, None, None, duration_ms, f"http {response.status_code}")
                return False, f"{provider} stream http {response.status_code}: {response.text[:300]}", _empty_usage()
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if provider in ("openai", "openai_compatible"):
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                try:
                    chunk = _json.loads(line)
                except BaseException:
                    continue
                if provider == "ollama":
                    delta = (chunk.get("message") or {}).get("content", "")
                    if delta:
                        content_parts.append(delta)
                        on_chunk(delta)
                    if chunk.get("done"):
                        prompt_tokens = chunk.get("prompt_eval_count")
                        completion_tokens = chunk.get("eval_count")
                else:
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {}).get("content", "")
                        if delta:
                            content_parts.append(delta)
                            on_chunk(delta)
                    usage = chunk.get("usage") or {}
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage.get("completion_tokens", completion_tokens)

        content = "".join(content_parts)
        if prompt_tokens is None:
            prompt_tokens = sum(llm_estimate_tokens(m.get("content", "")) for m in messages)
        if completion_tokens is None:
            completion_tokens = llm_estimate_tokens(content)
        duration_ms = int((time.monotonic() - start) * 1000)
        _log_llm_request(current_state, syslog.LOG_INFO, model, provider, url, prompt_tokens, completion_tokens, duration_ms, "stream")
        return True, content, {"prompt_tokens": prompt_tokens or 0, "completion_tokens": completion_tokens or 0}

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        _log_llm_request(current_state, syslog.LOG_ERR, model, provider, url, None, None, duration_ms, f"stream fail: {str(e)}")
        return False, f"llm stream fail: {str(e)}", _empty_usage()


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
