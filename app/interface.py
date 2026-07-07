from app.login import try_login
from app.validation import check_current_user_status
from app.db import get_user_by_username, get_all_actual_objects, get_all_object_versions, get_object_by_name_and_version, get_actual_object_by_name, create_new_object_version, create_new_object, db_get_secrets_list, update_secret_comment, update_secret_secret_comment, create_secret, delete_secret, create_execution, get_executions, get_execution_by_id, search_actual_objects, get_setting, set_setting, settings_user_scope, set_user_password, update_user_metadata, get_user_session_epoch, set_user_enabled, list_users, create_user, set_user_roles, get_ai_log, get_access_networks, create_access_network, delete_access_network, create_api_key, list_api_keys, delete_api_key, set_api_key_enabled, storage_list, storage_load, storage_save, storage_delete, create_schedule, list_schedules, get_schedule, update_schedule, set_schedule_enabled, delete_schedule, knowledge_save, knowledge_search, knowledge_list, knowledge_get, knowledge_delete
from app.llm import llm_health_check, llm_context_window, build_agent_system_prompt, llm_build_messages, llm_chat, llm_chat_stream, llm_truncate_to_tokens
import syslog
import asyncio
import json
import uuid
import time
import threading
from nicegui import ui, app, Client, run
from app.logging import get_log_message, logger_log, currentFuncName, currentTimestamp
from typing import Dict, Any, Tuple
from engine import commands_executor
from app.engine import command_parser, list_source_types, describe_source_functions
from app.ai_pipeline import AGENT_ACTIONS, extract_action, extract_final_harvester, parse_save_object, parse_memory_save, rank_notes_by_query
from app.tabular import parse_table_file
from app.i18n import translate, resolve_language, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE
from app.analyzer import build_execution_mermaid
from app.validation import json_validate, validate_itemname, validate_comment, check_regex_rule, REGEX_PASSWORD_RULE, REGEX_USERNAME_RULE
# via grok
# Theme definitions

async def sleep():
    """Небольшая защита от перебора паролей, вносим искусственную задержку"""
    await asyncio.sleep(1)

# Маска секрета в таблице/форме: реальное значение в UI не показывается;
# это же значение служит сигналом "секрет не менять" при сохранении.
SECRET_MASK = "***"


# ───────────────────────── Вывод результатов Harvester (PRINT/SHOW) ─────────────────────────

def _cell_to_str(value):
    """Привести значение ячейки к строке, безопасной для markdown-таблицы."""
    if value is None:
        text = ""
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _md_escape(text):
    """Экранировать markdown-значимые символы в динамическом тексте (имена с _, * и пр.),
    чтобы системные сообщения/имена объектов не интерпретировались как разметка."""
    import re
    return re.sub(r"([\\`*_\[\]()~>#|])", r"\\\1", str(text))


def records_to_markdown(data, max_rows=200):
    """list[dict] -> markdown-таблица (или маркированный список для скаляров)."""
    if not isinstance(data, list) or len(data) == 0:
        return "_(пусто)_"
    columns, seen = [], set()
    for row in data[:max_rows]:
        if isinstance(row, dict):
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
    note = "" if len(data) <= max_rows else f"\n\n_… показано {max_rows} из {len(data)} строк_"
    if not columns:
        body = "\n".join("- " + _cell_to_str(row) for row in data[:max_rows])
        return body + note
    header = "| " + " | ".join(str(c) for c in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = ["| " + " | ".join(_cell_to_str(row.get(c, "") if isinstance(row, dict) else row) for c in columns) + " |"
            for row in data[:max_rows]]
    return "\n".join([header, separator] + rows) + note


def records_to_aggrid_options(data, aggrid_theme="ag-theme-balham-dark", max_rows=5000):
    """list[dict] -> options для ui.aggrid с фильтрами и сортировкой по каждой колонке."""
    columns, seen = [], set()
    for row in data[:max_rows]:
        if isinstance(row, dict):
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
    row_data = []
    for row in data[:max_rows]:
        if isinstance(row, dict):
            row_data.append({c: (_cell_to_str(row.get(c)) if isinstance(row.get(c), (dict, list)) else row.get(c, ""))
                             for c in columns})
        else:
            row_data.append({"value": _cell_to_str(row)})
    column_defs = [{"headerName": str(c), "field": str(c), "filter": True, "sortable": True,
                    "resizable": True, "minWidth": 160, "tooltipField": str(c)}
                   for c in columns] or [{"headerName": "value", "field": "value", "filter": True, "sortable": True, "minWidth": 160}]
    return {
        "columnDefs": column_defs,
        "rowData": row_data,
        # minWidth не даёт колонкам схлопнуться -> при множестве полей включается горизонтальный скролл,
        # заголовки и значения остаются читаемыми; тултипы показывают полное значение ячейки
        "defaultColDef": {"filter": True, "sortable": True, "resizable": True, "minWidth": 160},
        "suppressFieldDotNotation": True,
        "enableBrowserTooltips": True,
        "pagination": True,
        "paginationPageSize": 20,
        "enableCellTextSelection": True,
        "domLayout": "normal",
    }


def _resolve_plot_styles(params, plt):
    """Разрешить запрошенные стили matplotlib/SciencePlots в список доступных.

    style: строка или список ('science', 'ieee', 'nature', 'grid', 'ggplot', ...).
    SciencePlots (если установлен) регистрирует свои стили при импорте. Неизвестные
    стили молча отбрасываются, чтобы рендер не падал."""
    requested = params.get("style")
    if not requested:
        return []
    if isinstance(requested, str):
        requested = [requested]
    try:
        import scienceplots  # noqa: F401  (регистрирует стили 'science'/'ieee'/'nature'/...)
    except Exception:
        pass
    available = set(plt.style.available)
    return [s for s in requested if s in available]


def render_plot_png_b64(data, params):
    """Построить график matplotlib по данным и optional_params (см. SHOW_MATPLOTLIB.md).

    Возвращает dict {b64, css_w, css_h}: PNG с высоким dpi (резкость), css_w/css_h —
    «логический» размер для браузера (figsize в дюймах × 96px), чёткость на retina/HiDPI.

    Простой режим: kind, x, y (y может быть списком столбцов), color, title, figsize, dpi.
    Общий режим (несколько слоёв): layers=[{kind,x,y,color,label,secondary_y,stacked}, ...].
    Пороговые линии: hlines=[{y,color,label,linestyle,linewidth}], vlines=[{x,...}].
    3D-режим: kind=bar3d|scatter3d, x, y, z (z — высота/третья ось), zlabel, elev, azim, bar_width.
    Оформление: title, xlabel, ylabel, grid(bool), legend(bool), legend_loc, logx, logy, ylim, xlim, rot.
    Стиль: style='science'|['science','grid']|'ggplot'|... (SciencePlots/встроенные), rc={...} переопределения,
    usetex(bool, по умолчанию False — стиль science не требует LaTeX)."""
    import io
    import base64
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas

    figsize = params.get("figsize", [10, 5])
    try:
        dpi = int(params.get("dpi", 150))
    except (TypeError, ValueError):
        dpi = 150
    dpi = max(50, min(dpi, 400))

    dataframe = pandas.DataFrame(data)

    def _finish(fig):
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", bbox_inches="tight", dpi=dpi)
        plt.close(fig)
        b64 = base64.b64encode(buffer.getvalue()).decode()
        return {"b64": b64, "css_w": int(figsize[0] * 96), "css_h": int(figsize[1] * 96)}

    def _build():
        # --- 3D-режим (bar3d / scatter3d) — отдельная ветка, 2D-параметры не применяются ---
        kind = (params.get("kind") or "").strip().lower()
        if kind in ("bar3d", "scatter3d"):
            from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 (регистрирует projection='3d')

            def column(name):
                if name is None or name not in dataframe.columns:
                    raise ValueError(f"3D: столбец '{name}' не найден (есть: {', '.join(map(str, dataframe.columns))})")
                return dataframe[name]

            def positions(series):
                # числовой столбец -> значения как есть; категориальный -> индексы + подписи тиков
                if pandas.api.types.is_numeric_dtype(series):
                    return [float(v) for v in series.tolist()], None, None
                categories = list(dict.fromkeys(series.tolist()))
                index = {c: i for i, c in enumerate(categories)}
                return [float(index[v]) for v in series.tolist()], list(range(len(categories))), [str(c) for c in categories]

            fig = plt.figure(figsize=(figsize[0], figsize[1]))
            ax3d = fig.add_subplot(projection="3d")
            xs, x_ticks, x_labels = positions(column(params.get("x")))
            ys, y_ticks, y_labels = positions(column(params.get("y")))
            zs = [float(v) for v in column(params.get("z")).tolist()]
            color = params.get("color", "#06b6d4")

            if kind == "bar3d":
                width = params.get("bar_width", 0.5)
                ax3d.bar3d([x - width / 2 for x in xs], [y - width / 2 for y in ys], [0] * len(zs),
                           width, width, zs, color=color, shade=True, alpha=params.get("alpha", 1.0))
            else:  # scatter3d
                ax3d.scatter(xs, ys, zs, c=color, depthshade=True, alpha=params.get("alpha", 1.0))

            # размеры шрифтов (тики наезжают при крупном шрифте — даём контроль)
            tick_fs = params.get("tick_fontsize", params.get("fontsize"))
            label_fs = params.get("label_fontsize", params.get("fontsize"))
            title_fs = params.get("title_fontsize", params.get("fontsize"))
            tick_rotation = params.get("tick_rotation")
            if tick_fs is not None:
                ax3d.tick_params(labelsize=tick_fs)

            if x_labels is not None:
                ax3d.set_xticks(x_ticks)
                ax3d.set_xticklabels(x_labels, rotation=tick_rotation if tick_rotation is not None else 0)
            if y_labels is not None:
                ax3d.set_yticks(y_ticks)
                ax3d.set_yticklabels(y_labels, rotation=tick_rotation if tick_rotation is not None else 0)
            if params.get("title"):
                ax3d.set_title(params["title"], fontsize=title_fs)
            ax3d.set_xlabel(params.get("xlabel") or str(params.get("x")), fontsize=label_fs, labelpad=params.get("labelpad", 8))
            ax3d.set_ylabel(params.get("ylabel") or str(params.get("y")), fontsize=label_fs, labelpad=params.get("labelpad", 8))
            ax3d.set_zlabel(params.get("zlabel") or str(params.get("z")), fontsize=label_fs, labelpad=params.get("labelpad", 8))
            if params.get("elev") is not None or params.get("azim") is not None:
                ax3d.view_init(elev=params.get("elev"), azim=params.get("azim"))
            return _finish(fig)

        fig, ax = plt.subplots(figsize=(figsize[0], figsize[1]))
        ax_secondary = {"ax": None}

        def target_axis(layer):
            if layer.get("secondary_y"):
                if ax_secondary["ax"] is None:
                    ax_secondary["ax"] = ax.twinx()
                return ax_secondary["ax"]
            return ax

        def plot_one(spec, axis):
            kw = {"kind": spec.get("kind", "line"), "ax": axis, "legend": False}
            for key in ("x", "y", "color", "label", "stacked", "rot", "alpha", "width"):
                if spec.get(key) is not None:
                    kw[key] = spec[key]
            dataframe.plot(**kw)

        # пороговые/опорные линии можно задать как на верхнем уровне, так и внутри слоёв —
        # собираем из обоих мест, чтобы оба написания работали
        hlines = list(params.get("hlines") or [])
        vlines = list(params.get("vlines") or [])
        layers = params.get("layers")
        if layers:
            for layer in layers:
                plot_one(layer, target_axis(layer))
                hlines.extend(layer.get("hlines") or [])
                vlines.extend(layer.get("vlines") or [])
        else:
            plot_one(params, ax)

        # пороговые/опорные линии (напр. порог, выше которого bar считается превышением)
        for hl in hlines:
            ax.axhline(y=hl.get("y", 0), color=hl.get("color"), linestyle=hl.get("linestyle", "--"),
                       linewidth=hl.get("linewidth", 1.5), label=hl.get("label"))
        for vl in vlines:
            ax.axvline(x=vl.get("x", 0), color=vl.get("color"), linestyle=vl.get("linestyle", "--"),
                       linewidth=vl.get("linewidth", 1.5), label=vl.get("label"))

        # оформление
        if params.get("title"):
            ax.set_title(params["title"])
        if params.get("xlabel"):
            ax.set_xlabel(params["xlabel"])
        if params.get("ylabel"):
            ax.set_ylabel(params["ylabel"])
        if params.get("grid"):
            ax.grid(True, alpha=0.3)
        if params.get("logy"):
            ax.set_yscale("log")
        if params.get("logx"):
            ax.set_xscale("log")
        if params.get("ylim"):
            ax.set_ylim(params["ylim"])
        if params.get("xlim"):
            ax.set_xlim(params["xlim"])

        # единая легенда (объединяем основную/вторичную оси и линии-пороги)
        if params.get("legend", True):
            handles, labels = ax.get_legend_handles_labels()
            if ax_secondary["ax"] is not None:
                handles2, labels2 = ax_secondary["ax"].get_legend_handles_labels()
                handles += handles2
                labels += labels2
            named = [(h, l) for h, l in zip(handles, labels) if l and not l.startswith("_")]
            if named:
                ax.legend([h for h, _ in named], [l for _, l in named], loc=params.get("legend_loc", "best"))

        try:
            fig.autofmt_xdate()
        except Exception:
            pass
        return _finish(fig)

    # оформление science-plots / встроенные стили — скоупом, чтобы не «протекало» на следующие графики.
    # text.usetex по умолчанию выключаем: стиль 'science' иначе требует установленного LaTeX.
    styles = _resolve_plot_styles(params, plt)
    rc_overrides = {}
    if not params.get("usetex"):
        rc_overrides["text.usetex"] = False
    if isinstance(params.get("rc"), dict):
        rc_overrides.update(params["rc"])
    with plt.style.context(styles):
        with plt.rc_context(rc_overrides):
            return _build()


def _safe_filename(name):
    """Безопасное имя файла из имени таблицы (без путей и спецсимволов)."""
    import re
    cleaned = re.sub(r'[^0-9A-Za-zА-Яа-яЁё._-]+', '_', str(name)).strip('_')
    return cleaned or "data"


def _normalize_for_tabular(data):
    """Для xlsx/csv: вложенные dict/list сериализуем в JSON-строку, скаляры — как есть."""
    rows = []
    for row in data:
        if isinstance(row, dict):
            rows.append({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                         for k, v in row.items()})
        else:
            rows.append({"value": row})
    return rows


def _safe_sheet_name(name, used):
    """Имя листа Excel: <=31 симв., без \\ / ? * [ ] :, не пустое, уникальное (без учёта регистра)."""
    import re
    s = re.sub(r'[\\/?*\[\]:]', '_', str(name)).strip().strip("'")
    if not s:
        s = "sheet"
    s = s[:31]
    base = s
    i = 1
    while s.lower() in used:
        suffix = f"_{i}"
        s = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(s.lower())
    return s


def _unique_zip_name(stem, ext, used):
    """Уникальное имя файла внутри zip."""
    name = f"{stem}{ext}"
    i = 1
    while name.lower() in used:
        name = f"{stem}_{i}{ext}"
        i += 1
    used.add(name.lower())
    return name


def records_to_download(tables_data, fmt, base_name):
    """Подготовить (content_bytes, filename, media_type) для скачивания одной или нескольких таблиц.

    tables_data — dict {table_name: list_of_dicts} (порядок сохраняется).
    Форматы: xlsx (лист на таблицу) | csv_in_zip | json_in_zip (файл на таблицу в zip)."""
    import io
    fmt = (fmt or "").strip().strip('"\'').lower()
    base = _safe_filename(base_name)
    for ext in (".xlsx", ".csv.zip", ".json.zip", ".zip", ".csv", ".json"):
        if base.lower().endswith(ext):
            base = base[:-len(ext)]
            break
    base = base or "export"

    if fmt == "xlsx":
        import pandas
        buffer = io.BytesIO()
        used = set()
        with pandas.ExcelWriter(buffer, engine="openpyxl") as writer:
            for table_name, data in tables_data.items():
                sheet = _safe_sheet_name(table_name, used)
                pandas.DataFrame(_normalize_for_tabular(data)).to_excel(writer, index=False, sheet_name=sheet)
        return (buffer.getvalue(), f"{base}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if fmt == "csv_in_zip":
        import zipfile
        import pandas
        zip_buffer = io.BytesIO()
        used = set()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for table_name, data in tables_data.items():
                fname = _unique_zip_name(_safe_filename(table_name), ".csv", used)
                zf.writestr(fname, pandas.DataFrame(_normalize_for_tabular(data)).to_csv(index=False).encode("utf-8-sig"))
        return zip_buffer.getvalue(), f"{base}.csv.zip", "application/zip"

    if fmt == "json_in_zip":
        import zipfile
        zip_buffer = io.BytesIO()
        used = set()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for table_name, data in tables_data.items():
                fname = _unique_zip_name(_safe_filename(table_name), ".json", used)
                zf.writestr(fname, json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
        return zip_buffer.getvalue(), f"{base}.json.zip", "application/zip"

    raise ValueError(f"unknown format '{fmt}' (xlsx | csv_in_zip | json_in_zip)")


def execute_script_api(script_text, current_state):
    """Выполнить DSL-скрипт для API (без UI). Возвращает (ok, msg, payload), где payload:
    {"text": <PRINT и SHOW table в markdown>, "files": [(filename, bytes, media_type), ...]}.
    PRINT -> текст; SHOW matplotlib -> PNG-файл; SHOW table -> markdown-таблица в текст; SAVE -> файл(ы)."""
    try:
        parsed = command_parser(script_text, current_state)
        parse_errors = [(i, c) for i, c in enumerate(parsed) if not c.get("parsed", True)]
        if parse_errors:
            details = "; ".join(f"line {c.get('line_number', '?')} #{i + 1} {c.get('command', '?')}: {c.get('parsed_comment', '?')}" for i, c in parse_errors)
            return False, f"parse errors: {details}", currentFuncName(), None

        executor_result = commands_executor(parsed, current_state)
        if not executor_result[0]:
            return False, executor_result[1], currentFuncName(), None
        variables, result_map = executor_result[3]

        def _resolve_table(name):
            if name in result_map and result_map[name][0]:
                return result_map[name][3]
            if isinstance(variables.get(name), list):
                return variables[name]
            return None

        text_parts = []
        files = []
        plot_index = 0

        for command in parsed:
            kind = command.get("command")
            if kind == "PRINT":
                arg = (command.get("print_arg") or "").strip()
                if len(arg) >= 2 and ((arg[0] == arg[-1] == '"') or (arg[0] == arg[-1] == "'")):
                    text_parts.append(arg[1:-1])
                    continue
                if arg in result_map and result_map[arg][0]:
                    text_parts.append(records_to_markdown(result_map[arg][3]))
                elif arg in variables:
                    value = variables[arg]
                    if isinstance(value, list) and (len(value) == 0 or isinstance(value[0], dict)):
                        text_parts.append(records_to_markdown(value))
                    else:
                        text_parts.append(f"{arg} = {json.dumps(value, ensure_ascii=False, default=str)}")
                else:
                    text_parts.append(arg)

            elif kind == "SHOW":
                table = (command.get("show_table") or "").strip()
                show_type = (command.get("show_type") or "table").strip().strip('"\'').lower()
                data = _resolve_table(table)
                if not data:
                    text_parts.append(f"*SHOW: нет табличных данных «{table}»*")
                    continue
                if show_type in ("matplotlib", "plot"):
                    params = {}
                    params_raw = (command.get("show_params") or "").strip()
                    if params_raw and json_validate(params_raw):
                        params = json.loads(params_raw)
                    plot = render_plot_png_b64(data, params)
                    import base64 as _b64
                    plot_index += 1
                    files.append((f"plot_{plot_index}_{_safe_filename(table)}.png",
                                  _b64.b64decode(plot["b64"]), "image/png"))
                else:  # table -> markdown в текст
                    text_parts.append(records_to_markdown(data))

            elif kind == "SAVE":
                # SAVE→storage уже исполнен движком (запись в БД) — не паковать в файл, только статус
                if command.get("save_is_storage"):
                    text_parts.append(f"*SAVE storage: {command.get('_info', 'stored')}*")
                    continue
                tables = command.get("save_tables") or []
                fmt = (command.get("save_format") or "").strip().strip('"\'').lower()
                tables_data = {}
                missing = []
                for table in tables:
                    resolved = _resolve_table(table)
                    if resolved is None:
                        missing.append(table)
                    else:
                        tables_data[table] = resolved
                if missing:
                    text_parts.append(f"*SAVE: нет табличных данных: {', '.join(missing)}*")
                    continue
                base_name = command.get("save_filename") or (tables[0] if len(tables) == 1 else "export")
                content, filename, media_type = records_to_download(tables_data, fmt, base_name)
                files.append((filename, content, media_type))

        return True, "Ok", currentFuncName(), {"text": "\n\n".join(p for p in text_parts if p is not None), "files": files}

    except BaseException as e:
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False, str(e), currentFuncName(), None


STEP_ICONS = {"pending": "⏳", "running": "🔄", "done": "✅", "error": "❌", "rejected": "⛔", "warning": "⚠️"}

def _step_label(command):
    """Человекочитаемая подпись шага для панели прогресса выполнения.
    Префикс `LN` — номер строки в скрипте (если известен), чтобы было видно,
    к какой строке относится статус/ошибка шага."""
    kind = command.get("command", "?")

    def base():
        if kind == "VALIDATE":
            return "Валидация скрипта"
        if kind == "GET":
            cache = "CACHE " if command.get("load_cache") else ""
            prefix = "APPLY " if "apply" in command else ""
            return f"{cache}{prefix}GET {command.get('source', '?')}:{command.get('function', '?')} → {command.get('data_name', '?')}"
        if kind == "LOAD":
            return f"LOAD {command.get('load_id', '?')} → {command.get('data_name', '?')}"
        if kind == "DEF":
            return f"DEF {command.get('variable_name', '?')}"
        if kind == "PRINT":
            return f"PRINT {command.get('print_arg', '')}"
        if kind == "SHOW":
            return f"SHOW {command.get('show_table', '?')} ({command.get('show_type', '?')})"
        if kind == "SAVE":
            tables = ", ".join(command.get("save_tables", []))
            as_part = f" AS {command['save_filename']}" if command.get("save_filename") else ""
            return f"SAVE [{tables}] ({command.get('save_format', '?')}){as_part}"
        if kind == "NOTIFY":
            return f"NOTIFY {command.get('notifier', '?')}"
        if kind == "CALC":
            return f"CALC {command.get('operation', '?')}({command.get('calc_x', '?')}, {command.get('calc_y', '?')}) → {command.get('result_name', '?')}"
        return kind

    line_number = command.get("line_number")
    return f"L{line_number}: {base()}" if line_number else base()


THEMES = {
    'dark': {
        'bg': '#1F2937',
        'text': '#FFFFFF',
        'accent': '#06B6D4',
        'card': '#2D3748',
        'glow': '0 0 15px rgba(6, 182, 212, 0.3)',
        'title': '#22D3EE',
        'panel': '#2D3748',
        'header': '#111827',
        'button': '#06B6D4'
    },
    'light': {
        'bg': '#F3F4F6',
        'text': '#1F2937',
        'accent': '#3B82F6',
        'card': '#FFFFFF',
        'glow': '0 0 15px rgba(59, 130, 246, 0.2)',
        'title': '#2563EB',
        'panel': '#F9FAFB',
        'header': '#2563EB',
        'button': '#3B82F6'
    }
}

def update_theme(theme: str, color_overrides=None):
    # палитра темы + персональные оверрайды цветов (этап 2 Settings)
    palette = {**THEMES.get(theme, THEMES['dark']), **(color_overrides or {})}
    css = f"""
        :root {{
            --bg-color: {palette['bg']};
            --text-color: {palette['text']};
            --accent-color: {palette['accent']};
            --card-bg: {palette['card']};
            --glow: {palette['glow']};
            --title-color: {palette['title']};
            --panel-bg: {palette['panel']};
            --header-color: {palette.get('header', palette['panel'])};
            --button-color: {palette.get('button', palette['accent'])};
            /* Quasar primary -> цвет заполненных кнопок (.bg-primary берёт из --q-primary).
               Шапка перекрывается отдельно правилом .q-header (см. CSS). */
            --q-primary: {palette.get('button', palette['accent'])};
        }}
        html, body {{
            margin: 0;
            padding: 0;
            background: var(--bg-color);
            overflow: hidden;
        }}
        /* окраска шапки и заполненных кнопок конкретным цветом (без зависимости от --q-primary,
           т.к. в части версий Quasar .bg-primary использует зашитый цвет). Инжектится живо в #theme-style. */
        .q-header, .q-header.bg-primary {{
            background-color: {palette.get('header', palette['panel'])} !important;
        }}
        .q-btn.q-btn--standard.bg-primary, .q-btn.q-btn--actionable.bg-primary,
        .q-btn.bg-primary, .q-btn--standard.bg-primary {{
            background-color: {palette.get('button', palette['accent'])} !important;
        }}
    """
    ui.run_javascript(f"""
        let style = document.querySelector('#theme-style');
        if (!style) {{
            style = document.createElement('style');
            style.id = 'theme-style';
            document.head.appendChild(style);
        }}
        style.textContent = `{css}`;
    """)


# Варианты шрифтов для раздела Settings (значение = CSS font-family).
FONT_OPTIONS = [
    "'Orbitron', 'Roboto', sans-serif",
    "'Roboto', sans-serif",
    "system-ui, -apple-system, 'Segoe UI', sans-serif",
    "Georgia, 'Times New Roman', serif",
    "'Courier New', ui-monospace, monospace",
]

# Роли палитры, доступные для пользовательской настройки цвета (ключ THEMES -> подпись).
# glow намеренно не выносим — это CSS box-shadow, а не простой цвет.
COLOR_ROLES = [
    ("bg", "Фон"),
    ("text", "Текст"),
    ("accent", "Акцент"),
    ("card", "Карточки"),
    ("title", "Заголовки"),
    ("panel", "Панели"),
    ("header", "Шапка"),
    ("button", "Кнопки"),
]

# Значения по умолчанию для «Внешнего вида» (persist в settings, scope user:<username>).
# Темы редактора кода CodeMirror (значение = имя темы CodeMirror в NiceGUI).
CODEMIRROR_THEMES = [
    "monokai", "dracula", "oneDark", "vscodeDark", "githubDark", "solarizedDark", "basicDark",
    "vscodeLight", "githubLight", "solarizedLight", "basicLight",
]

APPEARANCE_DEFAULTS = {
    "theme": "dark",
    "font": FONT_OPTIONS[0],
    "font_size": 14,
    "table_font": FONT_OPTIONS[1],
    "table_font_size": 13,
    # оверрайды цветов по теме: {"dark": {role: "#hex", ...}, "light": {...}}
    "colors": {},
    # тема редактора кода — отдельно для тёмной и светлой темы приложения
    "codemirror_theme": {"dark": "monokai", "light": "vscodeLight"},
}


def resolve_codemirror_theme(appearance, theme):
    """Тема CodeMirror для активной темы приложения. Поддерживает и старый формат (одна строка)."""
    cm = (appearance or {}).get("codemirror_theme")
    if isinstance(cm, dict):
        return cm.get(theme) or APPEARANCE_DEFAULTS["codemirror_theme"].get(theme)
    if isinstance(cm, str) and cm:
        return cm  # legacy: одно значение на обе темы
    return APPEARANCE_DEFAULTS["codemirror_theme"].get(theme)


def apply_appearance(appearance, current_state=None):
    """Применить внешний вид на клиенте: тема + оверрайды цветов (update_theme) и шрифты/размеры через CSS-переменные.
    Действует «живо» на весь UI и текстовые блоки; таблицы (AG Grid) — через override-правило в CSS.
    Режим Quasar dark (поля/меню/чекбоксы/таб-панели) переключается через current_state['ui_dark_mode']."""
    appearance = {**APPEARANCE_DEFAULTS, **(appearance or {})}
    theme = appearance["theme"]
    color_overrides = (appearance.get("colors") or {}).get(theme, {})
    update_theme(theme, color_overrides)
    if current_state is not None:
        dark_mode = current_state.get("ui_dark_mode")
        if dark_mode is not None:
            try:
                dark_mode.value = (theme == "dark")
            except BaseException:
                pass
    try:
        font_size = int(appearance.get("font_size") or APPEARANCE_DEFAULTS["font_size"])
    except BaseException:
        font_size = APPEARANCE_DEFAULTS["font_size"]
    try:
        table_font_size = int(appearance.get("table_font_size") or APPEARANCE_DEFAULTS["table_font_size"])
    except BaseException:
        table_font_size = APPEARANCE_DEFAULTS["table_font_size"]
    font = appearance.get("font") or APPEARANCE_DEFAULTS["font"]
    table_font = appearance.get("table_font") or APPEARANCE_DEFAULTS["table_font"]
    # цвета шапки/кнопок: ставим инлайново на <html> (перебивает любые стили).
    # --q-primary использует штатное правило Quasar .bg-primary -> кнопки красятся без перезагрузки CSS.
    palette = {**THEMES.get(theme, THEMES["dark"]), **(color_overrides or {})}
    button_color = palette.get("button", palette["accent"])
    header_color = palette.get("header", palette["panel"])
    ui.run_javascript(
        "const r = document.documentElement.style;"
        f"r.setProperty('--app-font', `{font}`);"
        f"r.setProperty('--app-font-size', '{font_size}px');"
        f"r.setProperty('--app-table-font', `{table_font}`);"
        f"r.setProperty('--app-table-font-size', '{table_font_size}px');"
        f"r.setProperty('--button-color', '{button_color}');"
        f"r.setProperty('--header-color', '{header_color}');"
        f"r.setProperty('--q-primary', '{button_color}');"
    )
    # Гарантированная окраска шапки и заполненных кнопок: инлайн-стиль с !important на самих
    # элементах перебивает любые правила Quasar/nicegui. Наблюдатель красит и вновь созданные кнопки.
    color_js = (
        "window.__uhColors = {button: '%s', header: '%s'};"
        "function uhApplyColors(){"
        "  document.querySelectorAll('.q-btn.bg-primary').forEach(function(e){"
        "    e.style.setProperty('background-color', window.__uhColors.button, 'important');});"
        "  document.querySelectorAll('.q-btn--outline, .q-btn--flat').forEach(function(e){"
        "    e.style.setProperty('color', window.__uhColors.button, 'important');});"
        "  document.querySelectorAll('.q-header').forEach(function(e){"
        "    e.style.setProperty('background-color', window.__uhColors.header, 'important');});"
        "}"
        "uhApplyColors();"
        "if(!window.__uhColorObserver){"
        "  window.__uhColorObserver = new MutationObserver(function(){"
        "    if(window.__uhColorRAF) return;"
        "    window.__uhColorRAF = requestAnimationFrame(function(){window.__uhColorRAF=null; uhApplyColors();});"
        "  });"
        "  window.__uhColorObserver.observe(document.body, {childList:true, subtree:true});"
        "}"
    ) % (button_color, header_color)
    ui.run_javascript(color_js)


def make_codemirror(current_state, **kwargs):
    """Создать редактор CodeMirror с темой из настроек и зарегистрировать его для живой смены темы.
    Неизвестные в текущей версии NiceGUI kwargs (напр. line_wrapping) отбрасываются без падения."""
    theme = current_state.get("codemirror_theme") or APPEARANCE_DEFAULTS["codemirror_theme"]
    editor = None
    for attempt_kwargs in ({"theme": theme, **kwargs}, {"theme": theme}, {}):
        try:
            editor = ui.codemirror(**attempt_kwargs)
            break
        except BaseException:
            continue
    if editor is None:
        editor = ui.codemirror()
    current_state.setdefault("ui_codemirrors", []).append(editor)
    return editor


def apply_codemirror_theme(current_state, theme):
    """Живо сменить тему всех зарегистрированных редакторов CodeMirror."""
    current_state["codemirror_theme"] = theme
    for editor in current_state.get("ui_codemirrors", []) or []:
        try:
            editor.theme = theme
        except BaseException:
            pass


async def login_page(current_state: Dict[str, Any]):
    ui.add_css("""
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Roboto:wght@400;700&display=swap');
        :root {
            --bg-color: #1F2937;
            --text-color: #FFFFFF;
            --accent-color: #06B6D4;
            --card-bg: #2D3748;
            --glow: 0 0 15px rgba(6, 182, 212, 0.3);
            --title-color: #22D3EE;
            --panel-bg: #2D3748;
        }
        html, body {
            margin: 0;
            padding: 0;
            background: var(--bg-color);
            overflow: hidden;
            font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif);
            font-size: var(--app-font-size, 14px);
            letter-spacing: 1px;
        }
        /* Шрифт/размер таблиц (AG Grid) — форсируем напрямую на ячейках/заголовках,
           независимо от класса темы (var --ag-font-family на корне темы не перебивается надёжно) */
        .ag-root-wrapper, .ag-header, .ag-header-cell, .ag-header-cell-text, .ag-header-group-cell,
        .ag-cell, .ag-cell-value, .ag-large-text-input, .ag-floating-filter-input {
            font-family: var(--app-table-font, var(--app-font, inherit)) !important;
        }
        .ag-cell, .ag-cell-value, .ag-header-cell-text, .ag-header-group-cell {
            font-size: var(--app-table-font-size, 13px) !important;
        }
        /* Цвета AG Grid — следуют палитре темы (по умолчанию грид светлый) */
        .ag-root-wrapper {
            --ag-background-color: var(--card-bg) !important;
            --ag-foreground-color: var(--text-color) !important;
            --ag-data-color: var(--text-color) !important;
            --ag-secondary-foreground-color: var(--text-color) !important;
            --ag-header-background-color: var(--panel-bg) !important;
            --ag-header-foreground-color: var(--text-color) !important;
            --ag-odd-row-background-color: var(--panel-bg) !important;
            --ag-row-border-color: var(--panel-bg) !important;
            --ag-border-color: var(--panel-bg) !important;
            --ag-control-panel-background-color: var(--panel-bg) !important;
            --ag-subheader-background-color: var(--panel-bg) !important;
            --ag-selected-row-background-color: var(--accent-color) !important;
            --ag-row-hover-color: var(--panel-bg) !important;
            --ag-input-focus-border-color: var(--accent-color) !important;
            background: var(--card-bg) !important;
            color: var(--text-color) !important;
        }
        /* прямое форсирование цветов AG Grid (на случай, если --ag-* не применяются) */
        .ag-row, .ag-cell, .ag-cell-value, .ag-row-odd, .ag-row-even,
        .ag-paging-panel, .ag-status-bar, .ag-floating-filter, .ag-side-bar, .ag-body-viewport {
            background-color: var(--card-bg) !important;
            color: var(--text-color) !important;
        }
        .ag-header, .ag-header-row, .ag-header-cell, .ag-header-group-cell,
        .ag-pinned-left-header, .ag-pinned-right-header {
            background-color: var(--panel-bg) !important;
            color: var(--text-color) !important;
        }
        /* Карточки/панели разделов следуют теме (по умолчанию q-card белая) */
        .q-card {
            background: var(--card-bg) !important;
            color: var(--text-color) !important;
        }
        .uh-panel {
            background: var(--panel-bg) !important;
            color: var(--text-color) !important;
        }
        /* Шапка и заполненные кнопки следуют палитре (по умолчанию — цвет Quasar primary).
           Селекторы с двумя классами перебивают утилиту Quasar .bg-primary (та же important, но ниже специфичность). */
        .q-header, .q-header.bg-primary {
            background: var(--header-color) !important;
        }
        .q-btn.bg-primary, .q-btn--standard.bg-primary, .q-btn--standard {
            background: var(--button-color) !important;
        }
        .main-container {
            width: 100vw;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            background: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            border: none;
            box-sizing: border-box;
            overflow: hidden;
        }
        .login-form {
            max-width: 400px;
            padding: 2rem;
            background: var(--card-bg);
            box-shadow: var(--glow);
            animation: fadeIn 0.5s ease-in;
            box-sizing: border-box;
        }
        .sidebar {
            min-width: 250px;
            padding: 1rem;
            margin-left: 2rem;
            background: var(--panel-bg);
            border: 1px solid var(--panel-bg);
            box-sizing: border-box;
        }
        .theme-toggle {
            position: absolute;
            top: 1rem;
            right: 1rem;
            z-index: 10;
        }
        .hover-glow:hover {
            transform: scale(1.05);
            filter: brightness(1.2);
            transition: all 0.3s;
        }
        .pulse {
            animation: pulse 2s infinite;
        }
        .title {
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            color: var(--title-color);
        }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.8; } }
        @media (max-width: 768px) {
            .sidebar {
                margin-left: 0;
                margin-top: 1rem;
                width: 100%;
                max-width: 400px;
            }
        }
    """)

    theme = app.storage.user.get('theme', 'dark')
    lang = resolve_language(app.storage.user.get('lang', ''), current_state.get('accept_language', ''))
    tr = lambda key, **kw: translate(key, lang, **kw)
    update_theme(theme)
    # корректная тема: режим Quasar dark перекрашивает поля ввода/переключатель/меню
    login_dark_mode = ui.dark_mode(value=(theme == 'dark'))

    async def toggle_theme():
        nonlocal theme
        theme = 'light' if theme == 'dark' else 'dark'
        app.storage.user.update({'theme': theme})
        update_theme(theme)
        login_dark_mode.value = (theme == 'dark')

    def on_login_language_change():
        app.storage.user.update({'lang': language_select.value or DEFAULT_LANGUAGE})
        ui.run_javascript("window.location.reload()")

    with ui.element('div').classes('main-container') as main_container:
        with ui.card().classes('login-form') as login_card:
            title_label = ui.label('Universal Harvester 2 Scripted').classes('title text-center text-2xl mb-4').style('text-transform: none')
            username_input = ui.input(label=tr("login.username"), placeholder=tr("login.username_ph")).classes('w-full mb-2')
            username_input.tooltip(tr("login.username_tip"))
            password_input = ui.input(label=tr("login.password"), password=True, placeholder=tr("login.password_ph")).classes('w-full mb-4')

            async def handle_login():
                if not username_input.value or not password_input.value:
                    ui.notify(tr("login.fill"), type='negative')
                    return
                login_result = try_login(username_input.value, password_input.value, current_state)
                await sleep()
                if login_result[0]:
                    login_data = login_result[3]
                    app.storage.user.update({
                        'username': login_data['username'],
                        'authenticated': login_data['authenticated'],
                        'session_id': login_data['session_id'],
                        "roles":login_data['roles'],
                        'session_epoch': login_data.get('session_epoch', ""),
                    })
                    user_status_label.set_text(f"USER: {login_data['username']}")
                    user_session_label.set_text(f"USER SESSION: {login_data['session_id']}")
                    ui.notify(tr("login.success"), type='positive')
                    ui.navigate.to('/')
                else:
                    ui.notify(tr("login.failed"), type='negative')

            # вход по Enter в поле пароля
            password_input.on('keydown.enter', handle_login)
            login_button = ui.button(tr("login.login"), on_click=handle_login).classes('w-full hover-glow mb-2').style('background: var(--accent-color)')
            # опциональная кнопка входа через Keycloak (только если включён keycloak)
            if current_state.get("keycloak_flag", False):
                try:
                    auth_url = current_state["keycloak_openid"].auth_url(redirect_uri=f"{current_state['itself_link']}login/callback")
                    ui.button(tr("login.keycloak"), icon='login', on_click=lambda: ui.navigate.to(auth_url)).classes('w-full hover-glow').style('background: var(--accent-color)')
                except Exception as e:
                    ui.label(tr("login.keycloak_error", error=str(e))).classes('text-red-500 text-sm')

        with ui.element('div').classes('sidebar border rounded-lg p-4') as sidebar:
            user_status_label = ui.label('USER: NOT AUTHORIZED').classes('text-sm mb-2 pulse')
            ip_label = ui.label(f"IP: {current_state.get('client_ip_address', 'N/A')}").classes('text-sm mb-2')
            port_label = ui.label(f"PORT: {current_state.get('client_port', 'N/A')}").classes('text-sm mb-2')
            app_session_label = ui.label(f"APP SESSION: {current_state.get('main_session_id', 'N/A')}").classes('text-sm mb-2')
            user_session_label = ui.label('USER SESSION: NONE').classes('text-sm mb-2')

        with ui.element('div').classes('theme-toggle'):
            with ui.row().classes('items-center gap-2'):
                language_select = ui.select(SUPPORTED_LANGUAGES, value=lang, label=tr("login.language")).props('dense').classes('w-32')
                language_select.on_value_change(on_login_language_change)
                ui.switch(tr("login.theme"), value=theme == 'light', on_change=toggle_theme).classes('hover-glow')

def main_page(keycloak_openid, current_state):
    logger_log(syslog.LOG_DEBUG, get_log_message("Main page opened", currentFuncName(), current_state))

    ui.add_css("""
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Roboto:wght@400;700&display=swap');
        :root {
            --bg-color: #1F2937;
            --text-color: #FFFFFF;
            --accent-color: #06B6D4;
            --card-bg: #2D3748;
            --glow: 0 0 15px rgba(6, 182, 212, 0.3);
            --title-color: #22D3EE;
            --panel-bg: #2D3748;
        }
        html, body {
            margin: 0;
            padding: 0;
            background: var(--bg-color);
            overflow: hidden;
            font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif);
            font-size: var(--app-font-size, 14px);
            letter-spacing: 1px;
        }
        /* Шрифт/размер таблиц (AG Grid) — форсируем напрямую на ячейках/заголовках,
           независимо от класса темы (var --ag-font-family на корне темы не перебивается надёжно) */
        .ag-root-wrapper, .ag-header, .ag-header-cell, .ag-header-cell-text, .ag-header-group-cell,
        .ag-cell, .ag-cell-value, .ag-large-text-input, .ag-floating-filter-input {
            font-family: var(--app-table-font, var(--app-font, inherit)) !important;
        }
        .ag-cell, .ag-cell-value, .ag-header-cell-text, .ag-header-group-cell {
            font-size: var(--app-table-font-size, 13px) !important;
        }
        /* Цвета AG Grid — следуют палитре темы (по умолчанию грид светлый) */
        .ag-root-wrapper {
            --ag-background-color: var(--card-bg) !important;
            --ag-foreground-color: var(--text-color) !important;
            --ag-data-color: var(--text-color) !important;
            --ag-secondary-foreground-color: var(--text-color) !important;
            --ag-header-background-color: var(--panel-bg) !important;
            --ag-header-foreground-color: var(--text-color) !important;
            --ag-odd-row-background-color: var(--panel-bg) !important;
            --ag-row-border-color: var(--panel-bg) !important;
            --ag-border-color: var(--panel-bg) !important;
            --ag-control-panel-background-color: var(--panel-bg) !important;
            --ag-subheader-background-color: var(--panel-bg) !important;
            --ag-selected-row-background-color: var(--accent-color) !important;
            --ag-row-hover-color: var(--panel-bg) !important;
            --ag-input-focus-border-color: var(--accent-color) !important;
            background: var(--card-bg) !important;
            color: var(--text-color) !important;
        }
        /* прямое форсирование цветов AG Grid (на случай, если --ag-* не применяются) */
        .ag-row, .ag-cell, .ag-cell-value, .ag-row-odd, .ag-row-even,
        .ag-paging-panel, .ag-status-bar, .ag-floating-filter, .ag-side-bar, .ag-body-viewport {
            background-color: var(--card-bg) !important;
            color: var(--text-color) !important;
        }
        .ag-header, .ag-header-row, .ag-header-cell, .ag-header-group-cell,
        .ag-pinned-left-header, .ag-pinned-right-header {
            background-color: var(--panel-bg) !important;
            color: var(--text-color) !important;
        }
        /* Карточки/панели разделов следуют теме (по умолчанию q-card белая) */
        .q-card {
            background: var(--card-bg) !important;
            color: var(--text-color) !important;
        }
        .uh-panel {
            background: var(--panel-bg) !important;
            color: var(--text-color) !important;
        }
        /* Шапка и заполненные кнопки следуют палитре (по умолчанию — цвет Quasar primary).
           Селекторы с двумя классами перебивают утилиту Quasar .bg-primary (та же important, но ниже специфичность). */
        .q-header, .q-header.bg-primary {
            background: var(--header-color) !important;
        }
        .q-btn.bg-primary, .q-btn--standard.bg-primary, .q-btn--standard {
            background: var(--button-color) !important;
        }
        .main-container {
            width: 100vw;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            background: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            border: none;
            box-sizing: border-box;
            overflow: hidden;
        }
        .login-form {
            max-width: 400px;
            padding: 2rem;
            background: var(--card-bg);
            box-shadow: var(--glow);
            animation: fadeIn 0.5s ease-in;
            box-sizing: border-box;
        }
        .sidebar {
            min-width: 250px;
            padding: 1rem;
            margin-left: 2rem;
            background: var(--panel-bg);
            border: 1px solid var(--panel-bg);
            box-sizing: border-box;
        }
        .theme-toggle {
            position: absolute;
            top: 1rem;
            right: 1rem;
            z-index: 10;
        }
        .hover-glow:hover {
            transform: scale(1.05);
            filter: brightness(1.2);
            transition: all 0.3s;
        }
        .pulse {
            animation: pulse 2s infinite;
        }
        .title {
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            color: var(--title-color);
        }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.8; } }
        @media (max-width: 768px) {
            .sidebar {
                margin-left: 0;
                margin-top: 1rem;
                width: 100%;
                max-width: 400px;
            }
        }
    """)

    theme = app.storage.user.get('theme', 'dark')
    # Внешний вид (тема/шрифты/размеры) — персональные настройки из settings, scope user:<username>.
    # Переключатель темы теперь в разделе Settings; здесь только загрузка и применение сохранённого.
    appearance_scope = settings_user_scope(current_state.get("username", "unknown"))
    # язык интерфейса: сохранённый (settings) -> язык браузера -> английский по умолчанию
    saved_language = get_setting(appearance_scope, "language", "", current_state)[3] or ""
    current_state["lang"] = resolve_language(saved_language, current_state.get("accept_language", ""))
    saved_appearance = get_setting(appearance_scope, "appearance", {}, current_state)[3] or {}
    appearance_state = {**APPEARANCE_DEFAULTS, "theme": theme, **saved_appearance}
    # режим Quasar dark (читаемость полей/меню/чекбоксов/таб-панелей) — реальный элемент dark_mode
    current_state["ui_dark_mode"] = ui.dark_mode(value=(appearance_state["theme"] == "dark"))
    # тема редактора кода: реестр редакторов и активная тема ДО построения панелей
    current_state["ui_codemirrors"] = []
    current_state["codemirror_theme"] = resolve_codemirror_theme(appearance_state, appearance_state["theme"])
    apply_appearance(appearance_state, current_state)

    def logout() -> None:
        app.storage.user.clear()
        try:
            if current_state["keycloak_flag"] == True:
                refresh_token = app.storage.user.get('refresh_token', "")
                if refresh_token != "":
                    keycloak_openid.logout(refresh_token)
        except BaseException as e:
            print("keycloak logout error: ", str(e))
        ui.navigate.to('/login')

    user_status = check_current_user_status(current_state)
    if user_status[0] == False or user_status[2] == False:
        logout()

    # Страж сессии: периодически проверяет, не заблокирован ли пользователь и не сменился ли его
    # session-epoch (смена пароля/блокировка админом или самим пользователем) — принудительный логаут.
    def session_guard():
        try:
            user_result = get_user_by_username(current_state["username"], current_state)
            if not user_result[0] or not user_result[3].get("enabled", False):
                logout()
                return
            stored_epoch = app.storage.user.get('session_epoch', "")
            db_epoch = get_user_session_epoch(current_state["username"], current_state)[3] or ""
            if db_epoch != (stored_epoch or ""):
                logout()
        except BaseException as e:
            logger_log(syslog.LOG_ERR, get_log_message(f"session_guard fail: {str(e)}", currentFuncName(), current_state))

    ui.timer(15.0, session_guard)

    tr = lambda key, **kw: translate(key, current_state.get("lang", DEFAULT_LANGUAGE), **kw)

    # раздел «Хранилище» — только администраторам (кэш общий, удаление влияет на всех)
    is_storage_admin = any(r in (current_state.get("roles") or []) for r in ("fullmaster", "storage_admin"))
    # раздел «Расписания» — только администраторам
    is_schedules_admin = any(r in (current_state.get("roles") or []) for r in ("fullmaster", "schedules_admin"))

    with ui.header(elevated=True) as top_panel:
        with ui.row().classes('items-center'):
            menu_items = [
                (tr("nav.settings"), tr("nav.settings.tip"), 'pets', "Settings"),
                (tr("nav.secrets"), tr("nav.secrets.tip"), 'key', "Secrets"),
                (tr("nav.objects"), tr("nav.objects.tip"), 'source', "Objects"),
                (tr("nav.ai"), tr("nav.ai.tip"), 'psychology', "AI"),
                (tr("nav.knowledge"), tr("nav.knowledge.tip"), 'menu_book', "Knowledge"),
                (tr("nav.harvester"), tr("nav.harvester.tip"), 'rocket_launch', "Harvester"),
                (tr("nav.history"), tr("nav.history.tip"), 'history', "History"),
            ]
            if is_storage_admin:
                menu_items.append((tr("nav.storage"), tr("nav.storage.tip"), 'inventory_2', "Storage"))
            if is_schedules_admin:
                menu_items.append((tr("nav.schedules"), tr("nav.schedules.tip"), 'schedule', "Schedules"))
            menu_items.append((tr("nav.logout"), tr("nav.logout.tip"), 'logout', "__logout__"))
            for item, tooltip, icon, target in menu_items:
                menu_item = ui.button(item, icon=icon).tooltip(tooltip)
                if target == "__logout__":
                    menu_item.on('click', logout)
                else:
                    menu_item.on('click', lambda t=target: show_panel(t))

            # индикаторы выполнения: отдельно Harvester и AI — работают НЕЗАВИСИМО,
            # запуск скрипта не гасит индикатор работающего AI и наоборот
            with ui.row().classes('items-center'):
                execution_spinner = ui.spinner(size='lg').props('color=white')
                execution_spinner.visible = False
                execution_status = ui.label('').classes('text-sm').style(
                    "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); letter-spacing: 1px;")
                ai_spinner = ui.spinner('dots', size='lg').props('color=cyan')
                ai_spinner.visible = False
                ai_status = ui.label('').classes('text-sm').style(
                    "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); letter-spacing: 1px;")
            # ссылки на индикаторы кладём в current_state, чтобы их видели обработчики draw_*
            current_state["ui_spinner"] = execution_spinner       # Harvester
            current_state["ui_status"] = execution_status
            current_state["ui_ai_spinner"] = ai_spinner           # AI
            current_state["ui_ai_status"] = ai_status

    # persistent-вкладки: панель строится ОДИН раз; переключение НЕ очищает интерфейс и НЕ
    # использует display:none. Неактивные панели уводятся за экран (с сохранением ширины),
    # поэтому внутренние табы Quasar остаются измеренными и не пересчитывают layout при показе
    # (устраняет мелькание/«сжатие» на пару кадров).
    ui.add_css(".uh-panel-offscreen { position: absolute !important; left: -100000px !important; top: 0 !important; width: 100% !important; }")

    panel_settings = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panel_secrets = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panel_objects = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panel_ai = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panel_knowledge = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panel_harvester = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panel_history = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panels = {
        "Settings": panel_settings, "Secrets": panel_secrets, "Objects": panel_objects,
        "AI": panel_ai, "Knowledge": panel_knowledge, "Harvester": panel_harvester, "History": panel_history,
    }
    panel_storage = None
    if is_storage_admin:
        panel_storage = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
        panels["Storage"] = panel_storage
    panel_schedules = None
    if is_schedules_admin:
        panel_schedules = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
        panels["Schedules"] = panel_schedules

    def show_panel(name):
        for panel_name, panel in panels.items():
            if panel_name == name:
                panel.classes(remove='uh-panel-offscreen')
            else:
                panel.classes(add='uh-panel-offscreen')

    # хук для перехода между разделами из обработчиков draw_* (напр. AI → Harvester)
    current_state["ui_show_panel"] = show_panel

    draw_settings(panel_settings, current_state)
    draw_secrets(panel_secrets, current_state)
    draw_objects(panel_objects, current_state)
    draw_ai(panel_ai, current_state)
    draw_knowledge(panel_knowledge, current_state)
    draw_harvester(panel_harvester, current_state)
    draw_history(panel_history, current_state)
    if panel_storage is not None:
        draw_storage(panel_storage, current_state)
    if panel_schedules is not None:
        draw_schedules(panel_schedules, current_state)
    show_panel("Harvester")

def draw_schedules(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    """Раздел «Расписания»: запуск сохранённых script-объектов по cron. Только администраторам
    (fullmaster/schedules_admin). Запуск идёт в контексте владельца расписания, пишется в историю."""
    try:
        import uuid as _uuid
        import datetime as _datetime
        from app.scheduler import run_schedule_now, next_run, validate_cron

        interface_container.clear()
        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)
        roles = current_state.get("roles") or []
        if not any(r in roles for r in ("fullmaster", "schedules_admin")):
            with interface_container:
                ui.label(tr("schedules.no_role"))
            return False, "no schedules_admin role", currentFuncName(), None

        current_user = current_state.get("username", "unknown")

        with interface_container:
            ui.label(tr("schedules.title")).style("color: var(--title-color); font-weight:700;")

            # список доступных сохранённых script-объектов
            script_names = []
            objects_result = get_all_actual_objects(current_state)
            if objects_result[0]:
                script_names = sorted([o["name"] for o in objects_result[3] if o.get("type") == "script"])

            with ui.row().classes('gap-2 items-end flex-wrap'):
                name_input = ui.input(label=tr("schedules.col.name")).props('dense').style('min-width: 160px')
                script_select = ui.select(script_names or [], label=tr("schedules.col.script")).props('dense').style('min-width: 180px')
                cron_input = ui.input(label=tr("schedules.col.cron"), value="*/5 * * * *").props('dense').style('min-width: 150px')
                enabled_switch = ui.switch(tr("schedules.col.enabled"), value=True)
                cron_preview = ui.label("").classes('text-xs opacity-70')

            def _refresh_cron_preview():
                ok, msg = validate_cron(cron_input.value or "")
                if not ok:
                    cron_preview.set_text(tr("schedules.cron_invalid"))
                    return
                nxt = next_run(cron_input.value, _datetime.datetime.now().astimezone())
                cron_preview.set_text(tr("schedules.cron_preview", next=(nxt.isoformat(timespec="minutes") if nxt else "—")))
            cron_input.on("blur", lambda: _refresh_cron_preview())

            with ui.row().classes('gap-2 flex-wrap'):
                ui.button(tr("schedules.btn.create"), icon='add').on_click(lambda: create_action())
                ui.button(tr("schedules.btn.toggle"), icon='power_settings_new').on_click(lambda: toggle_action())
                ui.button(tr("schedules.btn.run_now"), icon='play_arrow').on_click(lambda: run_now_action())
                ui.button(tr("schedules.btn.delete"), icon='delete', color='negative').on_click(lambda: delete_action())
                ui.button(tr("schedules.btn.refresh"), icon='refresh').on_click(lambda: refresh_grid())

            grid = ui.aggrid({}).classes('w-full').style('height: 58vh')

            def refresh_grid():
                # обновляем и список доступных script-объектов (новые скрипты появляются в выборе)
                objs = get_all_actual_objects(current_state)
                if objs[0]:
                    script_select.options = sorted([o["name"] for o in objs[3] if o.get("type") == "script"])
                    script_select.update()
                result = list_schedules(current_state)   # админ видит все
                now = _datetime.datetime.now().astimezone()
                rows = []
                if result[0]:
                    for s in result[3]:
                        enabled = s.get("enabled") in (True, 1, "1", "true", "True", "t")
                        status = s.get("last_status")
                        status_text = ("" if status is None else (tr("schedules.status.ok") if int(status) == 1 else tr("schedules.status.fail")))
                        nxt = next_run(s.get("cron") or "", now) if enabled else None
                        rows.append({
                            "id": s["id"], "name": s.get("name", ""), "owner": s.get("owner", ""),
                            "script": s.get("script_name", ""), "cron": s.get("cron", ""),
                            "enabled": tr("schedules.yes") if enabled else tr("schedules.no"),
                            "last_run": s.get("last_run") or "", "status": status_text,
                            "next_run": (nxt.isoformat(timespec="minutes") if nxt else "—"),
                        })
                else:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                grid.options["columnDefs"] = [
                    {"headerName": tr("schedules.col.name"), "field": "name", "filter": True, "sortable": True, "resizable": True, "minWidth": 140},
                    {"headerName": tr("schedules.col.owner"), "field": "owner", "filter": True, "sortable": True, "resizable": True, "minWidth": 110},
                    {"headerName": tr("schedules.col.script"), "field": "script", "filter": True, "sortable": True, "resizable": True, "minWidth": 140},
                    {"headerName": tr("schedules.col.cron"), "field": "cron", "filter": True, "sortable": True, "resizable": True, "minWidth": 120},
                    {"headerName": tr("schedules.col.enabled"), "field": "enabled", "filter": True, "sortable": True, "resizable": True, "minWidth": 90},
                    {"headerName": tr("schedules.col.last_run"), "field": "last_run", "filter": True, "sortable": True, "resizable": True, "minWidth": 180},
                    {"headerName": tr("schedules.col.status"), "field": "status", "filter": True, "sortable": True, "resizable": True, "minWidth": 90},
                    {"headerName": tr("schedules.col.next_run"), "field": "next_run", "filter": True, "sortable": True, "resizable": True, "minWidth": 180},
                ]
                grid.options["rowData"] = rows
                grid.options["rowSelection"] = "single"
                grid.options["defaultColDef"] = {"filter": True, "sortable": True, "resizable": True, "minWidth": 100}
                grid.options["enableCellTextSelection"] = True
                grid.options["domLayout"] = "normal"
                grid.update()

            async def _selected_id():
                row = (await grid.get_selected_row()) or {}
                return row.get("id")

            def create_action():
                name = (name_input.value or "").strip()
                script = script_select.value
                cron = (cron_input.value or "").strip()
                if not name or not script:
                    ui.notify(tr("schedules.need_name_script"), type="warning")
                    return
                ok, msg = validate_cron(cron)
                if not ok:
                    ui.notify(tr("schedules.cron_invalid"), type="negative")
                    return
                result = create_schedule(str(_uuid.uuid4()), name, current_user, script, cron,
                                         enabled_switch.value, current_user, current_state)
                if not result[0]:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                    return
                ui.notify(tr("schedules.created", name=name), type="positive")
                name_input.value = ""
                refresh_grid()

            async def toggle_action():
                sid = await _selected_id()
                if not sid:
                    ui.notify(tr("schedules.pick"), type="warning")
                    return
                cur = get_schedule(sid, current_state)
                if not cur[0] or not cur[3]:
                    ui.notify(tr("schedules.pick"), type="warning")
                    return
                enabled = cur[3].get("enabled") in (True, 1, "1", "true", "True", "t")
                result = set_schedule_enabled(sid, not enabled, current_state)
                if not result[0]:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                    return
                refresh_grid()

            async def delete_action():
                sid = await _selected_id()
                if not sid:
                    ui.notify(tr("schedules.pick"), type="warning")
                    return
                result = delete_schedule(sid, current_state)
                if not result[0]:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                    return
                ui.notify(tr("schedules.deleted"), type="positive")
                refresh_grid()

            async def run_now_action():
                sid = await _selected_id()
                if not sid:
                    ui.notify(tr("schedules.pick"), type="warning")
                    return
                ok, msg = run_schedule_now(sid, current_state)
                ui.notify(tr("schedules.ran") if ok else msg, type=("positive" if ok else "warning"))
                refresh_grid()

            refresh_grid()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None


def draw_storage(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    """Раздел «Хранилище»: список записей персистентного кэша (SAVE→storage) с метаданными,
    предпросмотром, скачиванием и удалением. Только администраторам (fullmaster/storage_admin)."""
    try:
        interface_container.clear()
        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)
        roles = current_state.get("roles") or []
        if not any(r in roles for r in ("fullmaster", "storage_admin")):
            with interface_container:
                ui.label(tr("storage.no_role"))
            return False, "no storage_admin role", currentFuncName(), None

        theme = current_state.get("aggrid_theme", "ag-theme-balham-dark")

        with interface_container:
            ui.label(tr("storage.title")).style("color: var(--title-color); font-weight:700;")
            with ui.row().classes('gap-2 items-center flex-wrap'):
                ui.button(tr("storage.btn.refresh"), icon='refresh').on_click(lambda: refresh_storage_grid())
                ui.button(tr("storage.btn.add"), icon='upload_file').on_click(lambda: add_to_storage_dialog())
                ui.button(tr("storage.btn.preview"), icon='visibility').on_click(lambda: preview_entry())
                fmt_select = ui.select(["json_in_zip", "csv_in_zip", "xlsx"], value="json_in_zip",
                                       label=tr("storage.download_fmt")).props('dense').style('min-width: 140px')
                ui.button(tr("storage.btn.download"), icon='download').on_click(lambda: download_entry())
                ui.button(tr("storage.btn.delete"), icon='delete', color='negative').on_click(lambda: delete_entry())
            grid_storage = ui.aggrid({}).classes('w-full').style('height: 62vh')

            def add_to_storage_dialog():
                """Загрузка отдельной таблицы (CSV/XLSX) в storage под ключом с TTL — для инвентаризационных
                данных вне систем. Ключ и TTL задаются до загрузки; парсинг файла -> storage_save (upsert)."""
                with ui.dialog() as add_dialog, ui.card().classes('w-full max-w-2xl'):
                    ui.label(tr("storage.add.title")).style("font-weight:700; color: var(--title-color);")
                    key_input = ui.input(tr("storage.add.key")).classes('w-full').props('dense')
                    ttl_input = ui.number(tr("storage.add.ttl"), value=None, min=0).classes('w-full').props('dense')
                    ui.label(tr("storage.add.ttl_hint")).classes('text-sm').style("opacity:0.75;")

                    def handle_upload(event):
                        key = (key_input.value or "").strip()
                        # если ключ не задан — берём имя файла без расширения
                        if not key:
                            base = (event.name or "").rsplit(".", 1)[0].strip()
                            key = base
                        if not key:
                            ui.notify(tr("storage.add.need_key"), type="warning")
                            return
                        # TTL: пусто -> не истекает (None); иначе целое число секунд
                        ttl = None
                        if ttl_input.value not in (None, ""):
                            try:
                                ttl = int(ttl_input.value)
                                if ttl <= 0:
                                    ttl = None
                            except (TypeError, ValueError):
                                ui.notify(tr("storage.add.bad_ttl"), type="warning")
                                return
                        try:
                            content = event.content.read()
                        except BaseException as read_error:
                            ui.notify(tr("storage.add.error", error=str(read_error)), type="negative")
                            return
                        ok, err, records = parse_table_file(content, event.name)
                        if not ok:
                            ui.notify(tr("storage.add.error", error=err), type="negative")
                            return
                        if not records:
                            ui.notify(tr("storage.add.empty"), type="warning")
                            return
                        save_result = storage_save(key, records, ttl, current_state)
                        if not save_result[0]:
                            ui.notify(tr("settings.common.error", error=save_result[1]), type="negative")
                            return
                        ui.notify(tr("storage.add.saved", name=key, rows=len(records)), type="positive")
                        add_dialog.close()
                        refresh_storage_grid()

                    ui.upload(label=tr("storage.add.upload"), auto_upload=True, on_upload=handle_upload) \
                        .props('accept=".csv,.xlsx,.xls"').classes('w-full')
                    ui.button(tr("settings.btn.close"), on_click=add_dialog.close).classes('hover-glow')
                add_dialog.open()

            def refresh_storage_grid():
                result = storage_list(current_state)
                rows = []
                if result[0]:
                    for e in result[3]:
                        rows.append({
                            "id": e["id"],
                            "owner": e["owner"],
                            "created": e["created_ts"],
                            "updated": e["updated_ts"],
                            "ttl": (tr("storage.ttl.never") if e["ttl"] == "" else e["ttl"]),
                            "rows": e["rows"],
                            "size": e["size_bytes"],
                            "status": tr("storage.status.expired") if e["expired"] else tr("storage.status.active"),
                        })
                else:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                grid_storage.options["columnDefs"] = [
                    {"headerName": tr("storage.col.key"), "field": "id", "filter": True, "sortable": True, "resizable": True, "minWidth": 160},
                    {"headerName": tr("storage.col.owner"), "field": "owner", "filter": True, "sortable": True, "resizable": True, "minWidth": 120},
                    {"headerName": tr("storage.col.created"), "field": "created", "filter": True, "sortable": True, "resizable": True, "minWidth": 190},
                    {"headerName": tr("storage.col.updated"), "field": "updated", "filter": True, "sortable": True, "resizable": True, "minWidth": 190},
                    {"headerName": tr("storage.col.ttl"), "field": "ttl", "filter": True, "sortable": True, "resizable": True, "minWidth": 100},
                    {"headerName": tr("storage.col.rows"), "field": "rows", "filter": True, "sortable": True, "resizable": True, "minWidth": 90},
                    {"headerName": tr("storage.col.size"), "field": "size", "filter": True, "sortable": True, "resizable": True, "minWidth": 110},
                    {"headerName": tr("storage.col.status"), "field": "status", "filter": True, "sortable": True, "resizable": True, "minWidth": 110},
                ]
                grid_storage.options["rowData"] = rows
                grid_storage.options["rowSelection"] = "single"
                grid_storage.options["defaultColDef"] = {"filter": True, "sortable": True, "resizable": True, "minWidth": 100}
                grid_storage.options["enableCellTextSelection"] = True
                grid_storage.options["enableBrowserTooltips"] = True
                grid_storage.options["domLayout"] = "normal"
                grid_storage.update()

            async def _selected_key():
                row = (await grid_storage.get_selected_row()) or {}
                return row.get("id")

            async def _load_selected_data():
                key = await _selected_key()
                if not key:
                    ui.notify(tr("storage.pick"), type="warning")
                    return None, None
                load_result = storage_load(key, current_state)
                if not load_result[0] or load_result[3] is None:
                    ui.notify(load_result[1] if not load_result[0] else tr("storage.gone", name=key), type="negative")
                    return key, None
                return key, (load_result[3].get("data") or [])

            async def preview_entry():
                key, data = await _load_selected_data()
                if data is None:
                    return
                with ui.dialog() as preview_dialog, ui.card().classes('w-full max-w-5xl'):
                    ui.label(tr("storage.preview_title", name=key)).style("font-weight:700; color: var(--title-color);")
                    if data:
                        ui.aggrid(records_to_aggrid_options(data, theme)).classes('w-full').style('height: 60vh')
                    else:
                        ui.markdown(tr("storage.preview_empty"))
                    ui.button(tr("settings.btn.close"), on_click=preview_dialog.close).classes('hover-glow')
                preview_dialog.open()

            async def download_entry():
                key, data = await _load_selected_data()
                if data is None:
                    return
                try:
                    content, filename, media_type = records_to_download({key: data}, fmt_select.value, key)
                except BaseException as download_error:
                    ui.notify(str(download_error), type="negative")
                    return
                try:
                    ui.download(content, filename, media_type)
                except TypeError:
                    ui.download(content, filename)

            async def delete_entry():
                key = await _selected_key()
                if not key:
                    ui.notify(tr("storage.pick"), type="warning")
                    return
                result = storage_delete(key, current_state)
                if not result[0]:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                    return
                ui.notify(tr("storage.deleted", name=key), type="positive")
                refresh_storage_grid()

            refresh_storage_grid()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None


def draw_knowledge(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    """Раздел «База знаний»: общая память AI-агента (таблица knowledge). Просмотр/поиск/добавление/
    редактирование/удаление заметок. Видна всем (память командная, как её и пишет агент)."""
    try:
        interface_container.clear()
        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)

        with interface_container:
            ui.label(tr("knowledge.title")).style("color: var(--title-color); font-weight:700;")
            with ui.row().classes('gap-2 items-center flex-wrap'):
                search_input = ui.input(tr("knowledge.search_placeholder")).props('dense clearable').style('min-width: 260px')
                ui.button(tr("knowledge.btn.refresh"), icon='refresh').on_click(lambda: refresh_grid())
                ui.button(tr("knowledge.btn.add"), icon='add').on_click(lambda: open_editor(None))
                ui.button(tr("knowledge.btn.edit"), icon='edit').on_click(lambda: edit_selected())
                ui.button(tr("knowledge.btn.preview"), icon='visibility').on_click(lambda: preview_selected())
                ui.button(tr("knowledge.btn.delete"), icon='delete', color='negative').on_click(lambda: delete_selected())
            grid = ui.aggrid({}).classes('w-full').style('height: 60vh')
            search_input.on('keydown.enter', lambda: refresh_grid())

            # кэш загруженных заметок по id — чтобы редактор/просмотр не били в БД повторно
            notes_by_id = {}

            def refresh_grid():
                query = (search_input.value or "").strip()
                if query:
                    result = knowledge_search(query, current_state)
                else:
                    result = knowledge_list(current_state)
                notes = result[3] if result[0] else []
                if not result[0]:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                notes_by_id.clear()
                rows = []
                for n in notes:
                    notes_by_id[n["id"]] = n
                    rows.append({
                        "id": n["id"],
                        "title": n["title"],
                        "tags": ", ".join(n.get("tags") or []),
                        "owner": n.get("owner", ""),
                        "updated": n.get("updated_at", ""),
                        "size": len(n.get("content") or ""),
                    })
                grid.options["columnDefs"] = [
                    {"headerName": tr("knowledge.col.title"), "field": "title", "filter": True, "sortable": True, "resizable": True, "minWidth": 220},
                    {"headerName": tr("knowledge.col.tags"), "field": "tags", "filter": True, "sortable": True, "resizable": True, "minWidth": 160},
                    {"headerName": tr("knowledge.col.owner"), "field": "owner", "filter": True, "sortable": True, "resizable": True, "minWidth": 120},
                    {"headerName": tr("knowledge.col.updated"), "field": "updated", "filter": True, "sortable": True, "resizable": True, "minWidth": 190},
                    {"headerName": tr("knowledge.col.size"), "field": "size", "filter": True, "sortable": True, "resizable": True, "minWidth": 110},
                ]
                grid.options["rowData"] = rows
                grid.options["rowSelection"] = "single"
                grid.options["defaultColDef"] = {"filter": True, "sortable": True, "resizable": True, "minWidth": 100}
                grid.options["enableCellTextSelection"] = True
                grid.options["enableBrowserTooltips"] = True
                grid.options["domLayout"] = "normal"
                grid.update()

            async def _selected_note():
                row = (await grid.get_selected_row()) or {}
                note_id = row.get("id")
                if not note_id:
                    ui.notify(tr("knowledge.pick"), type="warning")
                    return None
                return notes_by_id.get(note_id)

            def open_editor(note):
                """Диалог создания/редактирования. note=None -> новая заметка."""
                is_edit = note is not None
                with ui.dialog() as editor_dialog, ui.card().classes('w-full max-w-3xl'):
                    ui.label(tr("knowledge.dialog.edit") if is_edit else tr("knowledge.dialog.add")).style(
                        "font-weight:700; color: var(--title-color);")
                    title_input = ui.input(tr("knowledge.field.title")).classes('w-full').props('dense')
                    tags_input = ui.input(tr("knowledge.field.tags")).classes('w-full').props('dense')
                    content_input = ui.textarea(tr("knowledge.field.content")).classes('w-full').props('outlined autogrow')
                    if is_edit:
                        title_input.value = note["title"]
                        tags_input.value = ", ".join(note.get("tags") or [])
                        content_input.value = note.get("content", "")

                    def save():
                        title = (title_input.value or "").strip()
                        content = (content_input.value or "").strip()
                        if not title or not content:
                            ui.notify(tr("knowledge.need_fields"), type="warning")
                            return
                        tags = [t.strip() for t in (tags_input.value or "").split(",") if t.strip()]
                        # если при редактировании заголовок изменился — удаляем старую запись (upsert по title
                        # создаст новую), чтобы не плодить дубликат со старым названием.
                        if is_edit and note["title"].strip().lower() != title.lower():
                            knowledge_delete(note["id"], current_state)
                        save_result = knowledge_save(title, content, tags, current_state)
                        if not save_result[0]:
                            ui.notify(tr("settings.common.error", error=save_result[1]), type="negative")
                            return
                        ui.notify(tr("knowledge.saved"), type="positive")
                        editor_dialog.close()
                        refresh_grid()

                    with ui.row().classes('gap-2'):
                        ui.button(tr("knowledge.btn.save"), icon='save').on_click(save)
                        ui.button(tr("knowledge.btn.cancel"), on_click=editor_dialog.close)
                editor_dialog.open()

            async def edit_selected():
                note = await _selected_note()
                if note:
                    open_editor(note)

            async def preview_selected():
                note = await _selected_note()
                if not note:
                    return
                with ui.dialog() as preview_dialog, ui.card().classes('w-full max-w-3xl'):
                    tags = ", ".join(note.get("tags") or [])
                    ui.label(note["title"]).style("font-weight:700; color: var(--title-color);")
                    if tags:
                        ui.label(tags).classes('text-sm').style("opacity:0.8;")
                    ui.markdown("```\n" + (note.get("content") or "") + "\n```")
                    ui.button(tr("settings.btn.close"), on_click=preview_dialog.close).classes('hover-glow')
                preview_dialog.open()

            async def delete_selected():
                note = await _selected_note()
                if not note:
                    return
                result = knowledge_delete(note["id"], current_state)
                if not result[0]:
                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                    return
                ui.notify(tr("knowledge.deleted", name=note["title"]), type="positive")
                refresh_grid()

            refresh_grid()
        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None


def draw_settings(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    """Раздел Settings. Этап 1 — «Внешний вид»: тема, шрифты и размеры интерфейса/таблиц.
    Персональные настройки хранятся в settings (scope user:<username>) и применяются живо через CSS-переменные."""
    try:
        interface_container.clear()
        username = current_state.get("username", "unknown")
        scope = settings_user_scope(username)
        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)
        saved = get_setting(scope, "appearance", {}, current_state)[3] or {}
        appearance = {**APPEARANCE_DEFAULTS, "theme": app.storage.user.get('theme', 'dark'), **saved}

        with interface_container:
            with ui.element('div').classes('w-full').style('max-height: 88vh; overflow-y: auto;'):
                with ui.column().classes('w-full gap-4 p-2'):
                    # ──────────── Язык интерфейса ────────────
                    with ui.expansion(tr("settings.language.title"), icon='translate', value=False).classes('w-full'):
                        with ui.row().classes('items-end gap-2'):
                            language_select = ui.select(SUPPORTED_LANGUAGES, value=lang, label=tr("settings.language.label")).classes('w-64')

                            def save_language():
                                set_setting(scope, "language", language_select.value or DEFAULT_LANGUAGE, current_state)
                                ui.notify(tr("settings.language.saved"), type="positive")
                                ui.run_javascript("window.location.reload()")  # перезагрузка применяет язык ко всему интерфейсу

                            ui.button(tr("settings.language.apply"), icon='translate', on_click=save_language).classes('hover-glow')
                        ui.markdown(tr("settings.language.hint")).classes('text-xs opacity-60')


                    with ui.expansion(tr("settings.section.appearance"), icon='palette', value=False).classes('w-full'):
                        ui.markdown(tr("settings.appearance.hint"))

                        theme_select = ui.select(
                            {"dark": tr("settings.theme.dark"), "light": tr("settings.theme.light")}, value=appearance["theme"], label=tr("settings.theme.label")
                        ).classes('w-64')

                        ui.separator()
                        ui.label(tr("settings.appearance.interface")).style("color: var(--accent-color);")
                        with ui.row().classes('items-end gap-4 w-full'):
                            font_select = ui.select(
                                FONT_OPTIONS, value=appearance["font"], label=tr("settings.font.interface"), with_input=True
                            ).classes('grow')
                            font_size_input = ui.number(
                                label=tr("settings.font.size"), value=appearance["font_size"], min=8, max=32, step=1
                            ).classes('w-32')

                        ui.separator()
                        ui.label(tr("settings.appearance.tables")).style("color: var(--accent-color);")
                        with ui.row().classes('items-end gap-4 w-full'):
                            table_font_select = ui.select(
                                FONT_OPTIONS, value=appearance["table_font"], label=tr("settings.font.tables"), with_input=True
                            ).classes('grow')
                            table_font_size_input = ui.number(
                                label=tr("settings.font.size"), value=appearance["table_font_size"], min=8, max=24, step=1
                            ).classes('w-32')

                        ui.separator()
                        ui.label(tr("settings.appearance.codemirror")).style("color: var(--accent-color);")
                        ui.markdown(tr("settings.codemirror.hint")).classes('text-xs opacity-60')
                        # тема редактора по теме приложения: {"dark":..., "light":...} (поддержка старого строкового формата)
                        _cm_raw = appearance.get("codemirror_theme")
                        if isinstance(_cm_raw, dict):
                            cm_state = {"dark": _cm_raw.get("dark") or APPEARANCE_DEFAULTS["codemirror_theme"]["dark"],
                                        "light": _cm_raw.get("light") or APPEARANCE_DEFAULTS["codemirror_theme"]["light"]}
                        elif isinstance(_cm_raw, str) and _cm_raw:
                            cm_state = {"dark": _cm_raw, "light": APPEARANCE_DEFAULTS["codemirror_theme"]["light"]}
                        else:
                            cm_state = dict(APPEARANCE_DEFAULTS["codemirror_theme"])
                        codemirror_theme_select = ui.select(
                            CODEMIRROR_THEMES, value=cm_state.get(appearance["theme"]), label=tr("settings.codemirror.label")
                        ).classes('w-64')

                        ui.separator()
                        ui.label(tr("settings.appearance.colors")).style("color: var(--accent-color);")
                        ui.markdown(tr("settings.colors.hint")).classes('text-xs opacity-60')

                        # оверрайды цветов по теме: {"dark": {role: "#hex"}, "light": {...}} — живо обновляются пикерами
                        colors_state = {t: dict(v) for t, v in (appearance.get("colors") or {}).items()}
                        color_pickers = {}
                        suspend = {"v": False}

                        def effective_colors(theme_name):
                            return {**THEMES.get(theme_name, THEMES["dark"]), **(colors_state.get(theme_name) or {})}

                        with ui.row().classes('items-end gap-3 w-full flex-wrap'):
                            _eff = effective_colors(appearance["theme"])
                            for role, role_label in COLOR_ROLES:
                                color_pickers[role] = ui.color_input(label=tr(f"settings.color.{role}"), value=_eff[role]).classes('w-40')

                        def sync_colors():
                            # сохранить значения пикеров как оверрайды текущей темы
                            theme_name = theme_select.value or "dark"
                            colors_state[theme_name] = {
                                role: (color_pickers[role].value or THEMES[theme_name][role]) for role, _ in COLOR_ROLES
                            }

                        def sync_cm():
                            # сохранить выбранную тему редактора для текущей темы приложения
                            theme_name = theme_select.value or "dark"
                            cm_state[theme_name] = codemirror_theme_select.value or APPEARANCE_DEFAULTS["codemirror_theme"][theme_name]

                        def collect():
                            return {
                                "theme": theme_select.value or "dark",
                                "font": (font_select.value or APPEARANCE_DEFAULTS["font"]).strip(),
                                "font_size": int(font_size_input.value or APPEARANCE_DEFAULTS["font_size"]),
                                "table_font": (table_font_select.value or APPEARANCE_DEFAULTS["table_font"]).strip(),
                                "table_font_size": int(table_font_size_input.value or APPEARANCE_DEFAULTS["table_font_size"]),
                                "colors": colors_state,
                                "codemirror_theme": cm_state,
                            }

                        def preview():
                            # применить без сохранения (для подбора)
                            if suspend["v"]:
                                return
                            sync_colors()
                            sync_cm()
                            apply_appearance(collect(), current_state)
                            apply_codemirror_theme(current_state, cm_state[theme_select.value or "dark"])

                        def on_theme_change():
                            # при смене темы перерисовать пикеры цветов и тему редактора под выбранную тему
                            suspend["v"] = True
                            theme_name = theme_select.value or "dark"
                            eff = effective_colors(theme_name)
                            for role, _ in COLOR_ROLES:
                                color_pickers[role].value = eff[role]
                            codemirror_theme_select.value = cm_state.get(theme_name) or APPEARANCE_DEFAULTS["codemirror_theme"][theme_name]
                            suspend["v"] = False
                            preview()

                        def save():
                            sync_colors()
                            sync_cm()
                            new_appearance = collect()
                            set_setting_result = set_setting(scope, "appearance", new_appearance, current_state)
                            if not set_setting_result[0]:
                                ui.notify(tr("settings.appearance.save_fail", error=set_setting_result[1]), type="negative")
                                return
                            app.storage.user.update({'theme': new_appearance["theme"]})  # синхронизация с login-страницей
                            apply_appearance(new_appearance, current_state)
                            apply_codemirror_theme(current_state, cm_state[new_appearance["theme"]])
                            ui.notify(tr("settings.appearance.saved"), type="positive")

                        def reset_defaults():
                            suspend["v"] = True
                            theme_select.value = APPEARANCE_DEFAULTS["theme"]
                            font_select.value = APPEARANCE_DEFAULTS["font"]
                            font_size_input.value = APPEARANCE_DEFAULTS["font_size"]
                            table_font_select.value = APPEARANCE_DEFAULTS["table_font"]
                            table_font_size_input.value = APPEARANCE_DEFAULTS["table_font_size"]
                            cm_state.clear()
                            cm_state.update(APPEARANCE_DEFAULTS["codemirror_theme"])
                            codemirror_theme_select.value = cm_state[APPEARANCE_DEFAULTS["theme"]]
                            colors_state.clear()
                            eff = effective_colors(APPEARANCE_DEFAULTS["theme"])
                            for role, _ in COLOR_ROLES:
                                color_pickers[role].value = eff[role]
                            suspend["v"] = False
                            preview()

                        # живой предпросмотр при изменении контролов
                        theme_select.on('update:model-value', lambda: on_theme_change())
                        for control in (font_select, font_size_input, table_font_select, table_font_size_input, codemirror_theme_select):
                            control.on('update:model-value', lambda: preview())
                        for role, _ in COLOR_ROLES:
                            color_pickers[role].on('update:model-value', lambda: preview())

                        with ui.row().classes('gap-2 mt-2'):
                            ui.button(tr("settings.btn.save"), icon='save', on_click=save).classes('hover-glow')
                            ui.button(tr("settings.btn.reset"), icon='restart_alt', on_click=reset_defaults).props('outline')

                        # ───────────────────────── Учётная запись (self-service) ─────────────────────────

                    with ui.expansion(tr("settings.section.account"), icon='person', value=False).classes('w-full'):
                        ui.markdown(tr("settings.account.user", name=username)).classes('text-sm')

                        ui.label(tr("settings.account.changepw")).style("color: var(--accent-color);")
                        ui.markdown(tr("settings.account.pwrule")).classes('text-xs opacity-60')
                        ui.markdown(tr("settings.account.pwwarn")).classes('text-xs text-orange-400')
                        with ui.column().classes('gap-2 w-full max-w-md'):
                            old_password_input = ui.input(label=tr("settings.account.oldpw"), password=True, password_toggle_button=True).classes('w-full')
                            new_password_input = ui.input(label=tr("settings.account.newpw"), password=True, password_toggle_button=True).classes('w-full')
                            confirm_password_input = ui.input(label=tr("settings.account.confirmpw"), password=True, password_toggle_button=True).classes('w-full')

                        async def change_password():
                            old_pw = old_password_input.value or ""
                            new_pw = new_password_input.value or ""
                            confirm_pw = confirm_password_input.value or ""
                            if not old_pw or not new_pw:
                                ui.notify(tr("settings.account.fill"), type="negative")
                                return
                            if new_pw != confirm_pw:
                                ui.notify(tr("settings.account.mismatch"), type="negative")
                                return
                            if not check_regex_rule(new_pw, REGEX_PASSWORD_RULE):
                                ui.notify(tr("settings.pw.weak"), type="negative")
                                return
                            # проверяем текущий пароль через bcrypt
                            user_result = get_user_by_username(username, current_state)
                            if not user_result[0]:
                                ui.notify(tr("settings.account.userfail", error=user_result[1]), type="negative")
                                return
                            stored_hash = user_result[3]["pass"]
                            if isinstance(stored_hash, str):
                                stored_hash = stored_hash.encode("utf-8")
                            import bcrypt
                            if not bcrypt.checkpw(old_pw.encode("utf-8"), stored_hash):
                                ui.notify(tr("settings.account.wrongpw"), type="negative")
                                return
                            # подтверждение разлогина
                            with ui.dialog() as confirm_dialog, ui.card():
                                ui.label(tr("settings.account.confirm"))
                                with ui.row().classes('justify-end w-full'):
                                    ui.button(tr("settings.btn.cancel"), on_click=lambda: confirm_dialog.submit(False)).props('outline')
                                    ui.button(tr("settings.account.confirm_yes"), on_click=lambda: confirm_dialog.submit(True)).classes('hover-glow')
                            if not await confirm_dialog:
                                return
                            new_hash = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
                            set_result = set_user_password(username, new_hash, current_state)
                            if not set_result[0]:
                                ui.notify(tr("settings.account.changefail", error=set_result[1]), type="negative")
                                return
                            old_password_input.value = ""
                            new_password_input.value = ""
                            confirm_password_input.value = ""
                            # смена пароля отзывает все сессии (set_user_password бампит epoch) — выходим немедленно
                            ui.notify(tr("settings.account.changed"), type="positive")
                            app.storage.user.clear()
                            ui.navigate.to('/login')

                        ui.button(tr("settings.account.changebtn"), icon='password', on_click=change_password).classes('hover-glow')

                        ui.separator()
                        ui.label(tr("settings.account.meta")).style("color: var(--accent-color);")
                        ui.markdown(tr("settings.account.meta_hint")).classes('text-xs opacity-60')
                        user_meta_result = get_user_by_username(username, current_state)
                        current_meta = user_meta_result[3].get("json", {}) if user_meta_result[0] else {}
                        metadata_editor = make_codemirror(current_state).classes('w-full').style('max-height: 30vh')
                        metadata_editor.value = json.dumps(current_meta, ensure_ascii=False, indent=2)

                        def save_metadata():
                            text = metadata_editor.value or ""
                            if not json_validate(text):
                                ui.notify(tr("settings.meta.invalid_json"), type="negative")
                                return
                            parsed = json.loads(text)
                            if not isinstance(parsed, dict):
                                ui.notify(tr("settings.meta.not_object"), type="negative")
                                return
                            update_result = update_user_metadata(username, parsed, current_state)
                            if not update_result[0]:
                                ui.notify(tr("settings.meta.save_fail", error=update_result[1]), type="negative")
                                return
                            ui.notify(tr("settings.meta.saved"), type="positive")

                        ui.button(tr("settings.meta.savebtn"), icon='save', on_click=save_metadata).classes('hover-glow')

                    # ──────────── Управление пользователями (роли fullmaster / useradmin) ────────────
                    current_roles = user_meta_result[3].get("roles", []) if user_meta_result[0] else []
                    if any(role in current_roles for role in ("fullmaster", "useradmin")):
                        with ui.expansion(tr("settings.section.users"), icon='group', value=False).classes('w-full'):
                            ui.markdown(tr("settings.users.hint")).classes('text-xs opacity-60')

                            grid_users = ui.aggrid({}).classes('w-full').style('height: 40vh')

                            ui.label(tr("settings.users.actions")).style("color: var(--accent-color);")
                            selected_user_label = ui.label(tr("settings.users.none")).classes('text-sm').style('font-weight:700')
                            admin_roles_input = ui.input(label=tr("settings.users.roles"), value="[]").classes('w-full max-w-md')
                            admin_reset_pw_input = ui.input(label=tr("settings.users.resetpw"), password=True, password_toggle_button=True).classes('w-full max-w-md')
                            ui.label(tr("settings.users.meta")).classes('text-xs opacity-60')
                            admin_meta_editor = make_codemirror(current_state).classes('w-full').style('max-height: 25vh')
                            admin_selected = {"name": None, "enabled": None}

                            def refresh_users_grid():
                                list_result = list_users(current_state)
                                rows = []
                                if list_result[0]:
                                    for u in list_result[3]:
                                        rows.append({
                                            "username": u["name"],
                                            "enabled": (tr("settings.common.yes") if u["enabled"] else tr("settings.common.no")),
                                            "roles": json.dumps(u["roles"], ensure_ascii=False),
                                            "metadata": json.dumps(u["json"], ensure_ascii=False),
                                            "_enabled": u["enabled"],
                                        })
                                else:
                                    ui.notify(tr("settings.users.list_fail", error=list_result[1]), type="negative")
                                grid_users.options["columnDefs"] = [
                                    {"headerName": tr("settings.users.col.username"), "field": "username", "filter": True, "sortable": True, "resizable": True, "minWidth": 160, "tooltipField": "username"},
                                    {"headerName": tr("settings.users.col.enabled"), "field": "enabled", "filter": True, "sortable": True, "resizable": True, "minWidth": 110},
                                    {"headerName": tr("settings.users.col.roles"), "field": "roles", "filter": True, "sortable": True, "resizable": True, "minWidth": 200, "tooltipField": "roles"},
                                    {"headerName": tr("settings.users.col.metadata"), "field": "metadata", "filter": True, "sortable": True, "resizable": True, "minWidth": 240, "tooltipField": "metadata"},
                                ]
                                grid_users.options["rowData"] = rows
                                grid_users.options["rowSelection"] = "single"
                                grid_users.options["pagination"] = True
                                grid_users.options["paginationPageSize"] = 20
                                grid_users.options["enableCellTextSelection"] = True
                                grid_users.options["enableBrowserTooltips"] = True
                                grid_users.options["defaultColDef"] = {"filter": True, "sortable": True, "resizable": True, "minWidth": 140}
                                grid_users.options["domLayout"] = "normal"
                                grid_users.update()

                            def _refresh_selected_label():
                                if not admin_selected["name"]:
                                    selected_user_label.set_text(tr("settings.users.none"))
                                    block_user_button.set_text(tr("settings.users.block"))
                                    return
                                selected_user_label.set_text(
                                    tr("settings.users.selected", name=admin_selected['name'], status=(tr("settings.users.active") if admin_selected['enabled'] else tr("settings.users.blocked"))))
                                block_user_button.set_text(tr("settings.users.unblock") if not admin_selected["enabled"] else tr("settings.users.block"))

                            async def on_user_selected():
                                row = (await grid_users.get_selected_row()) or {}
                                if not row:
                                    return
                                admin_selected["name"] = row.get("username")
                                admin_selected["enabled"] = bool(row.get("_enabled"))
                                admin_roles_input.value = row.get("roles") or "[]"
                                admin_reset_pw_input.value = ""
                                try:
                                    admin_meta_editor.value = json.dumps(json.loads(row.get("metadata") or "{}"), ensure_ascii=False, indent=2)
                                except BaseException:
                                    admin_meta_editor.value = row.get("metadata") or "{}"
                                _refresh_selected_label()

                            def toggle_user_enabled():
                                name = admin_selected["name"]
                                if not name:
                                    ui.notify(tr("settings.users.pick"), type="warning")
                                    return
                                if name == username and admin_selected["enabled"]:
                                    ui.notify(tr("settings.users.noselfblock"), type="negative")
                                    return
                                result = set_user_enabled(name, not admin_selected["enabled"], current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                ui.notify((tr("settings.users.unblocked", name=name) if not admin_selected["enabled"]
                                           else tr("settings.users.blocked_done", name=name)), type="positive")
                                admin_selected["enabled"] = not admin_selected["enabled"]
                                _refresh_selected_label()
                                refresh_users_grid()

                            def save_user_roles():
                                name = admin_selected["name"]
                                if not name:
                                    ui.notify(tr("settings.users.pick"), type="warning")
                                    return
                                if not json_validate(admin_roles_input.value or ""):
                                    ui.notify(tr("settings.roles.array"), type="negative")
                                    return
                                parsed = json.loads(admin_roles_input.value)
                                if not isinstance(parsed, list):
                                    ui.notify(tr("settings.roles.array_strings"), type="negative")
                                    return
                                result = set_user_roles(name, parsed, current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                ui.notify(tr("settings.users.roles_saved", name=name), type="positive")
                                refresh_users_grid()

                            def reset_user_password():
                                name = admin_selected["name"]
                                if not name:
                                    ui.notify(tr("settings.users.pick"), type="warning")
                                    return
                                if not check_regex_rule(admin_reset_pw_input.value or "", REGEX_PASSWORD_RULE):
                                    ui.notify(tr("settings.pw.weak"), type="negative")
                                    return
                                import bcrypt
                                new_hash = bcrypt.hashpw((admin_reset_pw_input.value).encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
                                result = set_user_password(name, new_hash, current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                admin_reset_pw_input.value = ""
                                ui.notify(tr("settings.users.pw_reset", name=name), type="positive")

                            def save_user_metadata():
                                name = admin_selected["name"]
                                if not name:
                                    ui.notify(tr("settings.users.pick"), type="warning")
                                    return
                                text = admin_meta_editor.value or ""
                                if not json_validate(text):
                                    ui.notify(tr("settings.meta.invalid_json"), type="negative")
                                    return
                                parsed = json.loads(text)
                                if not isinstance(parsed, dict):
                                    ui.notify(tr("settings.meta.not_object"), type="negative")
                                    return
                                result = update_user_metadata(name, parsed, current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                ui.notify(tr("settings.users.meta_saved", name=name), type="positive")
                                refresh_users_grid()

                            with ui.row().classes('gap-2 flex-wrap'):
                                block_user_button = ui.button(tr("settings.users.block"), icon='block', on_click=toggle_user_enabled).props('outline')
                                ui.button(tr("settings.users.save_roles"), icon='save', on_click=save_user_roles)
                                ui.button(tr("settings.meta.savebtn"), icon='save', on_click=save_user_metadata)
                                ui.button(tr("settings.users.reset_pw_btn"), icon='password', on_click=reset_user_password).props('outline')
                                ui.button(tr("settings.btn.refresh"), icon='refresh', on_click=refresh_users_grid).props('flat')

                            grid_users.on("selectionChanged", on_user_selected)

                            with ui.expansion(tr("settings.users.create"), icon='person_add').classes('w-full'):
                                new_user_name = ui.input(label=tr("settings.users.username")).classes('w-full max-w-md')
                                new_user_pw = ui.input(label=tr("settings.users.password"), password=True, password_toggle_button=True).classes('w-full max-w-md')
                                new_user_roles = ui.input(label=tr("settings.users.roles"), value='[]').classes('w-full max-w-md')

                                def create_new_user():
                                    name = (new_user_name.value or "").strip()
                                    if not check_regex_rule(name, REGEX_USERNAME_RULE):
                                        ui.notify(tr("settings.users.name_rule"), type="negative")
                                        return
                                    if not check_regex_rule(new_user_pw.value or "", REGEX_PASSWORD_RULE):
                                        ui.notify(tr("settings.pw.weak"), type="negative")
                                        return
                                    if not json_validate(new_user_roles.value or ""):
                                        ui.notify(tr("settings.roles.array"), type="negative")
                                        return
                                    roles = json.loads(new_user_roles.value)
                                    if not isinstance(roles, list):
                                        ui.notify(tr("settings.roles.array"), type="negative")
                                        return
                                    import bcrypt
                                    new_hash = bcrypt.hashpw((new_user_pw.value).encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')
                                    result = create_user(name, new_hash, roles, {}, current_state)
                                    if not result[0]:
                                        ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                        return
                                    new_user_name.value = ""
                                    new_user_pw.value = ""
                                    new_user_roles.value = "[]"
                                    ui.notify(tr("settings.users.created", name=name), type="positive")
                                    refresh_users_grid()

                                ui.button(tr("settings.btn.create"), icon='person_add', on_click=create_new_user).classes('hover-glow')

                            refresh_users_grid()

                        # ──────────── AI-агент: настройки и журнал (роли fullmaster / aiadmin) ────────────

                    if any(role in current_roles for role in ("fullmaster", "aiadmin")):
                        with ui.expansion(tr("settings.section.ai"), icon='smart_toy', value=False).classes('w-full'):

                            ui.label(tr("settings.ai.limits")).style("color: var(--accent-color);")
                            max_iter_value = get_setting("global", "agent_max_iterations", 25, current_state)[3] or 25
                            session_actions_value = get_setting("global", "agent_session_max_actions", 40, current_state)[3] or 40
                            session_tokens_value = get_setting("global", "agent_session_token_budget", 200000, current_state)[3] or 200000
                            with ui.row().classes('items-end gap-2 flex-wrap'):
                                agent_iter_input = ui.number(label=tr("settings.ai.maxiter"),
                                                             value=int(max_iter_value), min=1, max=100, step=1).classes('w-60')
                                agent_session_actions_input = ui.number(label=tr("settings.ai.session_actions"),
                                                             value=int(session_actions_value), min=1, max=1000, step=1).classes('w-60')
                                agent_session_tokens_input = ui.number(label=tr("settings.ai.session_tokens"),
                                                             value=int(session_tokens_value), min=0, step=1000).classes('w-60')

                                def save_ai_limits():
                                    try:
                                        val = int(agent_iter_input.value or 25)
                                    except BaseException:
                                        val = 25
                                    val = max(1, min(val, 100))
                                    result = set_setting("global", "agent_max_iterations", val, current_state)
                                    if not result[0]:
                                        ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                        return
                                    try:
                                        sa = max(1, min(int(agent_session_actions_input.value or 40), 1000))
                                    except BaseException:
                                        sa = 40
                                    try:
                                        st = max(0, int(agent_session_tokens_input.value or 0))
                                    except BaseException:
                                        st = 0
                                    set_setting("global", "agent_session_max_actions", sa, current_state)
                                    set_setting("global", "agent_session_token_budget", st, current_state)
                                    ui.notify(tr("settings.ai.limits_saved"), type="positive")

                                ui.button(tr("settings.ai.save_limits"), icon='save', on_click=save_ai_limits).classes('hover-glow')

                            ui.separator()
                            ui.label(tr("settings.ai.log")).style("color: var(--accent-color);")
                            ui.markdown(tr("settings.ai.log_hint")).classes('text-xs opacity-60')
                            ai_summary_grid = ui.aggrid({}).classes('w-full').style('height: 22vh')
                            ui.label(tr("settings.ai.detail")).classes('text-xs opacity-60')
                            ai_log_grid = ui.aggrid({}).classes('w-full').style('height: 35vh')

                            def refresh_ai_log():
                                log_result = get_ai_log(current_state, 2000)
                                entries = log_result[3] if log_result[0] else []
                                if not log_result[0]:
                                    ui.notify(tr("settings.ai.log_fail", error=log_result[1]), type="negative")

                                # сводка по пользователям
                                summary = {}
                                for entry in entries:
                                    bucket = summary.setdefault(entry["username"], {
                                        "username": entry["username"], "requests": 0,
                                        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
                                    bucket["requests"] += 1
                                    bucket["prompt_tokens"] += entry.get("prompt_tokens") or 0
                                    bucket["completion_tokens"] += entry.get("completion_tokens") or 0
                                    bucket["total_tokens"] += entry.get("total_tokens") or 0
                                summary_rows = sorted(summary.values(), key=lambda x: x["total_tokens"], reverse=True)
                                ai_summary_grid.options["columnDefs"] = [
                                    {"headerName": tr("settings.ai.col.user"), "field": "username", "filter": True, "sortable": True, "resizable": True, "minWidth": 160},
                                    {"headerName": tr("settings.ai.col.requests"), "field": "requests", "filter": True, "sortable": True, "resizable": True, "minWidth": 110},
                                    {"headerName": tr("settings.ai.col.in"), "field": "prompt_tokens", "filter": True, "sortable": True, "resizable": True, "minWidth": 130},
                                    {"headerName": tr("settings.ai.col.out"), "field": "completion_tokens", "filter": True, "sortable": True, "resizable": True, "minWidth": 130},
                                    {"headerName": tr("settings.ai.col.total"), "field": "total_tokens", "filter": True, "sortable": True, "resizable": True, "minWidth": 130},
                                ]
                                ai_summary_grid.options["rowData"] = summary_rows
                                ai_summary_grid.options["domLayout"] = "normal"
                                ai_summary_grid.options["enableBrowserTooltips"] = True
                                ai_summary_grid.update()

                                # детализация
                                ai_log_grid.options["columnDefs"] = [
                                    {"headerName": tr("settings.ai.dcol.time"), "field": "timestamp", "filter": True, "sortable": True, "resizable": True, "minWidth": 180},
                                    {"headerName": tr("settings.ai.col.user"), "field": "username", "filter": True, "sortable": True, "resizable": True, "minWidth": 140},
                                    {"headerName": tr("settings.ai.dcol.model"), "field": "model", "filter": True, "sortable": True, "resizable": True, "minWidth": 160, "tooltipField": "model"},
                                    {"headerName": tr("settings.ai.dcol.provider"), "field": "provider", "filter": True, "sortable": True, "resizable": True, "minWidth": 120},
                                    {"headerName": tr("settings.ai.dcol.in"), "field": "prompt_tokens", "filter": True, "sortable": True, "resizable": True, "minWidth": 90},
                                    {"headerName": tr("settings.ai.dcol.out"), "field": "completion_tokens", "filter": True, "sortable": True, "resizable": True, "minWidth": 90},
                                    {"headerName": tr("settings.ai.dcol.total"), "field": "total_tokens", "filter": True, "sortable": True, "resizable": True, "minWidth": 90},
                                    {"headerName": tr("settings.ai.dcol.ms"), "field": "duration_ms", "filter": True, "sortable": True, "resizable": True, "minWidth": 90},
                                    {"headerName": tr("settings.ai.dcol.ok"), "field": "ok", "filter": True, "sortable": True, "resizable": True, "minWidth": 70},
                                ]
                                ai_log_grid.options["rowData"] = entries
                                ai_log_grid.options["defaultColDef"] = {"filter": True, "sortable": True, "resizable": True, "minWidth": 90}
                                ai_log_grid.options["pagination"] = True
                                ai_log_grid.options["paginationPageSize"] = 20
                                ai_log_grid.options["enableCellTextSelection"] = True
                                ai_log_grid.options["enableBrowserTooltips"] = True
                                ai_log_grid.options["domLayout"] = "normal"
                                ai_log_grid.update()

                            ui.button(tr("settings.ai.refresh_log"), icon='refresh', on_click=refresh_ai_log).props('outline')
                            refresh_ai_log()

                        # ──────────── Разрешённые сети (IP-whitelist) — роли fullmaster / netadmin ────────────

                    if any(role in current_roles for role in ("fullmaster", "netadmin")):
                        with ui.expansion(tr("settings.section.networks"), icon='lan', value=False).classes('w-full'):
                            ui.markdown(tr("settings.net.hint")).classes('text-xs text-orange-400')

                            net_grid = ui.aggrid({}).classes('w-full').style('height: 30vh')
                            net_selected = {"cidr": None, "comment": None}

                            def _allow_truthy(value):
                                return value in (1, True) or str(value).lower() in ("1", "true", "t", "yes")

                            def refresh_net_grid():
                                net_result = get_access_networks(current_state)
                                rows = []
                                if net_result[0]:
                                    for network in net_result[3]:
                                        rows.append({
                                            "cidr": network.get("cidr", ""),
                                            "allow": (tr("settings.common.yes") if _allow_truthy(network.get("allow")) else tr("settings.common.no")),
                                            "comment": network.get("comment", ""),
                                        })
                                net_grid.options["columnDefs"] = [
                                    {"headerName": tr("settings.net.col.cidr"), "field": "cidr", "filter": True, "sortable": True, "resizable": True, "minWidth": 180, "tooltipField": "cidr"},
                                    {"headerName": tr("settings.net.col.allow"), "field": "allow", "filter": True, "sortable": True, "resizable": True, "minWidth": 120},
                                    {"headerName": tr("settings.col.comment"), "field": "comment", "filter": True, "sortable": True, "resizable": True, "minWidth": 220, "tooltipField": "comment"},
                                ]
                                net_grid.options["rowData"] = rows
                                net_grid.options["rowSelection"] = "single"
                                net_grid.options["defaultColDef"] = {"filter": True, "sortable": True, "resizable": True, "minWidth": 140}
                                net_grid.options["enableCellTextSelection"] = True
                                net_grid.options["enableBrowserTooltips"] = True
                                net_grid.options["domLayout"] = "normal"
                                net_grid.update()

                            async def on_net_selected():
                                row = (await net_grid.get_selected_row()) or {}
                                net_selected["cidr"] = row.get("cidr")
                                net_selected["comment"] = row.get("comment")

                            net_grid.on("selectionChanged", on_net_selected)

                            with ui.row().classes('items-end gap-2 w-full flex-wrap'):
                                new_net_cidr = ui.input(label=tr("settings.net.cidr_label")).classes('w-64')
                                new_net_comment = ui.input(label=tr("settings.common.comment")).classes('grow')
                                new_net_allow = ui.checkbox(tr("settings.net.allow"), value=True)

                            def add_network():
                                cidr = (new_net_cidr.value or "").strip()
                                comment = (new_net_comment.value or "").strip()
                                import ipaddress
                                try:
                                    ipaddress.ip_network(cidr, strict=False)
                                except BaseException:
                                    ui.notify(tr("settings.net.bad_cidr"), type="negative")
                                    return
                                if not validate_comment(comment, current_state)[0]:
                                    ui.notify(tr("settings.net.bad_comment"), type="negative")
                                    return
                                result = create_access_network(cidr, new_net_allow.value, comment, current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                new_net_cidr.value = ""
                                new_net_comment.value = ""
                                ui.notify(tr("settings.net.added", cidr=cidr), type="positive")
                                refresh_net_grid()

                            def delete_network():
                                if not net_selected["cidr"]:
                                    ui.notify(tr("settings.net.pick"), type="warning")
                                    return
                                result = delete_access_network(net_selected["cidr"], net_selected["comment"] or "", current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                ui.notify(tr("settings.net.deleted", cidr=net_selected['cidr']), type="positive")
                                net_selected["cidr"] = None
                                net_selected["comment"] = None
                                refresh_net_grid()

                            with ui.row().classes('gap-2 flex-wrap'):
                                ui.button(tr("settings.net.add"), icon='add', on_click=add_network).classes('hover-glow')
                                ui.button(tr("settings.net.delete"), icon='delete', on_click=delete_network).props('outline')
                                ui.button(tr("settings.btn.refresh"), icon='refresh', on_click=refresh_net_grid).props('flat')

                            refresh_net_grid()

                        # ──────────── API-ключи (роли fullmaster / apiadmin) ────────────

                    if any(role in current_roles for role in ("fullmaster", "apiadmin")):
                        with ui.expansion(tr("settings.section.apikeys"), icon='vpn_key', value=False).classes('w-full'):
                            ui.markdown(tr("settings.api.hint")).classes('text-xs opacity-60')

                            keys_grid = ui.aggrid({}).classes('w-full').style('height: 28vh')
                            key_selected = {"key_hash": None, "enabled": None}

                            def _truthy(value):
                                return value in (1, True) or str(value).lower() in ("1", "true", "t", "yes")

                            def refresh_keys_grid():
                                keys_result = list_api_keys(current_state)
                                rows = []
                                if keys_result[0]:
                                    now = currentTimestamp()
                                    for key in keys_result[3]:
                                        expires_at = key.get("expires_at") or ""
                                        expired = bool(expires_at) and now >= expires_at
                                        enabled = _truthy(key.get("enabled"))
                                        rows.append({
                                            "owner": key.get("owner", ""),
                                            "comment": key.get("comment", ""),
                                            "status": (tr("settings.api.expired") if expired else (tr("settings.api.active") if enabled else tr("settings.api.disabled"))),
                                            "created_at": key.get("created_at", "") or "",
                                            "created_by": key.get("created_by", "") or "",
                                            "expires_at": expires_at or tr("settings.api.never"),
                                            "hash_prefix": (key.get("key_hash", "") or "")[:12] + "…",
                                            "key_hash": key.get("key_hash", ""),
                                            "_enabled": enabled,
                                        })
                                keys_grid.options["columnDefs"] = [
                                    {"headerName": tr("settings.api.col.owner"), "field": "owner", "filter": True, "sortable": True, "resizable": True, "minWidth": 130},
                                    {"headerName": tr("settings.col.comment"), "field": "comment", "filter": True, "sortable": True, "resizable": True, "minWidth": 180, "tooltipField": "comment"},
                                    {"headerName": tr("settings.api.col.status"), "field": "status", "filter": True, "sortable": True, "resizable": True, "minWidth": 100},
                                    {"headerName": tr("settings.api.col.created"), "field": "created_at", "filter": True, "sortable": True, "resizable": True, "minWidth": 180},
                                    {"headerName": tr("settings.api.col.createdby"), "field": "created_by", "filter": True, "sortable": True, "resizable": True, "minWidth": 130},
                                    {"headerName": tr("settings.api.col.expires"), "field": "expires_at", "filter": True, "sortable": True, "resizable": True, "minWidth": 180},
                                    {"headerName": tr("settings.api.col.hash"), "field": "hash_prefix", "filter": True, "sortable": True, "resizable": True, "minWidth": 140},
                                ]
                                keys_grid.options["rowData"] = rows
                                keys_grid.options["rowSelection"] = "single"
                                keys_grid.options["defaultColDef"] = {"filter": True, "sortable": True, "resizable": True, "minWidth": 110}
                                keys_grid.options["enableCellTextSelection"] = True
                                keys_grid.options["enableBrowserTooltips"] = True
                                keys_grid.options["domLayout"] = "normal"
                                keys_grid.update()

                            async def on_key_selected():
                                row = (await keys_grid.get_selected_row()) or {}
                                key_selected["key_hash"] = row.get("key_hash")
                                key_selected["enabled"] = row.get("_enabled")

                            keys_grid.on("selectionChanged", on_key_selected)

                            with ui.row().classes('items-end gap-2 w-full flex-wrap'):
                                new_key_owner = ui.input(label=tr("settings.api.owner_label")).classes('w-56')
                                new_key_comment = ui.input(label=tr("settings.common.comment")).classes('grow')
                                new_key_ttl = ui.number(label=tr("settings.api.ttl"), min=0, step=1).classes('w-64')

                            def create_api_key_action():
                                owner = (new_key_owner.value or "").strip()
                                if not owner:
                                    ui.notify(tr("settings.api.need_owner"), type="negative")
                                    return
                                owner_user = get_user_by_username(owner, current_state)
                                if not owner_user[0]:
                                    ui.notify(tr("settings.api.owner_notfound", owner=owner), type="negative")
                                    return
                                comment = (new_key_comment.value or "").strip()
                                ttl_days = new_key_ttl.value or 0
                                result = create_api_key(owner, comment, username, ttl_days, current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                token = result[3]
                                new_key_owner.value = ""
                                new_key_comment.value = ""
                                new_key_ttl.value = None
                                refresh_keys_grid()
                                with ui.dialog() as token_dialog, ui.card().classes('w-[36rem] max-w-full'):
                                    ui.label(tr("settings.api.created_title")).style('font-weight:700; color: var(--title-color);')
                                    ui.markdown(tr("settings.api.created_hint")).classes('text-xs opacity-60')
                                    ui.input(label="API key", value=token).props('readonly').classes('w-full')
                                    ui.button(tr("settings.btn.close"), on_click=token_dialog.close).classes('hover-glow')
                                token_dialog.open()

                            def toggle_key_enabled():
                                if not key_selected["key_hash"]:
                                    ui.notify(tr("settings.api.pick"), type="warning")
                                    return
                                result = set_api_key_enabled(key_selected["key_hash"], not key_selected["enabled"], current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                ui.notify((tr("settings.api.enabled_msg") if not key_selected["enabled"] else tr("settings.api.disabled_msg")), type="positive")
                                key_selected["enabled"] = not key_selected["enabled"]
                                refresh_keys_grid()

                            def delete_api_key_action():
                                if not key_selected["key_hash"]:
                                    ui.notify(tr("settings.api.pick"), type="warning")
                                    return
                                result = delete_api_key(key_selected["key_hash"], current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
                                key_selected["key_hash"] = None
                                ui.notify(tr("settings.api.deleted"), type="positive")
                                refresh_keys_grid()

                            with ui.row().classes('gap-2 flex-wrap'):
                                ui.button(tr("settings.api.create"), icon='vpn_key', on_click=create_api_key_action).classes('hover-glow')
                                ui.button(tr("settings.api.toggle"), icon='power_settings_new', on_click=toggle_key_enabled).props('outline')
                                ui.button(tr("settings.api.delete_key"), icon='delete', on_click=delete_api_key_action).props('outline')
                                ui.button(tr("settings.btn.refresh"), icon='refresh', on_click=refresh_keys_grid).props('flat')

                            refresh_keys_grid()

        return True, "Ok", currentFuncName(), None

    except BaseException as e:
        try:
            with interface_container:
                ui.label(translate("settings.error", current_state.get("lang", DEFAULT_LANGUAGE), error=str(e))).classes('text-red-400')
        except BaseException:
            pass
        return False, str(e), currentFuncName(), None

def draw_objects(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))

        interface_container.clear()

        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)

        current_user = current_state.get("username", "unknown")

        get_user_by_username_result = get_user_by_username(current_user, current_state)
        if not get_user_by_username_result[0]:
            logger_log(syslog.LOG_ERR, get_log_message(get_user_by_username_result[1], currentFuncName(), current_state))
            ui.notify(get_user_by_username_result[1], type="negative")
            return False, get_user_by_username_result[1], currentFuncName(), None

        current_user = get_user_by_username_result[3]

        has_object_admin = False
        if "objects_admin" in current_user["roles"]:
            has_object_admin = True
        if "fullmaster" in current_user["roles"]:
            has_object_admin = True
        
        if not has_object_admin:
            with interface_container:
                ui.label(tr("objects.no_role"))
                return False, f"There is not object_admin role for username {current_user}", currentFuncName(), None

        # Обновление таблицы объектов
        def update_grid_objects_list(grid, current_state):
            # получить из БД список объектов
            get_all_actual_objects_result = get_all_actual_objects(current_state)
            if not get_all_actual_objects_result[0]:
                logger_log(syslog.LOG_ERR, get_log_message(get_all_actual_objects_result[1], currentFuncName(), current_state))
                ui.notify(get_all_actual_objects_result[1], type="negative")
                return
            actual_objects = get_all_actual_objects_result[3]

            # собрать grid data
            grid_data = [
                {#["name", "roles", "version", "timestamp", "type", "owner"]
                    "name": object["name"],
                    "type": object["type"],
                    "version": object["version"],
                    "timestamp": object["timestamp"],
                    "owner": object["owner"],
                    "roles": json.dumps(object["roles"], indent=0, ensure_ascii=False)
                } for object in actual_objects
            ]

            # обновить объект
            grid.options['columnDefs'] = [
                    {"headerName": tr("objects.col.name"), "field": "name", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": tr("objects.col.type"), "field": "type", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": tr("objects.col.version"), "field": "version", "filter": True, "sortable": True, "minWidth": 60},
                    {"headerName": tr("objects.col.timestamp"), "field": "timestamp", "filter": True, "sortable": True, "minWidth": 200},
                    {"headerName": tr("objects.col.owner"), "field": "owner", "filter": True, "sortable": True, "minWidth": 120},
                    {"headerName": tr("objects.col.roles"), "field": "roles", "filter": True, "sortable": True, "minWidth": 120},
                ]
            grid.options['rowData'] = grid_data
            grid.options['defaultColDef'] = {
                    "wrapText": True,
                    "autoHeight": True,
                }
            grid.options['rowSelection'] = "single"
            grid.options['pagination'] = True
            grid.options['enableCellTextSelection'] = True
            grid.options['paginationPageSize'] = 20
            grid.options['domLayout'] = "normal"

            grid.update()
            return
        
        # Обновление таблицы версий объекта
        def update_grid_object_versions(object_name, grid, current_state):
            # получить из БД список объектов
            get_all_object_versions_result = get_all_object_versions(object_name, current_state)
            if not get_all_object_versions_result[0]:
                logger_log(syslog.LOG_ERR, get_log_message(get_all_object_versions_result[1], currentFuncName(), current_state))
                ui.notify(get_all_object_versions_result[1], type="negative")
                return
            object_versions = get_all_object_versions_result[3]

            # собрать grid data
            grid_data = [
                {#["name", "roles", "version", "timestamp", "type", "owner"]
                    "name": object["name"],
                    "type": object["type"],
                    "version": object["version"],
                    "timestamp": object["timestamp"],
                    "owner": object["owner"],
                    "roles": json.dumps(object["roles"], indent=0, ensure_ascii=False)
                } for object in object_versions
            ]

            # обновить объект
            grid.options['columnDefs'] = [
                    {"headerName": tr("objects.col.name"), "field": "name", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": tr("objects.col.type"), "field": "type", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": tr("objects.col.version"), "field": "version", "filter": True, "sortable": True, "minWidth": 60},
                    {"headerName": tr("objects.col.timestamp"), "field": "timestamp", "filter": True, "sortable": True, "minWidth": 200},
                    {"headerName": tr("objects.col.owner"), "field": "owner", "filter": True, "sortable": True, "minWidth": 120},
                    {"headerName": tr("objects.col.roles"), "field": "roles", "filter": True, "sortable": True, "minWidth": 120},
                ]
            grid.options['rowData'] = grid_data
            grid.options['defaultColDef'] = {
                    "wrapText": True,
                    "autoHeight": True,
                }
            grid.options['rowSelection'] = "single"
            grid.options['pagination'] = True
            grid.options['enableCellTextSelection'] = True
            grid.options['paginationPageSize'] = 20
            grid.options['domLayout'] = "normal"

            grid.update()
            return

        # Клик по строке объекта в таблице всех актуальных объектов
        async def grid_objects_list_click():
            selected_row = (await grid_objects_list.get_selected_row()) or {}
            if not selected_row:
                return
            update_grid_object_versions(selected_row["name"], grid_object_versions, current_state)
            object_panels.set_value('Object info')
            #ui.notify(selected_row, type="positive")



        # Клик по версии объекта
        async def grid_object_versions_click():
            selected_row = (await grid_object_versions.get_selected_row()) or {}
            if not selected_row:
                return
            
            get_object_by_name_and_version_result = get_object_by_name_and_version(selected_row["name"], selected_row["version"], current_state)
            if not get_object_by_name_and_version_result[0]:
                logger_log(syslog.LOG_ERR, get_log_message(get_object_by_name_and_version_result[1], currentFuncName(), current_state))
                ui.notify(get_object_by_name_and_version_result[1], type="negative")
                return
            selected_object_version = get_object_by_name_and_version_result[3]

            codemirror_show_object_version.value = json.dumps(selected_object_version["json"], indent=4, ensure_ascii=False)
            codemirror_edit_object_version.value = json.dumps(selected_object_version["json"], indent=4, ensure_ascii=False)
            label_edit_object_name.text = selected_object_version["name"]
            select_edit_object_type.set_value(selected_object_version["type"])
            input_edit_object_roles.value = json.dumps(selected_object_version["roles"], indent=0, ensure_ascii=False)

        # кнопка сохранения новой версии объекта
        async def save_button_of_object_editor():
            # получаем текущие значения до изменения
            selected_row = (await grid_object_versions.get_selected_row()) or {}
            if not selected_row:
                return
            
            get_object_by_name_and_version_result = get_object_by_name_and_version(selected_row["name"], selected_row["version"], current_state)
            if not get_object_by_name_and_version_result[0]:
                logger_log(syslog.LOG_ERR, get_log_message(get_object_by_name_and_version_result[1], currentFuncName(), current_state))
                ui.notify(get_object_by_name_and_version_result[1], type="negative")
                return
            selected_object_version = get_object_by_name_and_version_result[3]
            # проверяем, что в roles валидный список
            if not json_validate(input_edit_object_roles.value):
                ui.notify(tr("objects.roles_invalid"), type="negative")
                return
            # проверяем, что в codemirror валидный json
            if not json_validate(codemirror_edit_object_version.value):
                ui.notify(tr("objects.json_invalid"), type="negative")
                return
            # проверяем, что в codemirror именно dict
            if not isinstance(json.loads(codemirror_edit_object_version.value), dict):
                ui.notify(tr("objects.json_not_dict"), type="negative")
                return
            # проверяем, есть ли изменения
            if selected_object_version["type"] == select_edit_object_type.value and selected_object_version["roles"] == json.loads(input_edit_object_roles.value) and json.dumps(selected_object_version["json"], indent=4, ensure_ascii=False) == codemirror_edit_object_version.value:
                # полное совпадение, изменений нет
                ui.notify(tr("objects.no_changes"), type="negative")
            else:
                create_new_object_version_result = create_new_object_version(selected_object_version["name"], select_edit_object_type.value, json.loads(input_edit_object_roles.value), json.loads(codemirror_edit_object_version.value), current_state)
                if not create_new_object_version_result[0]:
                    ui.notify(f"{create_new_object_version_result[1]}", type="negative")
                    return
                ui.notify(tr("objects.version_saved", name=selected_row["name"]), type="positive")
                update_grid_object_versions(selected_row["name"], grid_object_versions, current_state)
                object_panels.set_value('Object info')
            # если есть, то создаём новую версию объекта

        # кнопка создания объекта
        def create_button_object():
            #сначала проверяем, заполнено ли имя
            if input_new_object_name.value == "":
                ui.notify(tr("objects.empty_name"), type="negative")
                return
            # проверка имени на корректность
            validate_itemname_result = validate_itemname(input_new_object_name.value, current_state)
            if not validate_itemname_result[0]:
                ui.notify(f"{validate_itemname_result[1]}", type="negative")
                return
            # выбор типа
            if select_new_object_type.value not in ["script", "source", "notifier", "llm"]:
                ui.notify(tr("objects.wrong_type"), type="negative")
                return
            # а заполнены ли роли?
            if input_new_object_roles.value == "":
                ui.notify(tr("objects.empty_roles"), type="negative")
                return
            # проверяем, что в roles валидный список
            if not json_validate(input_new_object_roles.value):
                ui.notify(tr("objects.roles_invalid"), type="negative")
                return
            # проверяем, что это точно список
            if not isinstance(json.loads(input_new_object_roles.value), list):
                ui.notify(tr("objects.roles_invalid"), type="negative")
                return
            # проверяем, что есть хотя бы одна роль
            if not len(json.loads(input_new_object_roles.value)) > 0:
                ui.notify(tr("objects.empty_roles_list"), type="negative")
                return
            # проверяем, что в codemirror валидный json
            if not json_validate(codemirror_create_new_object.value):
                ui.notify(tr("objects.json_invalid"), type="negative")
                return
            # проверяем, что в codemirror именно dict
            if not isinstance(json.loads(codemirror_create_new_object.value), dict):
                ui.notify(tr("objects.json_not_dict"), type="negative")
                return
            
            # проверяем, есть ли объект с таким именем
            get_all_object_versions_result = get_all_object_versions(input_new_object_name.value, current_state)
            if get_all_object_versions_result[0] == True:
                ui.notify(tr("objects.name_used"), type="negative")
                return
            else:
                if get_all_object_versions_result[1] != "object not found":
                    ui.notify(tr("objects.name_test_error"), type="negative")
                    return
                else:
                    # имя точно уникальное, записываем в базу новый объект
                    create_new_object_result = create_new_object(input_new_object_name.value, select_new_object_type.value, json.loads(input_new_object_roles.value), json.loads(codemirror_create_new_object.value), current_state)
                    if not create_new_object_result[0]:
                        ui.notify(tr("objects.create_error"), type="negative")
                        return
                    ui.notify(tr("objects.done"), type="positive")
                    update_grid_objects_list(grid_objects_list, current_state)
            
        with interface_container:
            with ui.tabs().classes('w-full') as tabs:
                tab_objects_list = ui.tab('Objects list', label=tr("objects.tab.list"))
                tab_one_object = ui.tab('Object info', label=tr("objects.tab.info"))
                tab_object_editor = ui.tab('Object editor', label=tr("objects.tab.editor"))
                tab_object_creator = ui.tab('Object creator', label=tr("objects.tab.creator"))
            with ui.tab_panels(tabs, value=tab_objects_list).classes('w-full h-full') as object_panels:
                with ui.tab_panel(tab_objects_list):
                    grid_objects_list = ui.aggrid({}).classes('h-[calc(85vh-100px)]')
                    grid_objects_list.on("selectionChanged", grid_objects_list_click)

                with ui.tab_panel(tab_one_object):
                    grid_object_versions = ui.aggrid({})
                    grid_object_versions.on("selectionChanged", grid_object_versions_click)
                    codemirror_show_object_version = make_codemirror(current_state)
                    

                with ui.tab_panel(tab_object_editor):
                    with ui.row():
                        label_edit_object_name = ui.label(tr("objects.field.name"))
                        select_edit_object_type = ui.select(["script", "source", "notifier", "llm"], value="script")
                        input_edit_object_roles = ui.input(label=tr("objects.field.roles"))
                        button_save_new_object_version = ui.button(tr("objects.btn.save"))
                        button_save_new_object_version.on_click(save_button_of_object_editor)
                        button_delete_object = ui.button(tr("objects.btn.delete"))
                        #button_delete_object.on_click(save_button_of_object_editor)
                    codemirror_edit_object_version = make_codemirror(current_state)
                    

                with ui.tab_panel(tab_object_creator):
                    with ui.row():
                        input_new_object_name = ui.input(label=tr("objects.field.name"))
                        select_new_object_type = ui.select(["script", "source", "notifier", "llm"], value="script")
                        input_new_object_roles = ui.input(label=tr("objects.field.roles"))
                        input_new_object_roles.value = '["default"]'
                        button_create_new_object = ui.button(tr("objects.btn.create"))
                        button_create_new_object.on_click(create_button_object)
                    codemirror_create_new_object = make_codemirror(current_state)

        update_grid_objects_list(grid_objects_list, current_state)

        return True, "OK", currentFuncName(), None

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    
def draw_harvester(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))

        interface_container.clear()

        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)

        current_user = current_state.get("username", "unknown")

        get_user_by_username_result = get_user_by_username(current_user, current_state)
        if not get_user_by_username_result[0]:
            logger_log(syslog.LOG_ERR, get_log_message(get_user_by_username_result[1], currentFuncName(), current_state))
            ui.notify(get_user_by_username_result[1], type="negative")
            return False, get_user_by_username_result[1], currentFuncName(), None

        current_user = get_user_by_username_result[3]

        # has_object_admin = False
        # if "objects_admin" in current_user["roles"]:
        #     has_object_admin = True
        # if "fullmaster" in current_user["roles"]:
        #     has_object_admin = True
        
        # if not has_object_admin:
        #     with interface_container:
        #         ui.label('You do not have object_admin role')
        #         return False, f"There is not object_admin role for username {current_user}", currentFuncName(), None
        
        def _render_print(command, variables, result_map):
            # PRINT всегда что-то рендерит (текст/таблица/значение) -> успех
            arg = (command.get("print_arg") or "").strip()
            # литерал в кавычках -> текст-комментарий
            if len(arg) >= 2 and ((arg[0] == arg[-1] == '"') or (arg[0] == arg[-1] == "'")):
                ui.markdown(arg[1:-1], extras=['tables', 'fenced-code-blocks'])
                return True
            # ссылка на таблицу данных (результат GET)?
            data = None
            if arg in result_map and result_map[arg][0]:
                data = result_map[arg][3]
            elif arg in variables:
                value = variables[arg]
                if isinstance(value, list) and (len(value) == 0 or isinstance(value[0], dict)):
                    data = value
                else:
                    ui.markdown(f"**{_md_escape(arg)}** = `{json.dumps(value, ensure_ascii=False, default=str)}`")
                    return True
            if data is not None:
                ui.markdown(records_to_markdown(data), extras=['tables', 'fenced-code-blocks'])
                return True
            # иначе — просто текст
            ui.markdown(arg, extras=['tables', 'fenced-code-blocks'])
            return True

        def _render_show(command, variables, result_map):
            table = (command.get("show_table") or "").strip()
            show_type = (command.get("show_type") or "table").strip().strip('"\'').lower()
            params_raw = (command.get("show_params") or "").strip()

            data = None
            if table in result_map and result_map[table][0]:
                data = result_map[table][3]
            elif isinstance(variables.get(table), list):
                data = variables[table]

            if not data:
                reason = tr("harv.show.no_data", table=table)
                command["_info"] = reason
                ui.markdown(f"*SHOW: {_md_escape(reason)}*")
                return False

            if show_type == "table":
                ui.aggrid(records_to_aggrid_options(data, current_state.get("aggrid_theme", "ag-theme-balham-dark"))).classes('w-full h-[60vh]')
                return True
            elif show_type in ("matplotlib", "plot"):
                params = {}
                if params_raw:
                    if json_validate(params_raw):
                        params = json.loads(params_raw)
                    else:
                        command["_info"] = tr("harv.show.bad_params")
                        ui.markdown(f"*SHOW: {_md_escape(command['_info'])}*")
                        return False
                try:
                    plot = render_plot_png_b64(data, params)
                    ui.image(f"data:image/png;base64,{plot['b64']}").style(
                        f"width: {plot['css_w']}px; max-width: 100%; height: auto")
                    return True
                except BaseException as plot_error:
                    command["_info"] = f"matplotlib error: {str(plot_error)}"
                    ui.markdown(f"*SHOW {_md_escape(command['_info'])}*")
                    return False
            else:
                command["_info"] = tr("harv.show.bad_type", type=show_type)
                ui.markdown(f"*SHOW: {_md_escape(command['_info'])}*")
                return False

        def _render_save(command, variables, result_map):
            # SAVE→storage исполняется движком (commands_executor), не как файловая выгрузка:
            # статус/сообщение уже проставлены, здесь только показываем результат.
            if command.get("save_is_storage"):
                info = command.get("_info") or tr("harv.save.stored", name=command.get("storage_key", "?"))
                ui.markdown(f"*SAVE storage: {_md_escape(info)}*")
                return command.get("_status") != "error"

            tables = command.get("save_tables") or []
            fmt = (command.get("save_format") or "").strip().strip('"\'').lower()
            save_filename = command.get("save_filename")

            # собираем данные по каждой таблице (с сохранением порядка)
            tables_data = {}
            missing = []
            for table in tables:
                if table in result_map and result_map[table][0]:
                    tables_data[table] = result_map[table][3]
                elif isinstance(variables.get(table), list):
                    tables_data[table] = variables[table]
                else:
                    missing.append(table)

            if missing:
                command["_info"] = tr("harv.save.no_data", tables=', '.join(missing))
                ui.markdown(f"*SAVE: {_md_escape(command['_info'])}*")
                return False
            if not tables_data:
                command["_info"] = tr("harv.save.no_tables")
                ui.markdown(f"*SAVE: {_md_escape(command['_info'])}*")
                return False

            # базовое имя файла: AS filename -> имя таблицы (если одна) -> 'export'
            base_name = save_filename or (tables[0] if len(tables) == 1 else "export")

            try:
                content, filename, media_type = records_to_download(tables_data, fmt, base_name)
            except BaseException as save_error:
                command["_info"] = str(save_error)
                ui.markdown(f"*SAVE error: {_md_escape(command['_info'])}*")
                return False

            try:
                ui.download(content, filename, media_type)
            except TypeError:
                ui.download(content, filename)
            total = sum(len(d) for d in tables_data.values())
            ui.markdown(tr("harv.save.downloading", filename=_md_escape(filename), tables=len(tables_data), rows=total))
            return True

        def _update_datavars(variables, result_map):
            rows = []
            for name, res in result_map.items():
                count = len(res[3]) if (res[0] and isinstance(res[3], list)) else 0
                rows.append({"name": name, "kind": "data", "rows": count})
            for name, value in variables.items():
                rows.append({"name": name, "kind": "variable",
                             "rows": (len(value) if isinstance(value, (list, dict)) else 1)})
            grid_datavars.options['columnDefs'] = [
                {"headerName": tr("harv.dv.name"), "field": "name", "filter": True, "sortable": True},
                {"headerName": tr("harv.dv.kind"), "field": "kind", "filter": True, "sortable": True},
                {"headerName": tr("harv.dv.rows"), "field": "rows", "filter": True, "sortable": True},
            ]
            grid_datavars.options['rowData'] = rows
            grid_datavars.options['domLayout'] = "normal"
            grid_datavars.update()
            codemirror_datavar.value = json.dumps(variables, ensure_ascii=False, indent=2, default=str)

        def _render_steps():
            steps = current_state.get("ui_steps") or []
            steps_panel.clear()
            if not steps:
                return
            with steps_panel:
                for command in steps:
                    state = command.get("_status", "pending")
                    info = command.get("_info", "")
                    # info показываем и в running (напр. прогресс APPLY «k/total»)
                    suffix = f" — {info}" if (state in ("done", "error", "warning", "running") and info) else ""
                    with ui.row().classes('items-center gap-2 no-wrap'):
                        if state == "running":
                            ui.spinner(size='sm')
                        else:
                            ui.label(STEP_ICONS.get(state, "·"))
                        ui.label(f"{_step_label(command)}{suffix}").classes('text-sm').style(
                            "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif);")

        async def button_script_click():
            execution_start = time.monotonic()
            spinner = current_state.get("ui_spinner")
            status = current_state.get("ui_status")
            steps_timer = None
            # нулевой шаг — валидация скрипта (отражает ошибки, найденные до выполнения шагов)
            validation_step = {"command": "VALIDATE", "_status": "running", "_info": ""}
            current_state["ui_steps"] = [validation_step]
            _render_steps()
            if spinner is not None:
                spinner.visible = True
            if status is not None:
                status.set_text(tr("harv.running"))
            try:
                parsed_command = command_parser(codemirror_script.value, current_state)

                # вывод ошибок парсинга
                parse_errors = [(i, c) for i, c in enumerate(parsed_command) if not c.get("parsed", True)]
                if parse_errors:
                    validation_step["_status"] = "error"
                    validation_step["_info"] = "; ".join(
                        f"L{c.get('line_number', '?')} #{i + 1} {c.get('command', '?')}: {c.get('parsed_comment', '?')}" for i, c in parse_errors)
                    card_results.clear()
                    with card_results:
                        ui.markdown(tr("harv.parse_errors"))
                        for i, c in parse_errors:
                            ui.markdown(tr("harv.parse_error_item", line=c.get('line_number', '?'), n=i + 1, cmd=c.get('command', '?'), comment=_md_escape(c.get('parsed_comment', '?'))))
                    return

                # прогресс шагов: инициализация статусов + таймер живого опроса (поток пишет статусы)
                for c in parsed_command:
                    c["_status"] = "pending"
                    c["_info"] = ""
                current_state["ui_steps"] = [validation_step] + parsed_command
                _render_steps()
                steps_timer = ui.timer(0.25, _render_steps)

                # пре-флайт (валидация) считается пройденным с началом выполнения;
                # при провале до старта шагов вернём error ниже
                validation_step["_status"] = "done"

                commands_executor_result = await run.io_bound(commands_executor, parsed_command, current_state)
                if not commands_executor_result[0]:
                    logger_log(syslog.LOG_ERR, get_log_message(commands_executor_result[1], currentFuncName(), current_state))
                    # если конкретный шаг уже помечен error — ошибка на нём, валидация остаётся done;
                    # иначе это ошибка пре-флайта/резолва -> помечаем валидацию
                    if not any(c.get("_status") == "error" for c in parsed_command):
                        validation_step["_status"] = "error"
                        validation_step["_info"] = commands_executor_result[1]
                    # все ещё не выполненные шаги отклонены; зависший на running (упавший без
                    # терминального статуса) помечаем error — он не должен крутиться вечно
                    for command in parsed_command:
                        if command.get("_status") == "pending":
                            command["_status"] = "rejected"
                        elif command.get("_status") == "running":
                            command["_status"] = "error"
                            if not command.get("_info"):
                                command["_info"] = commands_executor_result[1]
                    card_results.clear()
                    with card_results:
                        ui.markdown(tr("harv.exec_error", error=_md_escape(commands_executor_result[1])))
                    ui.notify(commands_executor_result[1], type="negative")
                    return

                variables, result_map = commands_executor_result[3]
                # DEF/CALC помечены движком; PRINT/SHOW/SAVE статус получают по результату рендера ниже

                # последовательный вывод PRINT/SHOW в порядке их следования в скрипте
                card_results.clear()
                with card_results:
                    rendered = 0
                    for command in parsed_command:
                        if command["command"] == "PRINT":
                            ok = _render_print(command, variables, result_map)
                        elif command["command"] == "SHOW":
                            ok = _render_show(command, variables, result_map)
                        elif command["command"] == "SAVE":
                            ok = _render_save(command, variables, result_map)
                        else:
                            continue
                        command["_status"] = "done" if ok else "error"
                        rendered += 1
                    if rendered == 0:
                        ui.markdown(tr("harv.no_output"))

                _update_datavars(variables, result_map)
                ui.notify(tr("harv.done"), type="positive")

            except BaseException as e:
                error_message = f"fail: {str(e)}"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                if validation_step["_status"] == "running":
                    validation_step["_status"] = "error"
                    validation_step["_info"] = error_message
                # оставшиеся шаги отклонены; зависший на running помечаем error (не крутится вечно)
                try:
                    for command in parsed_command:
                        if command.get("_status") == "pending":
                            command["_status"] = "rejected"
                        elif command.get("_status") == "running":
                            command["_status"] = "error"
                            if not command.get("_info"):
                                command["_info"] = error_message
                except NameError:
                    pass
                ui.notify(error_message, type="negative")
            finally:
                if steps_timer is not None:
                    try:
                        steps_timer.cancel()
                    except Exception:
                        steps_timer.active = False
                _render_steps()
                if spinner is not None:
                    spinner.visible = False
                if status is not None:
                    status.set_text("")
                # запись запуска в историю (executions) + обновление вкладки History
                try:
                    history_steps = [{
                        "command": step_command.get("command"),
                        "label": _step_label(step_command),
                        "status": step_command.get("_status", "pending"),
                        "info": step_command.get("_info", ""),
                    } for step_command in (current_state.get("ui_steps") or [])]
                    execution_success = 1 if all(s["status"] not in ("error", "rejected") for s in history_steps) else 0
                    create_execution(str(uuid.uuid4()), current_state.get("username", "unknown"), execution_success,
                                     {"script": codemirror_script.value, "steps": history_steps,
                                      "duration_seconds": round(time.monotonic() - execution_start, 3)}, current_state)
                    history_refresh = current_state.get("ui_history_refresh")
                    if history_refresh is not None:
                        history_refresh()
                except BaseException as history_error:
                    logger_log(syslog.LOG_ERR, get_log_message(f"history record fail: {str(history_error)}", currentFuncName(), current_state))

        # вся панель Harvester прокручивается ОДНИМ вертикальным скроллбаром.
        # q-card по умолчанию overflow:hidden -> переопределяем на auto и задаём высоту по вьюпорту.
        interface_container.style('height: calc(100vh - 110px); overflow-y: auto; overflow-x: hidden')
        with interface_container:
            ui.add_css('.mermaid { text-align: center; } .mermaid svg { display: block; margin-left: auto; margin-right: auto; }')
            # перенос длинных строк в редакторе скрипта (визуальный; CSS-фолбэк к line_wrapping)
            ui.add_css('.uh-cm-wrap .cm-content, .uh-cm-wrap .cm-line { white-space: pre-wrap !important; overflow-wrap: anywhere; word-break: break-word; }')
            # ручное растягивание окна редактора скрипта по вертикали: грип на внешней обёртке,
            # чтобы она росла в потоке и смещала кнопки/нижние элементы вниз; .cm-editor заполняет её
            ui.add_css('.uh-cm-resize { resize: vertical; overflow: auto; min-height: 120px; height: 30vh; max-height: 85vh; }'
                       ' .uh-cm-resize .cm-editor { height: 100%; }')
            with ui.column().classes('w-full no-wrap'):
                analysis_holder = {}

                def analyze_click():
                    # статический анализ потока выполнения -> Mermaid-граф (с вложенными скриптами)
                    try:
                        mermaid_text = build_execution_mermaid(codemirror_script.value, current_state)
                        analysis_holder["m"].content = mermaid_text
                        analysis_holder["m"].update()
                        analysis_holder["ready"] = True
                        harvester_panels.set_value('Analysis')
                    except BaseException as analyze_error:
                        ui.notify(tr("harv.analyze_error", error=str(analyze_error)), type="negative")

                async def export_graph(fmt):
                    # экспорт ВСЕГО графа (а не скриншота видимой части): берём уже отрисованный mermaid <svg>
                    # на клиенте и сохраняем как SVG (вектор) или PNG (растеризация всего bbox через canvas).
                    if fmt == "svg":
                        js = r"""
                        (function(){
                          const svg = document.querySelector('.uh-exec-graph svg');
                          if(!svg){ return 'nograph'; }
                          const xml = new XMLSerializer().serializeToString(svg);
                          const blob = new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n'+xml], {type:'image/svg+xml;charset=utf-8'});
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement('a'); a.href=url; a.download='execution_schema.svg';
                          document.body.appendChild(a); a.click(); a.remove();
                          setTimeout(function(){ URL.revokeObjectURL(url); }, 1000);
                          return 'ok';
                        })()
                        """
                    else:
                        js = r"""
                        (function(){
                          const svg = document.querySelector('.uh-exec-graph svg');
                          if(!svg){ return 'nograph'; }
                          const rect = svg.getBoundingClientRect();
                          const w = Math.max(1, Math.ceil(rect.width)), h = Math.max(1, Math.ceil(rect.height));
                          const xml = new XMLSerializer().serializeToString(svg);
                          const svg64 = btoa(unescape(encodeURIComponent(xml)));
                          const img = new Image();
                          img.onload = function(){
                            const scale = 2;  // ретина-резкость
                            const canvas = document.createElement('canvas');
                            canvas.width = w*scale; canvas.height = h*scale;
                            const ctx = canvas.getContext('2d');
                            ctx.scale(scale, scale);
                            ctx.fillStyle = getComputedStyle(document.body).backgroundColor || '#ffffff';
                            ctx.fillRect(0, 0, w, h);
                            ctx.drawImage(img, 0, 0, w, h);
                            canvas.toBlob(function(blob){
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement('a'); a.href=url; a.download='execution_schema.png';
                              document.body.appendChild(a); a.click(); a.remove();
                              setTimeout(function(){ URL.revokeObjectURL(url); }, 1000);
                            }, 'image/png');
                          };
                          img.src = 'data:image/svg+xml;base64,' + svg64;
                          return 'ok';
                        })()
                        """
                    try:
                        result = await ui.run_javascript(js, timeout=10.0)
                    except BaseException:
                        result = None
                    if result == "nograph":
                        ui.notify(tr("harv.export_empty"), type="warning")
                    else:
                        ui.notify(tr("harv.export_done", fmt=fmt.upper()), type="positive")

                def export_mermaid_text():
                    # сохранить исходник Mermaid (текст схемы) как .mmd
                    if not analysis_holder.get("ready"):
                        ui.notify(tr("harv.export_empty"), type="warning")
                        return
                    content = analysis_holder["m"].content or ""
                    ui.download(content.encode("utf-8"), "execution_schema.mmd", "text/plain")
                    ui.notify(tr("harv.export_done", fmt="Mermaid"), type="positive")

                with ui.tabs().classes('w-full') as tabs:
                    tab_script = ui.tab('Scripts', label=tr("harv.tab.scripts"))
                    tab_datavars = ui.tab('Data/Variables', label=tr("harv.tab.datavars"))
                    tab_analysis = ui.tab('Analysis', label=tr("harv.tab.analysis"))
                with ui.tab_panels(tabs, value=tab_script).classes('w-full') as harvester_panels:
                    with ui.tab_panel(tab_script):
                        # сворачиваемый блок скрипта (вместе с кнопками Execute/Анализ) — освобождает место под результаты
                        with ui.expansion(tr("harv.script"), icon='code', value=True).classes('w-full'):
                            codemirror_script = make_codemirror(current_state, line_wrapping=True).classes('w-full uh-cm-wrap uh-cm-resize')
                            with ui.row().classes('gap-2'):
                                button_script = ui.button(tr("harv.execute"), icon='rocket_launch').on_click(button_script_click)
                                button_analyze = ui.button(tr("harv.analyze"), icon='account_tree').on_click(analyze_click)
                        # сворачиваемый блок прогресса шагов (вариант A): список команд со статусами
                        with ui.expansion(tr("harv.steps"), icon='list', value=True).classes('w-full'):
                            steps_panel = ui.element('div').classes('w-full').style('padding: 4px 8px')
                        # область результатов (горизонтальный скролл — для широких таблиц; вертикальный — на всю панель)
                        card_results = ui.element('div').classes('w-full').style('overflow-x: auto; padding: 8px; border: 1px solid var(--panel-bg)')

                    # хуки для AI-раздела: предзаполнить редактор скриптом и/или запустить его
                    def _harvester_load(script_text):
                        codemirror_script.value = script_text or ""
                        harvester_panels.set_value('Scripts')
                    current_state["ui_harvester_load"] = _harvester_load
                    current_state["ui_harvester_run"] = button_script_click

                    with ui.tab_panel(tab_datavars):
                        grid_datavars = ui.aggrid({}).classes('w-full').style('height: 60vh')
                        codemirror_datavar = make_codemirror(current_state).classes('w-full')

                    with ui.tab_panel(tab_analysis):
                        with ui.row().classes('gap-2 q-mb-sm'):
                            ui.button(tr("harv.export_svg"), icon='download').props('size=sm').on_click(lambda: export_graph("svg"))
                            ui.button(tr("harv.export_png"), icon='image').props('size=sm').on_click(lambda: export_graph("png"))
                            ui.button(tr("harv.export_mmd"), icon='description').props('size=sm').on_click(export_mermaid_text)
                        analysis_holder["m"] = ui.mermaid('flowchart TD\n    start(["нажмите «Анализ выполнения»"])').classes('w-full uh-exec-graph').style('min-height: 60vh')

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    
def run_agent_script(script_text, current_state):
    """Выполнить сгенерированный агентом скрипт и вернуть компактную сводку результата (для LLM).
    Запуск идёт от имени текущего пользователя (по его ролям); пишется в историю с пометкой agent."""
    try:
        agent_run_start = time.monotonic()
        parsed = command_parser(script_text, current_state)
        parse_errors = [c for c in parsed if not c.get("parsed", True)]
        if parse_errors:
            return "ОШИБКА ПАРСИНГА: " + "; ".join(
                f"строка {c.get('line_number', '?')} {c.get('command', '?')}: {c.get('parsed_comment', '?')}" for c in parse_errors)

        # на этапе подготовки агент НЕ выполняет SHOW/SAVE — это вывод для человека;
        # все данные агенту возвращаются ниже (переменные + таблицы как list of dict)
        skipped_human_output = sorted({c.get("command") for c in parsed if c.get("command") in ("SHOW", "SAVE")})
        parsed = [c for c in parsed if c.get("command") not in ("SHOW", "SAVE")]

        for c in parsed:
            c["_status"] = "pending"
            c["_info"] = ""
        result = commands_executor(parsed, current_state)

        steps_text = "\n".join(
            f"  {STEP_ICONS.get(c.get('_status', 'pending'), '·')} {_step_label(c)}"
            + (f" — {c.get('_info')}" if c.get("_info") else "")
            for c in parsed)

        # запись в историю как агентский запуск (с длительностью)
        try:
            history_steps = [{"command": c.get("command"), "label": _step_label(c),
                              "status": c.get("_status", "pending"), "info": c.get("_info", "")} for c in parsed]
            create_execution(str(uuid.uuid4()), current_state.get("username", "unknown"),
                             1 if result[0] else 0,
                             {"script": script_text, "steps": history_steps, "agent": True,
                              "duration_seconds": round(time.monotonic() - agent_run_start, 3)}, current_state)
            history_refresh = current_state.get("ui_history_refresh")
            if history_refresh is not None:
                history_refresh()
        except BaseException:
            pass

        if not result[0]:
            return f"ВЫПОЛНЕНИЕ ПРЕРВАНО: {result[1]}\nШаги:\n{steps_text}"

        variables, result_map = result[3]
        data_lines = []
        for name, res in result_map.items():
            if res[0] and isinstance(res[3], list):
                rows = res[3]
                columns = list(rows[0].keys())[:40] if rows and isinstance(rows[0], dict) else []
                sample = json.dumps(rows[:5], ensure_ascii=False, default=str)
                data_lines.append(f"  - {name}: {len(rows)} строк; колонки: {columns}\n    первые строки (list of dict): {sample}")
        data_text = "\n".join(data_lines) if data_lines else "  (нет табличных данных)"
        var_text = json.dumps(variables, ensure_ascii=False, default=str)[:800] if variables else "—"
        skipped_note = f"\n(SHOW/SAVE пропущены на этапе подготовки: {', '.join(skipped_human_output)})" if skipped_human_output else ""
        return (f"ВЫПОЛНЕНИЕ ОК{skipped_note}\nШаги:\n{steps_text}\n"
                f"Данные (все таблицы скрипта, list of dict):\n{data_text}\nПеременные: {var_text}")

    except BaseException as e:
        return f"ОШИБКА ВЫПОЛНЕНИЯ: {str(e)}"


def draw_history(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    """История запусков скриптов (таблица executions): список + просмотр скрипта и шагов."""
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))
        interface_container.clear()
        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)
        current_user = current_state.get("username", "unknown")
        is_fullmaster = "fullmaster" in current_state.get("roles", [])
        history_cache = {"executions": []}   # кэш загруженных записей (для поиска без обращений в БД)

        def apply_history_filter():
            search_text = (search_history_input.value or "").strip().lower()
            grid_data = []
            for e in history_cache["executions"]:
                script = e.get("script") or ""
                if search_text and search_text not in script.lower():
                    continue
                preview = " ".join(script.split())[:140]
                grid_data.append({
                    "timestamp": e["timestamp"],
                    "owner": e["owner"],
                    "source": tr("history.source.agent") if e.get("agent") else tr("history.source.manual"),
                    "status": tr("history.status.ok") if e["status"] == 1 else tr("history.status.fail"),
                    "duration": f'{e["duration"]:.3f}' if isinstance(e.get("duration"), (int, float)) else "",
                    "script": preview,
                    "id": e["id"],
                })
            grid_history.options['columnDefs'] = [
                {"headerName": tr("history.col.timestamp"), "field": "timestamp", "filter": True, "sortable": True, "minWidth": 210},
                {"headerName": tr("history.col.user"), "field": "owner", "filter": True, "sortable": True, "minWidth": 120},
                {"headerName": tr("history.col.source"), "field": "source", "filter": True, "sortable": True, "minWidth": 100},
                {"headerName": tr("history.col.status"), "field": "status", "filter": True, "sortable": True, "minWidth": 90},
                {"headerName": tr("history.col.duration"), "field": "duration", "filter": True, "sortable": True, "minWidth": 110},
                {"headerName": tr("history.col.script"), "field": "script", "filter": True, "sortable": True, "minWidth": 320, "tooltipField": "script"},
                {"headerName": tr("history.col.id"), "field": "id", "filter": True, "sortable": True, "minWidth": 280},
            ]
            grid_history.options['rowData'] = grid_data
            grid_history.options['rowSelection'] = "single"
            grid_history.options['pagination'] = True
            grid_history.options['paginationPageSize'] = 20
            grid_history.options['enableCellTextSelection'] = True
            grid_history.options['enableBrowserTooltips'] = True
            grid_history.options['domLayout'] = "normal"
            grid_history.update()

        def update_history_grid():
            owner = None if is_fullmaster else current_user
            get_executions_result = get_executions(owner, current_state)
            history_cache["executions"] = get_executions_result[3] if get_executions_result[0] else []
            apply_history_filter()

        async def grid_history_click():
            selected_row = (await grid_history.get_selected_row()) or {}
            if not selected_row:
                return
            get_execution_by_id_result = get_execution_by_id(selected_row["id"], current_state)
            if not get_execution_by_id_result[0]:
                ui.notify(get_execution_by_id_result[1], type="negative")
                return
            execution = get_execution_by_id_result[3]
            execution_json = execution.get("json", {}) or {}
            codemirror_history.value = execution_json.get("script", "")
            steps_history_panel.clear()
            with steps_history_panel:
                duration = execution_json.get("duration_seconds")
                duration_text = tr("history.dur_suffix", sec=duration) if isinstance(duration, (int, float)) else ""
                ui.markdown(tr("history.detail", status=(tr("history.status.ok") if execution['status'] == 1 else tr("history.status.fail")), owner=execution.get('owner', '?'), ts=execution['timestamp'], dur=duration_text))
                for step in execution_json.get("steps", []):
                    icon = STEP_ICONS.get(step.get("status", "pending"), "·")
                    info = step.get("info", "")
                    suffix = f" — {info}" if info else ""
                    ui.label(f"{icon} {step.get('label', step.get('command', '?'))}{suffix}").classes('text-sm').style(
                        "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif);")

        with interface_container:
            with ui.column().classes('w-full no-wrap').style('height: calc(100vh - 130px); overflow-y: auto; overflow-x: hidden'):
                with ui.row().classes('items-center w-full'):
                    ui.label(tr("history.title")).classes('text-lg')
                    ui.button(tr("history.refresh"), icon='refresh').on_click(lambda: update_history_grid())
                    search_history_input = ui.input(tr("history.search")).classes('grow').on('keydown.enter', lambda: apply_history_filter())
                    ui.button(icon='search').on_click(lambda: apply_history_filter())
                grid_history = ui.aggrid({}).classes('w-full').style('height: 35vh')
                grid_history.on("selectionChanged", grid_history_click)
                ui.label(tr("history.script_label"))
                codemirror_history = make_codemirror(current_state).classes('w-full').style('max-height: 25vh')
                steps_history_panel = ui.element('div').classes('w-full').style('padding: 4px 8px')

        # ссылку на обновление кладём в current_state — Harvester дёрнет её после запуска
        current_state["ui_history_refresh"] = update_history_grid
        update_history_grid()

        return True, "OK", currentFuncName(), None
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None


def draw_ai(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))

        interface_container.clear()

        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)

        current_user = current_state.get("username", "unknown")

        get_user_by_username_result = get_user_by_username(current_user, current_state)
        if not get_user_by_username_result[0]:
            logger_log(syslog.LOG_ERR, get_log_message(get_user_by_username_result[1], currentFuncName(), current_state))
            ui.notify(get_user_by_username_result[1], type="negative")
            return False, get_user_by_username_result[1], currentFuncName(), None

        current_user = get_user_by_username_result[3]

        # has_object_admin = False
        # if "objects_admin" in current_user["roles"]:
        #     has_object_admin = True
        # if "fullmaster" in current_user["roles"]:
        #     has_object_admin = True
        
        # if not has_object_admin:
        #     with interface_container:
        #         ui.label('You do not have object_admin role')
        #         return False, f"There is not object_admin role for username {current_user}", currentFuncName(), None
        
        def list_llm_objects():
            get_all_actual_objects_result = get_all_actual_objects(current_state)
            objects = get_all_actual_objects_result[3] if get_all_actual_objects_result[0] else []
            return [o["name"] for o in objects if o.get("type") == "llm"]

        async def select_llm_and_check():
            name = select_llm.value
            if not name:
                return
            get_object_result = get_actual_object_by_name(name, "('llm')", current_state)
            if not get_object_result[0]:
                current_state["ui_selected_llm"] = None
                llm_status_label.set_text(f"❌ {get_object_result[1]}")
                return
            llm_json = get_object_result[3]["json"]
            llm_status_label.set_text(tr("ai.checking"))
            ok, message = await run.io_bound(llm_health_check, llm_json, current_state)
            current_state["ui_selected_llm"] = {"name": name, "json": llm_json, "ready": ok}
            llm_status_label.set_text(("✅ " if ok else "❌ ") + message + tr("ai.context_suffix", n=llm_context_window(llm_json)))

        with interface_container:
            with ui.column().classes('w-full no-wrap').style('height: calc(100vh - 130px); overflow-y: auto; overflow-x: hidden'):
                with ui.row().classes('items-center w-full'):
                    select_llm = ui.select(list_llm_objects(), label="LLM", with_input=True).classes('grow')
                    select_llm.on_value_change(lambda: select_llm_and_check())
                    ui.button(tr("ai.refresh"), icon='refresh').on_click(lambda: select_llm.set_options(list_llm_objects()))
                    ui.button(tr("ai.check"), icon='check').on_click(select_llm_and_check)
                llm_status_label = ui.label(tr("ai.not_selected")).classes('text-sm').style(
                    "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif);")

                # окно чата с агентом
                ui.separator()
                # история чата переживает перезагрузку страницы (per-user storage nicegui)
                try:
                    conversation = list(app.storage.user.get("ai_conversation", []) or [])
                except BaseException:
                    conversation = []

                def persist_conversation():
                    try:
                        app.storage.user["ai_conversation"] = list(conversation)
                    except BaseException:
                        pass
                # сессионные счётчики (в памяти панели): действия агента и приблизительные токены
                session_state = {"actions": 0, "tokens": 0, "cancel": False}
                counters_label = ui.label("").classes('text-xs opacity-70')

                def _session_limits():
                    try:
                        max_actions = int(get_setting("global", "agent_session_max_actions", 40, current_state)[3] or 40)
                    except BaseException:
                        max_actions = 40
                    try:
                        token_budget = int(get_setting("global", "agent_session_token_budget", 200000, current_state)[3] or 0)
                    except BaseException:
                        token_budget = 0
                    return max(1, max_actions), max(0, token_budget)

                def update_counters():
                    max_actions, token_budget = _session_limits()
                    budget_text = str(token_budget) if token_budget > 0 else "∞"
                    counters_label.set_text(tr("ai.counters", actions=session_state["actions"], max_actions=max_actions,
                                               tokens=session_state["tokens"], budget=budget_text))

                def render_chat():
                    chat_display.clear()
                    with chat_display:
                        if not conversation:
                            ui.markdown(tr("ai.empty_chat"))
                        for message in conversation:
                            content = message.get("content", "")
                            if message["role"] == "user" and content.startswith("РЕЗУЛЬТАТ ДЕЙСТВИЯ"):
                                who = tr("ai.who.action")
                            elif message["role"] == "user":
                                who = tr("ai.who.you")
                            else:
                                who = tr("ai.who.agent")
                            # code-friendly: '_' в именах (get_source_functions, source_object) не даёт курсив
                            ui.markdown(f"{who}\n\n{content}", extras=['tables', 'fenced-code-blocks', 'code-friendly'])
                    render_final_actions()
                    persist_conversation()

                def _current_final_script():
                    """Скрипт из последнего блока ```harvester``` последнего ответа агента (для кнопок)."""
                    for message in reversed(conversation):
                        if message.get("role") == "assistant":
                            return extract_final_harvester(message.get("content", ""))
                    return None

                def save_script_dialog(script_text, default_name=""):
                    """Диалог сохранения скрипта как script-объекта (создать/новая версия)."""
                    with ui.dialog() as dialog, ui.card().classes('w-[40rem] max-w-full'):
                        ui.label(tr("ai.save.title")).style("font-weight:700; color: var(--title-color);")
                        name_input = ui.input(tr("ai.save.name"), value=default_name).classes('w-full')
                        return_input = ui.input(tr("ai.save.return"), value="").classes('w-full')
                        roles_input = ui.input(tr("ai.save.roles"), value='["fullmaster"]').classes('w-full')

                        def do_save():
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify(tr("ai.save.need_name"), type="warning")
                                return
                            if not json_validate(roles_input.value or "[]"):
                                ui.notify(tr("settings.roles.array"), type="negative")
                                return
                            roles = json.loads(roles_input.value or "[]")
                            if not isinstance(roles, list):
                                ui.notify(tr("settings.roles.array_strings"), type="negative")
                                return
                            if not _role_allowed(roles):
                                ui.notify(tr("ai.save.roles_too_high"), type="negative")
                                return
                            obj_json = {"script": script_text, "return": (return_input.value or "").strip()}
                            existing = get_actual_object_by_name(name, "('script')", current_state)
                            if existing[0] and existing[3]:
                                result = create_new_object_version(name, "script", roles, obj_json, current_state)
                            else:
                                result = create_new_object(name, "script", roles, obj_json, current_state)
                            if not result[0]:
                                ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                return
                            ui.notify(tr("ai.save.saved", name=name), type="positive")
                            dialog.close()

                        with ui.row().classes('gap-2'):
                            ui.button(tr("ai.save.save"), icon='save', on_click=do_save)
                            ui.button(tr("settings.btn.close"), on_click=dialog.close).props('flat')
                    dialog.open()

                def render_final_actions():
                    final_actions.clear()
                    script = _current_final_script()
                    if not script:
                        return
                    with final_actions:
                        ui.label(tr("ai.final.label")).classes('text-xs opacity-70')
                        with ui.row().classes('gap-2 flex-wrap'):
                            ui.button(tr("ai.final.open"), icon='rocket_launch').props('size=sm').on_click(lambda: open_in_harvester(script, run=False))
                            ui.button(tr("ai.final.run"), icon='play_arrow').props('size=sm').on_click(lambda: open_in_harvester(script, run=True))
                            ui.button(tr("ai.final.save"), icon='save').props('size=sm').on_click(lambda: save_script_dialog(script))

                async def handle_save_object(argument):
                    """Действие агента save_object: разбор + подтверждение пользователем + запись объекта.
                    Возврат — текст результата для агента (продолжает цикл)."""
                    ok, err, norm = parse_save_object(argument)
                    if not ok:
                        return tr("ai.saveobj.rejected", reason=err)
                    if not _role_allowed(norm["roles"]):
                        return tr("ai.saveobj.rejected", reason=tr("ai.save.roles_too_high"))
                    with ui.dialog() as confirm_dialog, ui.card().classes('w-[44rem] max-w-full'):
                        ui.label(tr("ai.saveobj.title")).style("font-weight:700; color: var(--title-color);")
                        ui.markdown(tr("ai.saveobj.preview", name=norm["name"], roles=json.dumps(norm["roles"], ensure_ascii=False),
                                       ret=norm["json"].get("return") or "—"))
                        ui.markdown("```\n" + norm["json"]["script"] + "\n```", extras=['fenced-code-blocks'])
                        with ui.row().classes('gap-2'):
                            ui.button(tr("ai.saveobj.confirm"), icon='save', on_click=lambda: confirm_dialog.submit('confirm'))
                            ui.button(tr("ai.saveobj.reject"), on_click=lambda: confirm_dialog.submit('reject')).props('flat')
                    decision = await confirm_dialog
                    if decision != 'confirm':
                        return tr("ai.saveobj.user_rejected")
                    existing = get_actual_object_by_name(norm["name"], "('script')", current_state)
                    if existing[0] and existing[3]:
                        result = create_new_object_version(norm["name"], "script", norm["roles"], norm["json"], current_state)
                    else:
                        result = create_new_object(norm["name"], "script", norm["roles"], norm["json"], current_state)
                    if not result[0]:
                        return tr("ai.saveobj.rejected", reason=result[1])
                    return tr("ai.saveobj.done", name=norm["name"])

                async def open_in_harvester(script_text, run=False):
                    load = current_state.get("ui_harvester_load")
                    show = current_state.get("ui_show_panel")
                    if load is None or show is None:
                        ui.notify(tr("ai.final.no_harvester"), type="warning")
                        return
                    load(script_text)
                    show("Harvester")
                    if run:
                        run_fn = current_state.get("ui_harvester_run")
                        if run_fn is not None:
                            await run_fn()

                def _role_allowed(object_roles):
                    roles = current_state.get("roles", [])
                    return "fullmaster" in roles or any(r in (object_roles or []) for r in roles)

                def _script_params_summary(script_json):
                    """Параметры скрипта = его DEF с дефолтными значениями (+ что он возвращает)."""
                    body = (script_json or {}).get("script", "")
                    parsed = command_parser(body, current_state)
                    params = [f"{c['variable_name']}={json.dumps(c.get('variable_value'), ensure_ascii=False, default=str)}"
                              for c in parsed if c.get("command") == "DEF" and "variable_name" in c]
                    return ", ".join(params) if params else "—"

                def action_list_objects(type_filter):
                    get_all_actual_objects_result = get_all_actual_objects(current_state)
                    objects = get_all_actual_objects_result[3] if get_all_actual_objects_result[0] else []
                    type_filter = (type_filter or "").strip() or None
                    lines = []
                    for o in objects:
                        if not _role_allowed(o.get("roles")):
                            continue
                        if type_filter and o.get("type") != type_filter:
                            continue
                        line = f"- {o['name']} ({o.get('type', '?')})"
                        # для скриптов добавляем параметры (DEF) с дефолтами и return
                        if o.get("type") == "script":
                            full = get_actual_object_by_name(o["name"], "('script')", current_state)
                            if full[0]:
                                script_json = full[3].get("json", {}) or {}
                                line += f" — параметры (DEF с дефолтами): {_script_params_summary(script_json)}; return: {script_json.get('return', '?')}"
                        lines.append(line)
                    return "\n".join(lines) if lines else "объектов нет"

                def action_search_objects(query):
                    if not query:
                        return "укажите текст поиска"
                    search_result = search_actual_objects(query, current_state)
                    objects = search_result[3] if search_result[0] else []
                    lines = []
                    for o in objects:
                        if not _role_allowed(o.get("roles")):
                            continue
                        snippet = " ".join(str(o.get("json") or "").split())[:160]
                        lines.append(f"- {o['name']} ({o.get('type', '?')}): {snippet}")
                    return "\n".join(lines) if lines else "ничего не найдено"

                def action_get_object(name):
                    name = (name or "").strip()
                    if not name:
                        return "укажите имя объекта"
                    get_object_result = get_actual_object_by_name(name, "('source', 'script', 'notifier', 'llm')", current_state)
                    if not get_object_result[0]:
                        return f"объект '{name}' не найден"
                    obj = get_object_result[3]
                    if not _role_allowed(obj.get("roles")):
                        return f"нет доступа к объекту '{name}'"
                    header = f"{name} ({obj.get('type')}):"
                    if obj.get("type") == "script":
                        header += f"\nпараметры (DEF с дефолтами): {_script_params_summary(obj.get('json', {}))}"
                    return header + "\n" + json.dumps(obj.get("json", {}), ensure_ascii=False, indent=2)

                def action_memory_save(argument):
                    """memory_save: разобрать JSON и записать заметку в общую базу знаний (без подтверждения)."""
                    ok, err, norm = parse_memory_save(argument)
                    if not ok:
                        return f"не сохранено: {err}"
                    save_result = knowledge_save(norm["title"], norm["content"], norm["tags"], current_state)
                    if not save_result[0]:
                        return f"ошибка сохранения заметки: {save_result[1]}"
                    return f"заметка сохранена: «{norm['title']}» (id {save_result[3]})"

                def action_memory_search(query):
                    query = (query or "").strip()
                    if not query:
                        return "укажите текст поиска"
                    search_result = knowledge_search(query, current_state)
                    notes = search_result[3] if search_result[0] else []
                    if not notes:
                        return "заметок не найдено"
                    lines = []
                    for n in notes:
                        snippet = " ".join(str(n.get("content") or "").split())[:200]
                        tags = ", ".join(n.get("tags") or [])
                        lines.append(f"- «{n['title']}»" + (f" [{tags}]" if tags else "") + f": {snippet}")
                    return "\n".join(lines)

                def action_memory_list():
                    list_result = knowledge_list(current_state)
                    notes = list_result[3] if list_result[0] else []
                    if not notes:
                        return "память пуста"
                    return "\n".join(f"- «{n['title']}» (обновлено {n.get('updated_at', '?')})" for n in notes)

                def action_memory_get(key):
                    key = (key or "").strip()
                    if not key:
                        return "укажите title или id заметки"
                    get_result = knowledge_get(key, current_state)
                    if not get_result[0]:
                        return f"ошибка: {get_result[1]}"
                    note = get_result[3]
                    if not note:
                        return f"заметка «{key}» не найдена"
                    tags = ", ".join(note.get("tags") or [])
                    return f"«{note['title']}»" + (f" [{tags}]" if tags else "") + f"\n{note['content']}"

                def action_memory_delete(key):
                    key = (key or "").strip()
                    if not key:
                        return "укажите title или id заметки"
                    delete_result = knowledge_delete(key, current_state)
                    if not delete_result[0]:
                        return f"ошибка удаления: {delete_result[1]}"
                    return f"заметка «{key}» удалена (если существовала)"

                def dispatch_action(action, argument):
                    """Выполнить действие агента и вернуть текст результата (sync; вызывается через io_bound)."""
                    try:
                        if action == "run":
                            return run_agent_script(argument, current_state)
                        if action == "list_sources":
                            return list_source_types()
                        if action == "get_source_functions":
                            return describe_source_functions((argument or "").strip())
                        if action == "list_objects":
                            return action_list_objects(argument)
                        if action == "search_objects":
                            return action_search_objects(argument)
                        if action == "get_object":
                            return action_get_object(argument)
                        if action == "memory_save":
                            return action_memory_save(argument)
                        if action == "memory_search":
                            return action_memory_search(argument)
                        if action == "memory_list":
                            return action_memory_list()
                        if action == "memory_get":
                            return action_memory_get(argument)
                        if action == "memory_delete":
                            return action_memory_delete(argument)
                        return f"неизвестное действие: {action}"
                    except BaseException as e:
                        return f"ошибка действия {action}: {str(e)}"

                async def stream_reply(llm_json, messages):
                    """Ответ LLM с живым показом (стриминг); фолбэк на обычный llm_chat. -> (ok, reply, usage).
                    Реплика ассистента добавляется в conversation и наполняется по мере генерации."""
                    holder = {"text": ""}
                    holder_lock = threading.Lock()
                    conversation.append({"role": "assistant", "content": ""})
                    render_chat()

                    def on_chunk(delta):
                        with holder_lock:
                            holder["text"] += delta

                    def flush():
                        with holder_lock:
                            conversation[-1]["content"] = holder["text"]
                        render_chat()

                    stream_timer = ui.timer(0.2, flush)
                    try:
                        ok, reply, usage = await run.io_bound(llm_chat_stream, llm_json, messages, current_state, on_chunk)
                    finally:
                        try:
                            stream_timer.cancel()
                        except BaseException:
                            pass
                    if not ok and not holder["text"]:
                        # стрим ничего не дал -> фолбэк на обычный запрос
                        ok, reply, usage = await run.io_bound(llm_chat, llm_json, messages, current_state)
                    conversation[-1]["content"] = reply if ok else tr("ai.llm_error", error=reply)
                    render_chat()
                    return ok, reply, usage

                def _recall_memory(query):
                    """Подобрать релевантные заметки из общей памяти для авто-инъекции в промпт.
                    Возврат — форматированный текст (топ-N, усечённый) либо None. Ошибки БД не роняют чат."""
                    try:
                        list_result = knowledge_list(current_state)
                        if not list_result[0]:
                            return None
                        notes = rank_notes_by_query(list_result[3], query, limit=5)
                        if not notes:
                            return None
                        lines = []
                        for n in notes:
                            snippet = " ".join(str(n.get("content") or "").split())[:400]
                            tags = ", ".join(n.get("tags") or [])
                            lines.append(f"- «{n['title']}»" + (f" [{tags}]" if tags else "") + f": {snippet}")
                        return "\n".join(lines)
                    except BaseException:
                        return None

                async def send_message():
                    selected = current_state.get("ui_selected_llm")
                    if not selected or not selected.get("json"):
                        ui.notify(tr("ai.select_first"), type="negative")
                        return
                    user_text = (ai_chat_input.value or "").strip()
                    if not user_text:
                        return
                    session_state["cancel"] = False   # новый запрос снимает флаг остановки
                    conversation.append({"role": "user", "content": user_text})
                    ai_chat_input.value = ""
                    render_chat()

                    spinner = current_state.get("ui_ai_spinner")
                    status = current_state.get("ui_ai_status")
                    if spinner is not None:
                        spinner.visible = True
                    if status is not None:
                        status.set_text(tr("ai.thinking"))
                    try:
                        context_window = llm_context_window(selected["json"])
                        # авто-инъекция памяти: релевантные заметки по теме запроса (один раз на сообщение)
                        memory_context = await run.io_bound(_recall_memory, user_text)
                        # лимит цикла «ответ -> действие -> ответ» — глобальная настройка (Settings → AI)
                        try:
                            max_iterations = int(get_setting("global", "agent_max_iterations", 25, current_state)[3] or 25)
                        except BaseException:
                            max_iterations = 25
                        max_iterations = max(1, min(max_iterations, 100))
                        max_actions, token_budget = _session_limits()
                        for iteration in range(max_iterations):
                            if session_state["cancel"]:
                                conversation.append({"role": "assistant", "content": tr("ai.stopped")})
                                render_chat()
                                break
                            # сессионные лимиты: действия и токены — жёсткая остановка без нового запроса
                            if session_state["actions"] >= max_actions:
                                conversation.append({"role": "assistant", "content": tr("ai.limit_actions", n=max_actions)})
                                render_chat()
                                break
                            if token_budget and session_state["tokens"] >= token_budget:
                                conversation.append({"role": "assistant", "content": tr("ai.limit_tokens", n=token_budget)})
                                render_chat()
                                break
                            system_prompt = build_agent_system_prompt(memory_context)
                            messages = llm_build_messages(system_prompt, conversation, context_window)
                            ok, reply, usage = await stream_reply(selected["json"], messages)
                            session_state["tokens"] += (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                            update_counters()
                            if not ok:
                                break  # ошибка уже показана в ответе ассистента

                            action, argument = extract_action(reply)
                            if not action:
                                break  # финальный ответ (текст или ```harvester) — действий нет

                            session_state["actions"] += 1
                            update_counters()
                            if status is not None:
                                status.set_text(tr("ai.agent_action", action=action))
                            if action == "save_object":
                                # запись объекта — с подтверждением пользователя (нужен await UI)
                                action_result = await handle_save_object(argument)
                            else:
                                action_result = await run.io_bound(dispatch_action, action, argument)
                            action_result = llm_truncate_to_tokens(action_result, max(512, context_window // 4))
                            conversation.append({"role": "user", "content": f"РЕЗУЛЬТАТ ДЕЙСТВИЯ [{action}]:\n{action_result}"})
                            render_chat()
                            if status is not None:
                                status.set_text(tr("ai.thinking"))
                        else:
                            # лимит ИТЕРАЦИЙ на это сообщение исчерпан — просим финальный вариант без действий
                            conversation.append({"role": "user", "content":
                                "Достигнут лимит итераций на это сообщение. Приведи лучший финальный вариант скрипта в блоке "
                                "```harvester и краткий итог (что получилось, что осталось проверить вручную). Без действий."})
                            if not session_state["cancel"]:
                                system_prompt = build_agent_system_prompt(memory_context)
                                messages = llm_build_messages(system_prompt, conversation, context_window)
                                ok, reply, usage = await stream_reply(selected["json"], messages)
                                session_state["tokens"] += (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                                update_counters()
                    except BaseException as e:
                        conversation.append({"role": "assistant", "content": tr("ai.error", error=str(e))})
                        render_chat()
                    finally:
                        if spinner is not None:
                            spinner.visible = False
                        if status is not None:
                            status.set_text("")

                chat_display = ui.element('div').classes('w-full').style('flex: 1 1 auto; min-height: 30vh; overflow-y: auto; padding: 4px 8px; border: 1px solid var(--panel-bg)')
                final_actions = ui.element('div').classes('w-full').style('padding: 2px 8px')
                with ui.row().classes('w-full items-center'):
                    ai_chat_input = ui.input(tr("ai.input_placeholder")).classes('grow')
                    ai_chat_input.on('keydown.enter', lambda: send_message())
                    ui.button(tr("ai.send"), icon='send').on_click(send_message)

                    def stop_agent():
                        session_state["cancel"] = True
                        ui.notify(tr("ai.stop_requested"), type="warning")

                    def clear_chat():
                        conversation.clear()
                        session_state["actions"] = 0
                        session_state["tokens"] = 0
                        render_chat()
                        update_counters()

                    ui.button(tr("ai.stop"), icon='stop', color='negative').on_click(stop_agent)
                    ui.button(tr("ai.clear"), icon='delete').on_click(clear_chat)
                render_chat()
                update_counters()

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    
def draw_secrets(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))

        interface_container.clear()

        lang = current_state.get("lang", DEFAULT_LANGUAGE)
        tr = lambda key, **kw: translate(key, lang, **kw)

        current_user = current_state.get("username", "unknown")

        get_user_by_username_result = get_user_by_username(current_user, current_state)
        if not get_user_by_username_result[0]:
            logger_log(syslog.LOG_ERR, get_log_message(get_user_by_username_result[1], currentFuncName(), current_state))
            ui.notify(get_user_by_username_result[1], type="negative")
            return False, get_user_by_username_result[1], currentFuncName(), None

        current_user = get_user_by_username_result[3]

        has_object_admin = False
        if "secrets_admin" in current_user["roles"]:
            has_object_admin = True
        if "fullmaster" in current_user["roles"]:
            has_object_admin = True
        
        if not has_object_admin:
            with interface_container:
                ui.label(tr("secrets.no_role"))
                return False, f"There is not secrets_admin role for username {current_user}", currentFuncName(), None
            

        # Обновление таблицы секретов
        def update_grid_secrets_list(grid, current_state):
            # получить из БД список секретов
            db_get_secrets_list_result = db_get_secrets_list(current_state)
            if not db_get_secrets_list_result[0]:
                logger_log(syslog.LOG_ERR, get_log_message(db_get_secrets_list_result[1], currentFuncName(), current_state))
                ui.notify(db_get_secrets_list_result[1], type="negative")
                return
            
            actual_secrets = db_get_secrets_list_result[3]

            # собрать grid data
            grid_data = [
                {#["name", "roles", "version", "timestamp", "type", "owner"]
                    "system": object["system"],
                    "account": object["account"],
                    "secret": SECRET_MASK,
                    "comment": object["comment"]
                } for object in actual_secrets
            ]

            # обновить объект
            grid.options['columnDefs'] = [
                    {"headerName": tr("secrets.col.system"), "field": "system", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": tr("secrets.col.account"), "field": "account", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": tr("secrets.col.secret"), "field": "secret", "filter": True, "sortable": True, "minWidth": 60},
                    {"headerName": tr("secrets.col.comment"), "field": "comment", "filter": True, "sortable": True, "minWidth": 200},
                ]
            grid.options['rowData'] = grid_data
            grid.options['defaultColDef'] = {
                    "wrapText": True,
                    "autoHeight": True,
                }
            grid.options['rowSelection'] = "single"
            grid.options['pagination'] = True
            grid.options['enableCellTextSelection'] = True
            grid.options['paginationPageSize'] = 20
            grid.options['domLayout'] = "normal"

            grid.update()
            return
        
                # Клик по строке объекта в таблице всех актуальных объектов
        
        async def grid_secrets_list_click():
            selected_row = (await grid_secrets_list.get_selected_row()) or {}
            if not selected_row:
                return

            input_edit_secret_system.value  = selected_row["system"]
            input_edit_secret_account.value = selected_row["account"]
            input_edit_secret_secret.value  = selected_row["secret"]
            input_edit_secret_comment.value = selected_row["comment"]
            secrets_panels.set_value('Edit/create')

        async def save_button_of_secret_editor():
            # работаем по значениям полей формы (а не по выбранной строке таблицы),
            # чтобы работали и создание нового секрета, и редактирование существующего
            system  = input_edit_secret_system.value
            account = input_edit_secret_account.value
            secret  = input_edit_secret_secret.value
            comment = input_edit_secret_comment.value

            # валидация полей
            if not validate_itemname(system, current_state)[0]:
                ui.notify(tr("secrets.invalid.system"), type="negative")
                return
            if not validate_itemname(account, current_state)[0]:
                ui.notify(tr("secrets.invalid.account"), type="negative")
                return
            if not validate_comment(comment, current_state)[0]:
                ui.notify(tr("secrets.invalid.comment"), type="negative")
                return

            # получаем актуальный список секретов (пустая таблица -> пустой список, это не ошибка)
            db_get_secrets_list_result = db_get_secrets_list(current_state)
            if db_get_secrets_list_result[0]:
                actual_secrets = db_get_secrets_list_result[3]
            elif db_get_secrets_list_result[1] == "db table is empty?":
                actual_secrets = []
            else:
                logger_log(syslog.LOG_ERR, get_log_message(db_get_secrets_list_result[1], currentFuncName(), current_state))
                ui.notify(db_get_secrets_list_result[1], type="negative")
                return

            # ищем существующий секрет по паре system:account
            existing = None
            for s in actual_secrets:
                if s["system"] == system and s["account"] == account:
                    existing = s
                    break

            if existing is not None:
                # обновление существующего секрета
                if secret == SECRET_MASK:
                    # значение секрета не меняем -- обновляем только комментарий
                    result = update_secret_comment(system, account, comment, current_state)
                    success_message = tr("secrets.comment_updated", pair=f"{system}:{account}")
                else:
                    result = update_secret_secret_comment(system, account, comment, secret, current_state)
                    success_message = tr("secrets.secret_updated", pair=f"{system}:{account}")
            else:
                # создание нового секрета
                if secret == "" or secret == SECRET_MASK:
                    ui.notify(tr("secrets.empty"), type="negative")
                    return
                result = create_secret(system, account, comment, secret, current_state)
                success_message = tr("secrets.created", pair=f"{system}:{account}")

            if not result[0]:
                ui.notify(result[1], type="negative")
                return

            ui.notify(success_message, type="positive")
            update_grid_secrets_list(grid_secrets_list, current_state)
            secrets_panels.set_value('Secrets')

        # кнопка "New" -- очистить форму для создания нового секрета
        def new_button_of_secret_editor():
            input_edit_secret_system.value  = ""
            input_edit_secret_account.value = ""
            input_edit_secret_secret.value  = ""
            input_edit_secret_comment.value = ""

        # кнопка удаления секрета
        async def delete_button_of_secret_editor():
            system  = input_edit_secret_system.value
            account = input_edit_secret_account.value

            # валидация полей
            if not validate_itemname(system, current_state)[0]:
                ui.notify(tr("secrets.invalid.system"), type="negative")
                return
            if not validate_itemname(account, current_state)[0]:
                ui.notify(tr("secrets.invalid.account"), type="negative")
                return

            # получаем актуальный список секретов
            db_get_secrets_list_result = db_get_secrets_list(current_state)
            if db_get_secrets_list_result[0]:
                actual_secrets = db_get_secrets_list_result[3]
            elif db_get_secrets_list_result[1] == "db table is empty?":
                actual_secrets = []
            else:
                logger_log(syslog.LOG_ERR, get_log_message(db_get_secrets_list_result[1], currentFuncName(), current_state))
                ui.notify(db_get_secrets_list_result[1], type="negative")
                return

            exists = any(s["system"] == system and s["account"] == account for s in actual_secrets)
            if not exists:
                ui.notify(tr("secrets.not_found", pair=f"{system}:{account}"), type="negative")
                return

            delete_secret_result = delete_secret(system, account, current_state)
            if not delete_secret_result[0]:
                ui.notify(delete_secret_result[1], type="negative")
                return

            ui.notify(tr("secrets.deleted", pair=f"{system}:{account}"), type="positive")
            update_grid_secrets_list(grid_secrets_list, current_state)
            secrets_panels.set_value('Secrets')

        # скелет интерфейса
        with interface_container:
            with ui.tabs().classes('w-full') as tabs:
                tab_secrets = ui.tab('Secrets', label=tr("secrets.tab.list"))
                tab_edit_secrets = ui.tab('Edit/create', label=tr("secrets.tab.edit"))
            with ui.tab_panels(tabs, value=tab_secrets).classes('w-full h-full') as secrets_panels:
                with ui.tab_panel(tab_secrets):
                    grid_secrets_list = ui.aggrid({}).classes('h-[calc(85vh-100px)]')
                    grid_secrets_list.on("selectionChanged", grid_secrets_list_click)

                with ui.tab_panel(tab_edit_secrets):
                    input_edit_secret_system  = ui.input(label=tr("secrets.field.system"))
                    input_edit_secret_account = ui.input(label=tr("secrets.field.account"))
                    input_edit_secret_secret  = ui.input(label=tr("secrets.field.secret"), password=True)
                    input_edit_secret_comment = ui.input(label=tr("secrets.field.comment"))
                    with ui.row():
                        button_secret_new    = ui.button(tr("secrets.btn.new")).on_click(new_button_of_secret_editor)
                        button_secret_save   = ui.button(tr("secrets.btn.save")).on_click(save_button_of_secret_editor)
                        button_secret_delete = ui.button(tr("secrets.btn.delete")).on_click(delete_button_of_secret_editor)

        update_grid_secrets_list(grid_secrets_list, current_state)

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None