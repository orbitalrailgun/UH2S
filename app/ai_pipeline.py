"""Чистые (без nicegui/БД) хелперы AI-агента: разбор действий/финала и аргумента save_object.
Вынесены отдельно для офлайн-тестируемости (импортируются в app/interface.py)."""

import re
import json

# Действия агента (по одному блоку ```<action> ...``` за ответ). ```harvester``` — не действие.
AGENT_ACTIONS = ("run", "list_sources", "get_source_functions", "list_objects",
                 "search_objects", "get_object", "save_object")


def extract_action(text):
    """Первый блок-действие ```<action> ...``` -> (action, argument) или (None, None)."""
    match = re.search(r"```(" + "|".join(AGENT_ACTIONS) + r")\b[ \t]*\n?(.*?)```", text or "", flags=re.DOTALL)
    if not match:
        return None, None
    return match.group(1), match.group(2).strip()


def extract_final_harvester(text):
    """Код из последнего блока ```harvester``` (финальный скрипт агента) или None."""
    matches = re.findall(r"```harvester\b[ \t]*\n?(.*?)```", text or "", flags=re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip() or None


def parse_save_object(argument):
    """Разобрать/провалидировать аргумент действия save_object (JSON).
    Возврат (ok, error_or_none, normalized|None), где normalized = {name, type, roles, json:{script,return}}.
    Разрешён только type=script; требуется name и непустой json.script."""
    try:
        data = json.loads(argument or "")
    except BaseException as e:
        return False, f"невалидный JSON: {e}", None
    if not isinstance(data, dict):
        return False, "ожидался JSON-объект", None
    name = str(data.get("name") or "").strip()
    if not name:
        return False, "не задан name", None
    obj_type = str(data.get("type") or "script").strip().lower()
    if obj_type != "script":
        return False, "поддерживается только type=script", None
    obj_json = data.get("json")
    if not isinstance(obj_json, dict) or not str(obj_json.get("script") or "").strip():
        return False, "нужен json.script (тело скрипта)", None
    roles = data.get("roles")
    if not isinstance(roles, list):
        roles = ["fullmaster"]
    return True, None, {
        "name": name,
        "type": "script",
        "roles": roles,
        "json": {"script": obj_json["script"], "return": (obj_json.get("return") or "")},
    }
