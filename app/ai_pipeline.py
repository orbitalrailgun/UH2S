"""Чистые (без nicegui/БД) хелперы AI-агента: разбор действий/финала и аргумента save_object.
Вынесены отдельно для офлайн-тестируемости (импортируются в app/interface.py)."""

import re
import json

# Действия агента (по одному блоку ```<action> ...``` за ответ). ```harvester``` — не действие.
AGENT_ACTIONS = ("run", "list_sources", "get_source_functions", "list_objects",
                 "search_objects", "get_object", "save_object",
                 "memory_save", "memory_search", "memory_list", "memory_get", "memory_delete")


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


def parse_memory_save(argument):
    """Разобрать/провалидировать аргумент действия memory_save (JSON).
    Возврат (ok, error_or_none, normalized|None), где normalized = {title, content, tags:[...]}.
    Требуются непустые title и content; tags — список строк (по умолчанию [])."""
    try:
        data = json.loads(argument or "")
    except BaseException as e:
        return False, f"невалидный JSON: {e}", None
    if not isinstance(data, dict):
        return False, "ожидался JSON-объект {title, content, tags}", None
    title = str(data.get("title") or "").strip()
    if not title:
        return False, "не задан title", None
    content = str(data.get("content") or "").strip()
    if not content:
        return False, "не задан content", None
    tags = data.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    return True, None, {"title": title, "content": content, "tags": tags}


def _tokenize(text):
    """Слова длиной >=3 в нижнем регистре (для простого лексического матчинга заметок)."""
    return [w for w in re.findall(r"\w+", (text or "").lower()) if len(w) >= 3]


def rank_notes_by_query(notes, query, limit=5):
    """Ранжировать заметки по релевантности запросу для авто-инъекции в промпт (чистая функция).

    Скор = число совпавших уникальных слов запроса в title(*2)/tags(*2)/content. При нулевом скоре у всех
    (или пустом query) — берём самые свежие по updated_at. Возврат: список заметок (dict), не длиннее limit."""
    notes = list(notes or [])
    terms = set(_tokenize(query))

    def score(note):
        if not terms:
            return 0
        title_words = set(_tokenize(note.get("title", "")))
        tag_words = set(_tokenize(" ".join(note.get("tags", []) or [])))
        content_words = set(_tokenize(note.get("content", "")))
        return (2 * len(terms & title_words) + 2 * len(terms & tag_words) + len(terms & content_words))

    scored = [(score(n), n) for n in notes]
    if terms and any(s > 0 for s, _ in scored):
        scored = [(s, n) for s, n in scored if s > 0]
        scored.sort(key=lambda sn: (sn[0], sn[1].get("updated_at", "")), reverse=True)
    else:
        # нет совпадений/пустой запрос -> свежие
        scored.sort(key=lambda sn: sn[1].get("updated_at", ""), reverse=True)
    return [n for _, n in scored[:max(0, int(limit))]]
