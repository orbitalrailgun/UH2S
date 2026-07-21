"""Статический анализатор Harvester-скриптов -> Mermaid flowchart.

Строит граф потока выполнения по распарсенному скрипту:
- DEF/CALC — переменные;
- GET <source>:<func> — обращение к source-объекту;
- GET script:<name> — вызов скрипта (рекурсивно разворачивается в подграф);
- GET APPLY:... — построчный fan-out;
- PRINT/SHOW/SAVE/NOTIFY — элементы вывода.
Рёбра — по ссылкам команды на ранее объявленные имена (инъекции %(x)X, APPLY data,
имена таблиц/переменных в параметрах/SQL/выводе).
"""

import re
import json

from app.engine import command_parser
from app.db import get_actual_object_by_name

MAX_SCRIPT_DEPTH = 5  # глубина разворачивания вложенных GET script:...


def _short(value, limit=48):
    """Короткая безопасная для Mermaid-метки строка."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(str(text).split())
    text = text.replace('"', "'").replace("`", "'")
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return text


def _refs(command, known_names):
    """Имена ранее объявленных переменных/таблиц, на которые ссылается команда."""
    text = command.get("line", "") or ""
    # для GET убираем токен '<source>:<function>' из текста поиска — иначе имя переменной,
    # совпавшее с именем функции/источника (напр. DEF ... AS query при GET duckdb_im:query(...)),
    # давало бы ложное ребро. Реальные ссылки (%(query)s в параметрах, FROM query в SQL) остаются.
    if command.get("command") == "GET":
        source = command.get("source")
        function = command.get("function")
        if source and function:
            text = text.replace(f"{source}:{function}", " ")
    apply_block = command.get("apply")
    if isinstance(apply_block, dict):
        text += " " + str(apply_block.get("data", ""))
    own = command.get("data_name") or command.get("variable_name") or command.get("result_name")
    refs = []
    for name in known_names:
        if name == own:
            continue
        if re.search(r"(?<![\w]){}(?![\w])".format(re.escape(name)), text):
            refs.append(name)
    return refs


def _node_for(command):
    """(label, shape, css_class) для команды. shape: rect/round/stadium/subroutine."""
    kind = command.get("command")
    if kind == "DEF":
        return f"DEF {command.get('variable_name', '?')} = {_short(command.get('variable_value', ''))}", "round", "defc"
    if kind == "CALC":
        return f"CALC {command.get('result_name', '?')} = ({_short(command.get('operation', ''), 24)})", "round", "defc"
    if kind == "GET":
        source = command.get("source", "?")
        func = command.get("function", "?")
        data_name = command.get("data_name", "?")
        apply_prefix = "APPLY " if "apply" in command else ""
        if source == "script":
            return f"{apply_prefix}{data_name} ⟵ script:{func}", "subroutine", "scriptc"
        return f"{apply_prefix}{data_name} ⟵ {source}:{func}", "rect", "getc"
    if kind == "PRINT":
        return f"PRINT {_short(command.get('print_arg', ''))}", "stadium", "outc"
    if kind == "SHOW":
        return f"SHOW {command.get('show_table', '?')} ({command.get('show_type', '?')})", "stadium", "outc"
    if kind == "SAVE":
        tables = ", ".join(command.get("save_tables") or [])
        return f"SAVE [{_short(tables)}] ({command.get('save_format', '?')})", "stadium", "outc"
    if kind == "NOTIFY":
        return f"NOTIFY {command.get('notifier', '?')}", "stadium", "outc"
    if kind == "VALIDATE":
        return "validate", "round", "defc"
    return _short(command.get("command", "?")), "rect", "getc"


def _wrap(node_id, label, shape):
    label = label.replace('"', "'")
    if shape == "round":
        return f'{node_id}("{label}")'
    if shape == "stadium":
        return f'{node_id}(["{label}"])'
    if shape == "subroutine":
        return f'{node_id}[["{label}"]]'
    return f'{node_id}["{label}"]'


def _build_scope(script_text, current_state, ctx, depth, visited):
    """Построить узлы/рёбра одной области (скрипт). Возвращает id первого узла области (или None)."""
    parsed = command_parser(script_text, current_state)
    defs = {}            # имя -> node_id (последний объявивший)
    first_id = None

    for command in parsed:
        ctx["counter"] += 1
        node_id = f"n{ctx['counter']}"
        if first_id is None:
            first_id = node_id

        label, shape, css = _node_for(command)
        ctx["lines"].append("    " + _wrap(node_id, label, shape))
        ctx["classes"].setdefault(css, []).append(node_id)

        # рёбра от ранее объявленных имён, на которые ссылается команда (подпись = имя данных)
        for ref in _refs(command, list(defs.keys())):
            edge_label = _short(ref, 24).replace("|", "/").replace('"', "'")
            ctx["lines"].append(f'    {defs[ref]} -->|"{edge_label}"| {node_id}')

        # вложенный скрипт -> подграф
        if command.get("command") == "GET" and command.get("source") == "script":
            script_name = command.get("function", "")
            if script_name and script_name not in visited and depth < MAX_SCRIPT_DEPTH:
                obj = get_actual_object_by_name(script_name, "('script')", current_state)
                obj_json = (obj[3].get("json", {}) or {}) if obj[0] else {}
                body = obj_json.get("script")
                if body:
                    sub_id = f"sg{ctx['counter']}"
                    ctx["lines"].append(f'    subgraph {sub_id}["script: {script_name}"]')
                    # внутри субграфа — сверху вниз, как во внешнем графе (иначе Mermaid рисует LR)
                    ctx["lines"].append("    direction TB")
                    _sub_first, sub_defs = _build_scope(body, current_state, ctx, depth + 1, visited | {script_name})
                    ctx["lines"].append("    end")
                    ctx["lines"].append(f"    {node_id} --> {sub_id}")
                    # что скрипт отдаёт наружу: ребро от узла-return к вызывающему узлу (в его data_name)
                    return_name = obj_json.get("return")
                    if return_name and return_name in sub_defs:
                        ret_label = _short(return_name, 24).replace("|", "/").replace('"', "'")
                        ctx["lines"].append(f'    {sub_defs[return_name]} -->|"return: {ret_label}"| {node_id}')

        # регистрируем объявляемое имя
        defined = command.get("data_name") or command.get("variable_name") or command.get("result_name")
        if defined:
            defs[defined] = node_id

    return first_id, defs


def build_execution_mermaid(script_text, current_state):
    """Mermaid flowchart потока выполнения скрипта (со вложенными скриптами)."""
    ctx = {"counter": 0, "lines": [], "classes": {}}
    _build_scope(script_text or "", current_state, ctx, depth=0, visited=set())

    # init-директива: светлые линии/стрелки + ТЁМНЫЙ фон субграфов (script: ...), чтобы блок скрипта
    # не был белым на тёмной теме (clusterBkg/clusterBorder/titleColor — переменные Mermaid для кластеров)
    out = ['%%{init: {"theme": "base", "themeVariables": {'
           '"lineColor": "#94a3b8", "clusterBkg": "#0f172a", "clusterBorder": "#475569", '
           '"titleColor": "#e5e7eb", "tertiaryColor": "#0f172a"}}}%%', "flowchart TD"]
    if not ctx["lines"]:
        out.append('    empty["(пустой скрипт)"]')
        return "\n".join(out)
    out.extend(ctx["lines"])
    # стили категорий
    out.append("    classDef defc fill:#1f2937,stroke:#60a5fa,color:#e5e7eb;")
    out.append("    classDef getc fill:#064e3b,stroke:#34d399,color:#e5e7eb;")
    out.append("    classDef scriptc fill:#3730a3,stroke:#a5b4fc,color:#e5e7eb;")
    out.append("    classDef outc fill:#7c2d12,stroke:#fdba74,color:#ffffff;")
    for css, ids in ctx["classes"].items():
        if ids:
            out.append(f"    class {','.join(ids)} {css};")
    return "\n".join(out)
