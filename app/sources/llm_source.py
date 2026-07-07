"""Источник данных на базе объекта типа `llm`: анализ/обогащение собранных таблиц через LLM.

GET <llm_object>:line_analysis(data="tbl", instructions="...", [knowledge_base=true])  — построчно;
GET <llm_object>:data_analysis(data="tbl", instructions="...", [knowledge_base=true]) — весь набор.

source_object здесь — это json самого llm-объекта (type/url/model/key/...), который принимает llm_chat.
Тяжёлых зависимостей нет: llm_chat (requests) импортируется лениво внутри функций."""
import re
import json
import syslog
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.logging import get_log_message, logger_log, currentFuncName


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


def _system_prompt(instructions, knowledge, per_line):
    """Системный промпт для line/data анализа: строгий JSON-вывод + инструкция + опц. заметки."""
    if per_line:
        head = ("Ты анализируешь ОДНУ строку данных (JSON-объект). Выполни инструкцию и верни РОВНО ОДИН "
                "JSON-объект ТОЛЬКО с новыми полями (исходные поля не повторяй). Никакого текста вне JSON.")
    else:
        head = ("Ты анализируешь НАБОР данных (JSON-массив объектов). Выполни инструкцию и верни РОВНО ОДИН "
                "JSON-массив объектов. Никакого текста вне JSON.")
    parts = [head, f"Инструкция:\n{instructions}"]
    if knowledge:
        parts.append("Справочные заметки из базы знаний (используй при необходимости):\n" + knowledge)
    return "\n\n".join(parts)


def _resolve_table(parameters, data_map, func_name, current_state):
    """Достать входную таблицу по параметру data. Возврат (ok, error_or_none, rows)."""
    data_name = parameters.get("data")
    if not isinstance(data_name, str) or not data_name:
        return False, "не задан параметр data (имя собранной таблицы, строкой)", None
    rows = data_map.get(data_name)
    if rows is None:
        return False, f"таблица '{data_name}' не найдена среди собранных данных", None
    if not isinstance(rows, list):
        return False, f"'{data_name}' не является таблицей (ожидается list of dict)", None
    return True, None, rows


def _workers(source_object, current_state):
    try:
        return max(1, int(source_object.get("max_threads") or current_state.get("processes", 4)))
    except (TypeError, ValueError):
        return 4


def execute_llm_line_analysis(parameters, source_object, data_map, current_state):
    """Построчный анализ: на КАЖДУЮ строку data — независимый вызов LLM; JSON-ответ добавляется полями
    к строке. На выходе исходная таблица + новые столбцы. Ошибка строки не роняет прогон (поле llm_error)."""
    from app.llm import llm_chat
    try:
        instructions = parameters.get("instructions") or ""
        use_kb = bool(parameters.get("knowledge_base", False))
        ok, err, rows = _resolve_table(parameters, data_map, currentFuncName(), current_state)
        if not ok:
            logger_log(syslog.LOG_ERR, get_log_message(f"line_analysis: {err}", currentFuncName(), current_state))
            return False, err, currentFuncName(), []
        if len(rows) == 0:
            return True, "empty input", currentFuncName(), []

        knowledge = _knowledge_context(instructions, current_state) if use_kb else ""
        system_prompt = _system_prompt(instructions, knowledge, per_line=True)
        llm_json = source_object  # json llm-объекта, пригодный для llm_chat

        def analyze(index, row):
            messages = [{"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False, default=str)}]
            call_ok, content, _usage = llm_chat(llm_json, messages, current_state)
            if not call_ok:
                return index, {**row, "llm_error": str(content)}
            generated = _parse_json_object(content)
            if generated is None:
                return index, {**row, "llm_error": "не удалось разобрать JSON из ответа LLM"}
            return index, _merge_generated(row, generated)

        results = [None] * len(rows)
        with ThreadPoolExecutor(max_workers=_workers(source_object, current_state)) as pool:
            futures = [pool.submit(analyze, i, r) for i, r in enumerate(rows)]
            for future in as_completed(futures):
                index, merged = future.result()
                results[index] = merged

        return True, "OK", currentFuncName(), results

    except BaseException as e:
        error_message = f"llm line_analysis fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_llm_data_analysis(parameters, source_object, data_map, current_state):
    """Анализ всего набора одним вызовом: на выходе [{}] по инструкции (сводки/выводы/агрегаты)."""
    from app.llm import llm_chat
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

        call_ok, content, _usage = llm_chat(source_object, messages, current_state)
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
