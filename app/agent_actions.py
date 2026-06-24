"""Переиспользуемые capability-функции Harvester (без UI).

Возвращают текст (для агентов/MCP, не для человеко-интерфейса — намеренно на английском).
Учитывают роли пользователя из current_state['roles'] (fullmaster видит всё).
Используются MCP-сервером; могут быть переиспользованы AI-агентом."""

import json

from app.engine import command_parser
from app.db import get_all_actual_objects, get_actual_object_by_name, search_actual_objects


def role_allowed(current_state, object_roles):
    """Доступен ли объект текущему пользователю по ролям."""
    roles = current_state.get("roles", []) or []
    return "fullmaster" in roles or any(r in (object_roles or []) for r in roles)


def script_params_summary(current_state, script_json):
    """Параметры скрипта = его DEF с дефолтными значениями."""
    body = (script_json or {}).get("script", "")
    parsed = command_parser(body, current_state)
    params = [f"{c['variable_name']}={json.dumps(c.get('variable_value'), ensure_ascii=False, default=str)}"
              for c in parsed if c.get("command") == "DEF" and "variable_name" in c]
    return ", ".join(params) if params else "—"


def list_objects(current_state, type_filter=None):
    """Список доступных объектов (с параметрами для скриптов). type_filter — опц. фильтр по типу."""
    result = get_all_actual_objects(current_state)
    objects = result[3] if result[0] else []
    type_filter = (type_filter or "").strip() or None
    lines = []
    for o in objects:
        if not role_allowed(current_state, o.get("roles")):
            continue
        if type_filter and o.get("type") != type_filter:
            continue
        line = f"- {o['name']} ({o.get('type', '?')})"
        if o.get("type") == "script":
            full = get_actual_object_by_name(o["name"], "('script')", current_state)
            if full[0]:
                script_json = full[3].get("json", {}) or {}
                line += f" — params (DEF): {script_params_summary(current_state, script_json)}; return: {script_json.get('return', '?')}"
        lines.append(line)
    return "\n".join(lines) if lines else "no objects"


def search_objects(current_state, query):
    """Поиск по содержимому объектов (с учётом ролей)."""
    if not query:
        return "specify search text"
    result = search_actual_objects(query, current_state)
    objects = result[3] if result[0] else []
    lines = []
    for o in objects:
        if not role_allowed(current_state, o.get("roles")):
            continue
        snippet = " ".join(str(o.get("json") or "").split())[:160]
        lines.append(f"- {o['name']} ({o.get('type', '?')}): {snippet}")
    return "\n".join(lines) if lines else "nothing found"


def get_object(current_state, name):
    """Полная карточка объекта (json), для скриптов — с параметрами (DEF)."""
    name = (name or "").strip()
    if not name:
        return "specify object name"
    result = get_actual_object_by_name(name, "('source', 'script', 'notifier', 'llm')", current_state)
    if not result[0]:
        return f"object '{name}' not found"
    obj = result[3]
    if not role_allowed(current_state, obj.get("roles")):
        return f"no access to object '{name}'"
    header = f"{name} ({obj.get('type')}):"
    if obj.get("type") == "script":
        header += f"\nparams (DEF): {script_params_summary(current_state, obj.get('json', {}))}"
    return header + "\n" + json.dumps(obj.get("json", {}), ensure_ascii=False, indent=2)
