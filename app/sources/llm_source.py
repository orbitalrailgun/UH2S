"""Источник данных на базе объекта типа `llm`: анализ/обогащение собранных таблиц через LLM.

GET <llm_object>:line_analysis(data="tbl", instructions="...", [knowledge_base=true])  — построчно;
GET <llm_object>:data_analysis(data="tbl", instructions="...", [knowledge_base=true]) — весь набор.

source_object здесь — это json самого llm-объекта (type/url/model/key/...), который принимает llm_chat.
Тяжёлых зависимостей нет: llm_chat (requests) импортируется лениво внутри функций."""
import re
import json
import time
import random
import syslog
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.logging import get_log_message, logger_log, currentFuncName


# Маркеры ТРАНЗИЕНТНЫХ ошибок LLM-эндпоинта (таймаут/сеть/перегрузка) — их имеет смысл повторять.
# llm_chat не бросает исключение, а возвращает (False, текст) — поэтому классифицируем по тексту.
_TRANSIENT_MARKERS = ("timed out", "timeout", "read timed out", "connection", "connectionpool",
                      "temporarily", "reset by peer", "econnreset", "429", "500", "502", "503", "504",
                      "too many requests", "overloaded", "unavailable")


def _is_transient_error(error_text):
    lowered = (error_text or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def _llm_chat_with_retry(llm_json, messages, current_state, attempts, backoff):
    """Обёртка над llm_chat с повторами на транзиентных ошибках (экспоненциальный backoff + джиттер).
    Нетранзиентные ошибки (напр. 400/401) не повторяются. Возврат — как у llm_chat: (ok, content, usage)."""
    from app.llm import llm_chat
    attempts = max(1, int(attempts))
    result = (False, "no attempts", {})
    for attempt in range(attempts):
        result = llm_chat(llm_json, messages, current_state)
        if result[0]:
            return result
        if attempt < attempts - 1 and _is_transient_error(result[1]):
            delay = backoff * (2 ** attempt) + random.uniform(0, 0.3)
            logger_log(syslog.LOG_WARNING, get_log_message(
                f"llm retry attempt {attempt + 1}/{attempts - 1} after {delay:.1f}s ({str(result[1])[:120]})",
                currentFuncName(), current_state))
            time.sleep(delay)
            continue
        break
    return result


def _retry_config(source_object):
    """Параметры повторов из конфига llm-объекта: max_retries (деф. 2) -> attempts, backoff сек (деф. 1.0)."""
    try:
        attempts = int(source_object.get("max_retries", 2)) + 1
    except (TypeError, ValueError):
        attempts = 3
    try:
        backoff = float(source_object.get("retry_backoff_seconds", 1.0))
    except (TypeError, ValueError):
        backoff = 1.0
    return attempts, backoff


def _strip_code_fences(text):
    """Убрать обёртку ```json ... ``` / ``` ... ``` вокруг ответа модели."""
    body = (text or "").strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", body, flags=re.DOTALL)
    return match.group(1).strip() if match else body


def _parse_json_object(text):
    """Извлечь JSON-объект из ответа LLM -> dict | None (толерантно к тексту вокруг/фенсам)."""
    raw = _strip_code_fences(text)
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    except BaseException:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except BaseException:
            pass
    return None


def _parse_json_array(text):
    """Извлечь JSON-массив объектов из ответа LLM -> list | None.
    Если модель вернула {"results":[...]} — берём первый вложенный список; объект -> [объект]."""
    raw = _strip_code_fences(text)
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, list):
                    return nested
            return [value]
    except BaseException:
        pass
    match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, list):
                return value
        except BaseException:
            pass
    return None


def _merge_generated(row, generated):
    """Слить сгенерированные поля в строку. Коллизия с исходным столбцом -> префикс llm_<name>."""
    merged = dict(row)
    for key, value in generated.items():
        target = key if key not in merged else f"llm_{key}"
        merged[target] = value
    return merged


def _knowledge_context(query_text, current_state, limit=5):
    """Релевантные заметки из общей базы знаний для подмешивания в промпт (read-only). Ошибки не роняют анализ."""
    try:
        from app.db import knowledge_list
        from app.ai_pipeline import rank_notes_by_query
        list_result = knowledge_list(current_state)
        if not list_result[0]:
            return ""
        notes = rank_notes_by_query(list_result[3], query_text, limit=limit)
        if not notes:
            return ""
        lines = []
        for note in notes:
            snippet = " ".join(str(note.get("content") or "").split())[:400]
            lines.append(f"- {note.get('title')}: {snippet}")
        return "\n".join(lines)
    except BaseException:
        return ""


# поле в JSON-ответе, куда модель кладёт временную заметку для последующих строк (не попадает в столбцы)
_NOTE_FIELD = "_note"


def _system_prompt(instructions, knowledge, per_line, temp_notes=False):
    """Системный промпт для line/data анализа: строгий JSON-вывод + инструкция + опц. заметки/scratchpad."""
    if per_line:
        head = ("Ты анализируешь ОДНУ строку данных (JSON-объект). Выполни инструкцию и верни РОВНО ОДИН "
                "JSON-объект ТОЛЬКО с новыми полями (исходные поля не повторяй). Никакого текста вне JSON.")
    else:
        head = ("Ты анализируешь НАБОР данных (JSON-массив объектов). Выполни инструкцию и верни РОВНО ОДИН "
                "JSON-массив объектов. Никакого текста вне JSON.")
    parts = [head, f"Инструкция:\n{instructions}"]
    if knowledge:
        parts.append("Справочные заметки из базы знаний (используй при необходимости):\n" + knowledge)
    if per_line and temp_notes:
        parts.append(
            f"Строки обрабатываются ПО ПОРЯДКУ. Ты можешь вести временные заметки в рамках этого прогона: "
            f"добавь в свой JSON НЕОБЯЗАТЕЛЬНОЕ строковое поле \"{_NOTE_FIELD}\" — краткое наблюдение, полезное "
            f"для последующих строк (напр. замеченный паттерн, кандидат в кластер). Это поле — служебное, оно "
            f"НЕ станет столбцом результата. Ранее сделанные заметки будут переданы тебе в блоке «Заметки прогона».")
    return "\n\n".join(parts)


def _extract_note(generated):
    """Вынуть служебное поле _note из JSON-ответа строки -> (note_str|None, generated_без__note).
    Заметка не должна попадать в столбцы результата."""
    if _NOTE_FIELD not in generated:
        return None, generated
    remainder = {k: v for k, v in generated.items() if k != _NOTE_FIELD}
    note = generated.get(_NOTE_FIELD)
    if note is None or (isinstance(note, str) and not note.strip()):
        return None, remainder
    return (note if isinstance(note, str) else json.dumps(note, ensure_ascii=False)), remainder


def _scratchpad_block(scratchpad):
    """Текстовый блок накопленных заметок прогона для подмешивания в промпт строки."""
    if not scratchpad:
        return ""
    lines = "\n".join(f"- {note}" for note in scratchpad)
    return f"Заметки прогона (сделаны на предыдущих строках):\n{lines}"


def _resolve_table(parameters, data_map, func_name, current_state):
    """Достать входные данные по параметру data. Принимает имя таблицы строкой ("tbl") ИЛИ список
    имён (["t1","t2"]) — строки нескольких таблиц склеиваются. Возврат (ok, error_or_none, rows)."""
    data_param = parameters.get("data")
    if isinstance(data_param, str) and data_param:
        names = [data_param]
    elif isinstance(data_param, list) and data_param:
        names = [str(name) for name in data_param]
    else:
        return False, "не задан параметр data (имя собранной таблицы строкой или список имён)", None

    rows = []
    for name in names:
        table = data_map.get(name)
        if table is None:
            return False, f"таблица '{name}' не найдена среди собранных данных", None
        if not isinstance(table, list):
            return False, f"'{name}' не является таблицей (ожидается list of dict)", None
        rows.extend(table)
    return True, None, rows


def _workers(source_object, current_state):
    try:
        return max(1, int(source_object.get("max_threads") or current_state.get("processes", 4)))
    except (TypeError, ValueError):
        return 4


def _notes_width(parameters):
    """Ширина буфера временных заметок из параметра temp_notes (int). 0/отрицательное/невалидное -> 0
    (заметки выключены). Совместимость: булев True трактуем как небольшую ширину по умолчанию."""
    value = parameters.get("temp_notes", 0)
    if isinstance(value, bool):
        return 30 if value else 0
    try:
        width = int(value)
    except (TypeError, ValueError):
        return 0
    return width if width > 0 else 0


def execute_llm_line_analysis(parameters, source_object, data_map, current_state):
    """Построчный анализ: на КАЖДУЮ строку data — вызов LLM; JSON-ответ добавляется полями к строке.
    На выходе исходная таблица + новые столбцы. Ошибка строки не роняет прогон (поле llm_error).

    temp_notes=<int N> (>0) -> ПОСЛЕДОВАТЕЛЬНЫЙ проход с run-scoped заметками: модель может класть в ответ
    поле "_note", видимое на следующих строках (кластеризация/корреляция); N — ширина буфера (сколько
    последних заметок держать). temp_notes=0 (по умолчанию) -> заметки выключены, строки независимы и
    обрабатываются ПАРАЛЛЕЛЬНО (max_threads)."""
    try:
        instructions = parameters.get("instructions") or ""
        use_kb = bool(parameters.get("knowledge_base", False))
        notes_width = _notes_width(parameters)
        use_notes = notes_width > 0
        ok, err, rows = _resolve_table(parameters, data_map, currentFuncName(), current_state)
        if not ok:
            logger_log(syslog.LOG_ERR, get_log_message(f"line_analysis: {err}", currentFuncName(), current_state))
            return False, err, currentFuncName(), []
        if len(rows) == 0:
            return True, "empty input", currentFuncName(), []

        knowledge = _knowledge_context(instructions, current_state) if use_kb else ""
        system_prompt = _system_prompt(instructions, knowledge, per_line=True, temp_notes=use_notes)
        llm_json = source_object  # json llm-объекта, пригодный для llm_chat
        attempts, backoff = _retry_config(source_object)

        def call_row(row, scratchpad_text):
            user_content = json.dumps(row, ensure_ascii=False, default=str)
            if scratchpad_text:
                user_content = scratchpad_text + "\n\nСтрока для анализа:\n" + user_content
            messages = [{"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}]
            call_ok, content, _usage = _llm_chat_with_retry(llm_json, messages, current_state, attempts, backoff)
            if not call_ok:
                return {**row, "llm_error": str(content)}, None
            generated = _parse_json_object(content)
            if generated is None:
                return {**row, "llm_error": "не удалось разобрать JSON из ответа LLM"}, None
            note, generated = _extract_note(generated) if use_notes else (None, generated)
            return _merge_generated(row, generated), note

        # temp_notes -> последовательно, накапливая заметки; иначе -> параллельно (строки независимы)
        if use_notes:
            results = []
            scratchpad = []
            for row in rows:
                merged, note = call_row(row, _scratchpad_block(scratchpad))
                results.append(merged)
                if note:
                    scratchpad.append(note)
                    if len(scratchpad) > notes_width:
                        scratchpad = scratchpad[-notes_width:]
            return True, "OK", currentFuncName(), results

        results = [None] * len(rows)
        with ThreadPoolExecutor(max_workers=_workers(source_object, current_state)) as pool:
            futures = {pool.submit(call_row, row, ""): i for i, row in enumerate(rows)}
            for future in as_completed(futures):
                index = futures[future]
                merged, _note = future.result()
                results[index] = merged

        return True, "OK", currentFuncName(), results

    except BaseException as e:
        error_message = f"llm line_analysis fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_llm_data_analysis(parameters, source_object, data_map, current_state):
    """Анализ всего набора одним вызовом: на выходе [{}] по инструкции (сводки/выводы/агрегаты)."""
    try:
        instructions = parameters.get("instructions") or ""
        use_kb = bool(parameters.get("knowledge_base", False))
        ok, err, rows = _resolve_table(parameters, data_map, currentFuncName(), current_state)
        if not ok:
            logger_log(syslog.LOG_ERR, get_log_message(f"data_analysis: {err}", currentFuncName(), current_state))
            return False, err, currentFuncName(), []

        knowledge = _knowledge_context(instructions, current_state) if use_kb else ""
        system_prompt = _system_prompt(instructions, knowledge, per_line=False)
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(rows, ensure_ascii=False, default=str)}]
        attempts, backoff = _retry_config(source_object)

        call_ok, content, _usage = _llm_chat_with_retry(source_object, messages, current_state, attempts, backoff)
        if not call_ok:
            return False, f"llm data_analysis: {content}", currentFuncName(), []
        array = _parse_json_array(content)
        if array is None:
            return False, "llm data_analysis: не удалось разобрать JSON-массив из ответа LLM", currentFuncName(), []
        records = [item if isinstance(item, dict) else {"value": item} for item in array]
        return True, "OK", currentFuncName(), records

    except BaseException as e:
        error_message = f"llm data_analysis fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
