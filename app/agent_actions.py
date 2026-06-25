"""Переиспользуемые capability-функции Harvester (без UI), возвращают структуры (JSON-able).

Используются MCP-сервером. Учитывают роли пользователя из current_state['roles']
(fullmaster видит всё).

Модель данных Harvester (важно для агентов/MCP):
  source_object (сконфигурированный объект type='source', со своим ИМЕНЕМ) ->
  source_type (тип коннектора = source_object.json['type']) -> functions (функции типа).
В DSL источник вызывается ПО ИМЕНИ source-объекта:  GET <source_object_name>:<function>(...).
"""

import json

from app.engine import command_parser
from app.db import get_all_actual_objects, get_actual_object_by_name, search_actual_objects


def role_allowed(current_state, object_roles):
    """Доступен ли объект текущему пользователю по ролям."""
    roles = current_state.get("roles", []) or []
    return "fullmaster" in roles or any(r in (object_roles or []) for r in roles)


def script_params_list(current_state, script_json):
    """Параметры скрипта (DEF) как list[{name, default}]."""
    body = (script_json or {}).get("script", "")
    parsed = command_parser(body, current_state)
    return [{"name": c["variable_name"], "default": c.get("variable_value")}
            for c in parsed if c.get("command") == "DEF" and "variable_name" in c]


def _enrich_object(current_state, name, type_):
    """Доп. поля по объекту: для source — source_type; для script — params/return."""
    extra = {}
    if type_ in ("source", "script"):
        full = get_actual_object_by_name(name, f"('{type_}')", current_state)
        if full[0]:
            obj_json = full[3].get("json", {}) or {}
            if type_ == "source":
                extra["source_type"] = obj_json.get("type")
            elif type_ == "script":
                extra["params"] = script_params_list(current_state, obj_json)
                extra["return"] = obj_json.get("return")
    return extra


def list_objects(current_state, type_filter=None):
    """Доступные объекты как list[dict]. Для source указывается source_type, для script — params/return."""
    result = get_all_actual_objects(current_state)
    objects = result[3] if result[0] else []
    type_filter = (type_filter or "").strip() or None
    out = []
    for o in objects:
        if not role_allowed(current_state, o.get("roles")):
            continue
        if type_filter and o.get("type") != type_filter:
            continue
        item = {"name": o["name"], "type": o.get("type", "?")}
        item.update(_enrich_object(current_state, o["name"], o.get("type")))
        out.append(item)
    return out


def search_objects(current_state, query):
    """Поиск по содержимому объектов -> list[dict] {name, type, source_type?, match}."""
    if not query:
        return []
    result = search_actual_objects(query, current_state)
    objects = result[3] if result[0] else []
    out = []
    for o in objects:
        if not role_allowed(current_state, o.get("roles")):
            continue
        raw_json = o.get("json")
        source_type = None
        try:
            parsed_json = json.loads(raw_json) if isinstance(raw_json, str) else (raw_json or {})
            if o.get("type") == "source" and isinstance(parsed_json, dict):
                source_type = parsed_json.get("type")
        except BaseException:
            pass
        item = {"name": o["name"], "type": o.get("type", "?"),
                "match": " ".join(str(raw_json or "").split())[:200]}
        if source_type is not None:
            item["source_type"] = source_type
        out.append(item)
    return out


def get_object(current_state, name):
    """Карточка объекта -> dict {name, type, roles, json, source_type?/params?/return?} или {error}."""
    name = (name or "").strip()
    if not name:
        return {"error": "specify object name"}
    result = get_actual_object_by_name(name, "('source', 'script', 'notifier', 'llm')", current_state)
    if not result[0]:
        return {"error": f"object '{name}' not found"}
    obj = result[3]
    if not role_allowed(current_state, obj.get("roles")):
        return {"error": f"no access to object '{name}'"}
    obj_json = obj.get("json", {}) or {}
    out = {"name": name, "type": obj.get("type"), "roles": obj.get("roles"), "json": obj_json}
    if obj.get("type") == "source":
        out["source_type"] = obj_json.get("type")
    elif obj.get("type") == "script":
        out["params"] = script_params_list(current_state, obj_json)
        out["return"] = obj_json.get("return")
    return out


def run_script_structured(script_text, current_state):
    """Выполнить DSL-скрипт и вернуть структуру (JSON-able):
    {ok, print:[...], tables:{name:rows}, variables:{...}, artifacts:[...]}.
    PRINT отдаётся структурно (текст/таблица/значение). Бинарные артефакты (SHOW matplotlib / SAVE)
    только перечисляются — за их содержимым обращаться к REST POST /api/script."""
    from engine import commands_executor
    try:
        parsed = command_parser(script_text, current_state)
        parse_errors = [(i, c) for i, c in enumerate(parsed) if not c.get("parsed", True)]
        if parse_errors:
            details = "; ".join(f"#{i + 1} {c.get('command', '?')}: {c.get('parsed_comment', '?')}" for i, c in parse_errors)
            return {"ok": False, "error": f"parse errors: {details}"}

        executor_result = commands_executor(parsed, current_state)
        if not executor_result[0]:
            return {"ok": False, "error": executor_result[1]}
        variables, result_map = executor_result[3]

        def resolve_table(table_name):
            if table_name in result_map and result_map[table_name][0]:
                return result_map[table_name][3]
            if isinstance(variables.get(table_name), list):
                return variables[table_name]
            return None

        print_items = []
        artifacts = []
        for command in parsed:
            kind = command.get("command")
            if kind == "PRINT":
                arg = (command.get("print_arg") or "").strip()
                if len(arg) >= 2 and ((arg[0] == arg[-1] == '"') or (arg[0] == arg[-1] == "'")):
                    print_items.append({"type": "text", "value": arg[1:-1]})
                elif arg in result_map and result_map[arg][0]:
                    print_items.append({"type": "table", "name": arg, "rows": result_map[arg][3]})
                elif arg in variables:
                    value = variables[arg]
                    if isinstance(value, list) and (len(value) == 0 or isinstance(value[0], dict)):
                        print_items.append({"type": "table", "name": arg, "rows": value})
                    else:
                        print_items.append({"type": "value", "name": arg, "value": value})
                else:
                    print_items.append({"type": "text", "value": arg})
            elif kind == "SHOW":
                show_type = (command.get("show_type") or "table").strip().strip('"\'').lower()
                if show_type in ("matplotlib", "plot"):
                    artifacts.append({"command": "SHOW", "kind": "matplotlib_png",
                                      "table": command.get("show_table"),
                                      "note": "binary; fetch via REST POST /api/script"})
            elif kind == "SAVE":
                artifacts.append({"command": "SAVE", "kind": "file",
                                  "format": command.get("save_format"),
                                  "tables": command.get("save_tables"),
                                  "filename": command.get("save_filename"),
                                  "note": "binary; fetch via REST POST /api/script"})

        tables = {name: res[3] for name, res in result_map.items() if res[0] and isinstance(res[3], list)}
        # variables приводим к JSON-able через json round-trip с default=str
        try:
            safe_variables = json.loads(json.dumps(variables, ensure_ascii=False, default=str))
        except BaseException:
            safe_variables = {k: str(v) for k, v in (variables or {}).items()}

        return {"ok": True, "print": print_items, "tables": tables,
                "variables": safe_variables, "artifacts": artifacts}

    except BaseException as e:
        return {"ok": False, "error": str(e)}
