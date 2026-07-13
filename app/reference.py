"""Справочник для редактора Harvester: каталог подсказок (команды DSL, функции источников, заметки БЗ,
сохранённые скрипты) с готовыми сниппетами вставки. Без nicegui/БД — чтобы формирование/фильтрация
тестировались офлайн. Динамические данные (реестр источников, knowledge, объекты) передаются извне.

Запись каталога (entry): {"group", "label", "signature", "snippet", "doc"}.
"""
import re
import json


def dsl_command_snippets():
    """Статический каталог команд DSL с сигнатурой и шаблоном вставки."""
    items = [
        ("DEF", "DEF <значение> AS <имя>",
         'DEF "value" AS name',
         "Переменная: int/float/\"строка\"/true/false/[список]/{словарь}; можно %(имя)X внутри."),
        ("CALC", "CALC(X, Y, operation[, opt]) AS Z",
         'CALC(a, b, "PLUS") AS result',
         "Операция задаётся 3-м аргументом. Ниже — отдельные записи по каждой операции CALC."),
        ("GET", "GET <source>:<func>(params) AS data",
         'GET source_name:function(param="value") AS data',
         "Вызов источника. Точные функции/параметры — в разделе «Функции источников»."),
        ("GET script", "GET script:<имя>(params) AS data",
         'GET script:my_script(param="value") AS data',
         "Вызов сохранённого скрипта; параметры перекрывают его DEF."),
        ("GET APPLY", "GET APPLY:<data>(<col> AS <x>):[<unique>] <src:func>(... %(x)s ...) AS d",
         'GET APPLY:hosts(address AS ip):[] dns:query(target=%(ip)s) AS resolved',
         "Fan-out по строкам таблицы. Скобки [] обязательны ([] = без дедупа)."),
        ("PRINT", "PRINT(имя | \"текст\")",
         'PRINT(data)',
         "Markdown-вывод: таблица/значение по имени или текст-комментарий в кавычках."),
        ("SHOW", "SHOW(table, table|matplotlib[, {params}])",
         'SHOW(data, table)',
         "Таблица или график."),
        ("SAVE", "SAVE(table|[t1,t2], xlsx|csv_in_zip|json_in_zip) [AS file]",
         'SAVE(data, xlsx) AS report',
         "Скачивание файла с результатом."),
        ("SAVE storage", "SAVE(data, storage[, ttl]) AS key",
         'SAVE(data, storage, 3600) AS my_key',
         "Persistent-кэш: сохранить таблицу под ключом с TTL (сек)."),
        ("LOAD", "LOAD(key[, ttl_ignore]) AS d",
         'LOAD(my_key) AS data',
         "Чтение из persistent-кэша по ключу."),
        ("NOTIFY", "NOTIFY notifier(\"текст\")",
         'NOTIFY my_notifier("текст уведомления")',
         "Отправка уведомления через объект-notifier."),
    ]
    entries = [{"group": "DSL", "label": label, "signature": signature, "snippet": snippet, "doc": doc}
               for label, signature, snippet, doc in items]
    return entries + calc_operation_snippets()


def calc_operation_snippets():
    """Отдельная запись на каждую операцию CALC — чтобы все были находимы поиском (не только PLUS).
    Формат execute_calc: CALC(X, Y, operation[, optional]) AS Z."""
    ops = [
        ("PLUS", 'CALC(a, b, "PLUS") AS sum', "Сложение чисел X+Y."),
        ("MINUS", 'CALC(a, b, "MINUS") AS diff', "Вычитание X-Y."),
        ("MULT", 'CALC(a, b, "MULT") AS product', "Умножение X*Y."),
        ("DEV", 'CALC(a, b, "DEV") AS quotient', "Деление X/Y."),
        ("POW", 'CALC(a, b, "POW") AS power', "Степень: X в степени Y (или optional)."),
        ("TRIM", 'CALC(text, "", "TRIM") AS trimmed', "Обрезка строки X; optional — набор символов (по умолчанию пробелы). Y игнорируется."),
        ("CONCAT", 'CALC(a, b, "CONCAT", " ") AS joined', "Конкатенация X+Y; optional — разделитель."),
        ("SPLIT", 'CALC(text, ",", "SPLIT") AS parts', "Разбить X по разделителю Y -> список; optional — maxsplit."),
        ("RE_SEARCH", 'CALC(text, "regex", "RE_SEARCH") AS found', "Поиск regex Y в X -> bool."),
        ("RE_SUBSTRING", 'CALC(text, "regex", "RE_SUBSTRING") AS match', "Первое совпадение regex Y в X; optional — номер группы."),
        ("DATETIME_FORMAT", 'CALC(dt, "%Y-%m-%d", "DATETIME_FORMAT", "%d.%m.%Y") AS formatted',
         "Переформатировать дату-строку X из формата Y в формат optional."),
        ("UNIXTIME_TO_DATETIME", 'CALC(ts, "%Y-%m-%d %H:%M:%S", "UNIXTIME_TO_DATETIME", "UTC") AS dt',
         "Unixtime X -> дата-строка в формате Y; optional — таймзона."),
        ("DATETIME_TO_UNIXTIME", 'CALC(dt, "%Y-%m-%d %H:%M:%S", "DATETIME_TO_UNIXTIME", "UTC") AS ts',
         "Дата-строка X формата Y -> unixtime (int); optional — таймзона."),
    ]
    return [{"group": "CALC", "label": f"CALC {op}", "signature": snippet, "snippet": snippet, "doc": doc}
            for op, snippet, doc in ops]


def format_dsl_literal(value):
    """Пример параметра -> DSL-литерал: строка в кавычках, list/dict как JSON, bool -> true/false."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return '"' + str(value) + '"'


def _params_signature(params):
    """{name: {type, example}} -> 'name:type, ...' для показа сигнатуры."""
    return ", ".join(f"{name}:{spec.get('type', 'string')}" for name, spec in (params or {}).items())


def source_function_snippet(source_type, function, required):
    """GET-сниппет с обязательными параметрами и их примерами."""
    args = ", ".join(f"{name}={format_dsl_literal(spec.get('example'))}"
                     for name, spec in (required or {}).items())
    return f"GET {source_type}:{function}({args}) AS data"


def source_function_entries(source_types, describe_fn):
    """Каталог функций источников из реестра. source_types — list[str]; describe_fn(source_type) ->
    структура describe_source_functions_struct. Возврат — список entry."""
    entries = []
    for source_type in source_types:
        described = describe_fn(source_type) or {}
        for func in described.get("functions", []) or []:
            name = func.get("function")
            required = func.get("required") or {}
            optional = func.get("optional") or {}
            req_sig = _params_signature(required)
            opt_sig = _params_signature(optional)
            doc = f"обязательные: {req_sig or '—'}"
            if opt_sig:
                doc += f" | опц.: {opt_sig}"
            entries.append({
                "group": f"source: {source_type}",
                "label": f"{source_type}:{name}",
                "signature": f"GET {source_type}:{name}({req_sig})",
                "snippet": source_function_snippet(source_type, name, required),
                "doc": doc,
            })
    return entries


def object_get_entries(objects, describe_fn):
    """GET-подсказки по РЕАЛЬНО существующим объектам (по их именам). objects — список нормализованных
    записей: {name, type, source_type?, def_params?, ret?}. describe_fn(source_type) -> структура функций.
    - type=source: для каждой функции типа источника -> GET <name>:<func>(обяз. параметры);
    - type=llm:    функции типа 'llm' (line_analysis/data_analysis) -> GET <name>:<func>(...);
    - type=script: GET script:<name>(...).
    Именно эти цели можно реально вызвать (в отличие от возможных ТИПОВ источников из реестра)."""
    entries = []
    for obj in objects or []:
        name = obj.get("name")
        obj_type = obj.get("type")
        if not name:
            continue
        if obj_type == "script":
            params = obj.get("def_params") or ""
            ret = obj.get("ret") or ""
            doc = (f"параметры: {params}" if params else "без параметров")
            if ret:
                doc += f" | return: {ret}"
            entries.append({"group": "объект: script", "label": f"script:{name}",
                            "signature": f"GET script:{name}(...)", "snippet": f"GET script:{name}() AS data", "doc": doc})
            continue
        # source / llm — резолвим функции через реестр по типу коннектора
        registry_type = obj.get("source_type") if obj_type == "source" else ("llm" if obj_type == "llm" else None)
        if not registry_type:
            continue
        described = describe_fn(registry_type) or {}
        for func in described.get("functions", []) or []:
            fname = func.get("function")
            required = func.get("required") or {}
            entries.append({
                "group": f"объект: {registry_type}",
                "label": f"{name}:{fname}",
                "signature": f"GET {name}:{fname}({_params_signature(required)})",
                "snippet": source_function_snippet(name, fname, required),
                "doc": f"реальный объект «{name}» (тип {registry_type})",
            })
    return entries


def script_object_entries(scripts):
    """Каталог сохранённых script-объектов. scripts — список {name, params_summary?, return?}."""
    entries = []
    for script in scripts or []:
        name = script.get("name")
        if not name:
            continue
        params = script.get("params_summary") or ""
        ret = script.get("return") or ""
        doc = (f"параметры: {params}" if params else "без параметров")
        if ret:
            doc += f" | return: {ret}"
        entries.append({
            "group": "script",
            "label": f"script:{name}",
            "signature": f"GET script:{name}(...)",
            "snippet": f"GET script:{name}() AS data",
            "doc": doc,
        })
    return entries


def knowledge_entries(notes):
    """Каталог заметок базы знаний. notes — список {title, content, tags}. Сниппет — комментарий-подсказка
    (заметка вставляется как /* ... */, чтобы не ломать выполнение)."""
    entries = []
    for note in notes or []:
        title = note.get("title")
        if not title:
            continue
        content = " ".join(str(note.get("content") or "").split())
        tags = ", ".join(note.get("tags") or [])
        entries.append({
            "group": "knowledge",
            "label": title,
            "signature": (f"[{tags}] " if tags else "") + content[:80],
            "snippet": f"/* {title}: {content[:200]} */",
            "doc": content[:400],
        })
    return entries


def filter_entries(entries, query):
    """Регистронезависимый поиск по подстроке в label/signature/group/doc. Пустой запрос -> все."""
    query = (query or "").strip().lower()
    if not query:
        return list(entries)
    result = []
    for entry in entries:
        haystack = " ".join([entry.get("label", ""), entry.get("signature", ""),
                             entry.get("group", ""), entry.get("doc", "")]).lower()
        if query in haystack:
            result.append(entry)
    return result


def extract_search_token(text):
    """Токен для авто-поиска подсказок из текущего ввода: последнее «слово» последней строки
    (буквы/цифры/подчёркивание/двоеточие — покрывает source:func). Пусто -> ''.
    Курсор в обёртке codemirror недоступен, поэтому берём конец текста (типовой ввод — в конце)."""
    if not text:
        return ""
    last_line = text.replace("\r", "").split("\n")[-1]
    match = re.search(r"[\w:]+$", last_line)
    return match.group(0) if match else ""


def insert_snippet(current_text, snippet):
    """Вставка сниппета в текст скрипта. Команды DSL разделяются top-level '|', переносы косметические,
    поэтому новую команду добавляем через '\\n| ', если в редакторе уже есть непустое содержимое."""
    base = current_text or ""
    if not base.strip():
        return snippet
    joiner = "\n" if base.endswith("\n") else "\n"
    return f"{base.rstrip()}{joiner}| {snippet}"
