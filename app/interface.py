from app.login import try_login
from app.validation import check_current_user_status
from app.db import get_user_by_username, get_all_actual_objects, get_all_object_versions, get_object_by_name_and_version, get_actual_object_by_name, create_new_object_version, create_new_object, db_get_secrets_list, update_secret_comment, update_secret_secret_comment, create_secret, delete_secret, create_execution, get_executions, get_execution_by_id, search_actual_objects, get_setting, set_setting, settings_user_scope, set_user_password, update_user_metadata, get_user_session_epoch, set_user_enabled, list_users, create_user, set_user_roles, get_ai_log, get_access_networks, create_access_network, delete_access_network, create_api_key, list_api_keys, delete_api_key, set_api_key_enabled
from app.llm import llm_health_check, llm_context_window, build_agent_system_prompt, llm_build_messages, llm_chat, llm_truncate_to_tokens
import syslog
import asyncio
import json
import uuid
import time
from nicegui import ui, app, Client, run
from app.logging import get_log_message, logger_log, currentFuncName, currentTimestamp
from typing import Dict, Any, Tuple
from engine import commands_executor
from app.engine import command_parser, list_source_types, describe_source_functions
from app.i18n import translate, resolve_language, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE
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


def render_plot_png_b64(data, params):
    """Построить график matplotlib по данным и optional_params.

    Возвращает dict {b64, css_w, css_h}: PNG рендерится с высоким dpi (резкость),
    а css_w/css_h — «логический» размер для отображения (figsize в дюймах × 96px),
    чтобы браузер изображение уменьшал, а не растягивал (чётко на retina/HiDPI).

    optional_params: kind, x, y, title, figsize=[w_in,h_in], dpi (по умолчанию 150)."""
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
    fig, ax = plt.subplots(figsize=(figsize[0], figsize[1]))
    plot_kwargs = {"kind": params.get("kind", "line"), "ax": ax}
    if params.get("x") is not None:
        plot_kwargs["x"] = params["x"]
    if params.get("y") is not None:
        plot_kwargs["y"] = params["y"]
    dataframe.plot(**plot_kwargs)
    if params.get("title"):
        ax.set_title(params["title"])
    try:
        fig.autofmt_xdate()
    except Exception:
        pass
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", dpi=dpi)
    plt.close(fig)
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return {"b64": b64, "css_w": int(figsize[0] * 96), "css_h": int(figsize[1] * 96)}


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
            details = "; ".join(f"#{i + 1} {c.get('command', '?')}: {c.get('parsed_comment', '?')}" for i, c in parse_errors)
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
    """Человекочитаемая подпись шага для панели прогресса выполнения."""
    kind = command.get("command", "?")
    if kind == "VALIDATE":
        return "Валидация скрипта"
    if kind == "GET":
        prefix = "APPLY " if "apply" in command else ""
        return f"{prefix}GET {command.get('source', '?')}:{command.get('function', '?')} → {command.get('data_name', '?')}"
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


THEMES = {
    'dark': {
        'bg': '#1F2937',
        'text': '#FFFFFF',
        'accent': '#06B6D4',
        'card': '#2D3748',
        'glow': '0 0 15px rgba(6, 182, 212, 0.3)',
        'title': '#22D3EE',
        'panel': '#2D3748'
    },
    'light': {
        'bg': '#F3F4F6',
        'text': '#1F2937',
        'accent': '#3B82F6',
        'card': '#FFFFFF',
        'glow': '0 0 15px rgba(59, 130, 246, 0.2)',
        'title': '#2563EB',
        'panel': '#F9FAFB'
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
        }}
        html, body {{
            margin: 0;
            padding: 0;
            background: var(--bg-color);
            overflow: hidden;
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
    ui.run_javascript(
        "const r = document.documentElement.style;"
        f"r.setProperty('--app-font', `{font}`);"
        f"r.setProperty('--app-font-size', '{font_size}px');"
        f"r.setProperty('--app-table-font', `{table_font}`);"
        f"r.setProperty('--app-table-font-size', '{table_font_size}px');"
    )


def make_codemirror(current_state, **kwargs):
    """Создать редактор CodeMirror с темой из настроек и зарегистрировать его для живой смены темы."""
    theme = current_state.get("codemirror_theme") or APPEARANCE_DEFAULTS["codemirror_theme"]
    try:
        editor = ui.codemirror(theme=theme, **kwargs)
    except BaseException:
        editor = ui.codemirror(**kwargs)
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
    update_theme(theme)

    async def toggle_theme():
        nonlocal theme
        theme = 'light' if theme == 'dark' else 'dark'
        app.storage.user.update({'theme': theme})
        update_theme(theme)
        ui.notify(f'Switched to {theme} theme', type='info')

    with ui.element('div').classes('main-container') as main_container:
        with ui.card().classes('login-form') as login_card:
            title_label = ui.label('NEON GENESIS UNIVERSAL HARVESTER').classes('title text-center text-2xl mb-4')
            username_input = ui.input(label='USERNAME', placeholder='Enter username').classes('w-full mb-2')
            username_input.tooltip("Enter your login here")
            password_input = ui.input(label='PASSWORD', password=True, placeholder='Enter password').classes('w-full mb-4')
            
            async def handle_login():
                if not username_input.value or not password_input.value:
                    ui.notify("Please fill in both username and password", type='negative')
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
                    ui.notify("Login successful!", type='positive')
                    ui.navigate.to('/')
                else:
                    ui.notify("Login failed", type='negative')

            login_button = ui.button('LOGIN', on_click=handle_login).classes('w-full hover-glow mb-2').style(f'background: {THEMES[theme]["accent"]}')
            if current_state.get("keycloak_flag", False):
                try:
                    auth_url = current_state["keycloak_openid"].auth_url(redirect_uri=f"{current_state['itself_link']}login/callback")
                    ui.button('LOGIN VIA KEYCLOAK', on_click=lambda: ui.navigate.to(auth_url)).classes('w-full hover-glow').style(f'background: {THEMES[theme]["accent"]}')
                except Exception as e:
                    ui.label(f"Keycloak error: {str(e)}").classes('text-red-500 text-sm')

        with ui.element('div').classes('sidebar border rounded-lg p-4') as sidebar:
            user_status_label = ui.label('USER: NOT AUTHORIZED').classes('text-sm mb-2 pulse')
            ip_label = ui.label(f"IP: {current_state.get('client_ip_address', 'N/A')}").classes('text-sm mb-2')
            port_label = ui.label(f"PORT: {current_state.get('client_port', 'N/A')}").classes('text-sm mb-2')
            app_session_label = ui.label(f"APP SESSION: {current_state.get('main_session_id', 'N/A')}").classes('text-sm mb-2')
            user_session_label = ui.label('USER SESSION: NONE').classes('text-sm mb-2')

        with ui.element('div').classes('theme-toggle'):
            ui.switch('Light Theme', value=theme == 'light', on_change=toggle_theme).classes('hover-glow')

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

    with ui.header(elevated=True) as top_panel:
        with ui.row().classes('items-center'):
            menu_items = [
                (tr("nav.settings"), tr("nav.settings.tip"), 'pets', "Settings"),
                (tr("nav.secrets"), tr("nav.secrets.tip"), 'key', "Secrets"),
                (tr("nav.objects"), tr("nav.objects.tip"), 'source', "Objects"),
                (tr("nav.ai"), tr("nav.ai.tip"), 'psychology', "AI"),
                (tr("nav.harvester"), tr("nav.harvester.tip"), 'rocket_launch', "Harvester"),
                (tr("nav.history"), tr("nav.history.tip"), 'history', "History"),
                (tr("nav.logout"), tr("nav.logout.tip"), 'logout', "__logout__"),
            ]
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
    panel_harvester = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panel_history = ui.card().classes('w-full h-full uh-panel uh-panel-offscreen')
    panels = {
        "Settings": panel_settings, "Secrets": panel_secrets, "Objects": panel_objects,
        "AI": panel_ai, "Harvester": panel_harvester, "History": panel_history,
    }

    def show_panel(name):
        for panel_name, panel in panels.items():
            if panel_name == name:
                panel.classes(remove='uh-panel-offscreen')
            else:
                panel.classes(add='uh-panel-offscreen')

    draw_settings(panel_settings, current_state)
    draw_secrets(panel_secrets, current_state)
    draw_objects(panel_objects, current_state)
    draw_ai(panel_ai, current_state)
    draw_harvester(panel_harvester, current_state)
    draw_history(panel_history, current_state)
    show_panel("Harvester")

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
                    ui.label(tr("settings.language.title")).style(
                        "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); font-size: 1.25rem; color: var(--title-color);")
                    with ui.row().classes('items-end gap-2'):
                        language_select = ui.select(SUPPORTED_LANGUAGES, value=lang, label=tr("settings.language.label")).classes('w-64')

                        def save_language():
                            set_setting(scope, "language", language_select.value or DEFAULT_LANGUAGE, current_state)
                            ui.notify(tr("settings.language.saved"), type="positive")
                            ui.run_javascript("window.location.reload()")  # перезагрузка применяет язык ко всему интерфейсу

                        ui.button(tr("settings.language.apply"), icon='translate', on_click=save_language).classes('hover-glow')
                    ui.markdown(tr("settings.language.hint")).classes('text-xs opacity-60')

                    ui.separator()
                    ui.label(tr("settings.section.appearance")).style(
                        "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); font-size: 1.25rem; color: var(--title-color);")
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
                    ui.separator()
                    ui.label(tr("settings.section.account")).style(
                        "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); font-size: 1.25rem; color: var(--title-color);")
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
                        ui.separator()
                        ui.label(tr("settings.section.users")).style(
                            "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); font-size: 1.25rem; color: var(--title-color);")
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
                        ui.separator()
                        ui.label(tr("settings.section.ai")).style(
                            "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); font-size: 1.25rem; color: var(--title-color);")

                        ui.label(tr("settings.ai.limits")).style("color: var(--accent-color);")
                        max_iter_value = get_setting("global", "agent_max_iterations", 10, current_state)[3] or 10
                        with ui.row().classes('items-end gap-2'):
                            agent_iter_input = ui.number(label=tr("settings.ai.maxiter"),
                                                         value=int(max_iter_value), min=1, max=100, step=1).classes('w-72')

                            def save_ai_limits():
                                try:
                                    val = int(agent_iter_input.value or 10)
                                except BaseException:
                                    val = 10
                                val = max(1, min(val, 100))
                                result = set_setting("global", "agent_max_iterations", val, current_state)
                                if not result[0]:
                                    ui.notify(tr("settings.common.error", error=result[1]), type="negative")
                                    return
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
                        ui.separator()
                        ui.label(tr("settings.section.networks")).style(
                            "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); font-size: 1.25rem; color: var(--title-color);")
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
                        ui.separator()
                        ui.label(tr("settings.section.apikeys")).style(
                            "font-family: var(--app-font, 'Orbitron', 'Roboto', sans-serif); font-size: 1.25rem; color: var(--title-color);")
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
                    ui.markdown(f"**{arg}** = `{json.dumps(value, ensure_ascii=False, default=str)}`")
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
                ui.markdown(f"*SHOW: {reason}*")
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
                        ui.markdown(f"*SHOW: {command['_info']}*")
                        return False
                try:
                    plot = render_plot_png_b64(data, params)
                    ui.image(f"data:image/png;base64,{plot['b64']}").style(
                        f"width: {plot['css_w']}px; max-width: 100%; height: auto")
                    return True
                except BaseException as plot_error:
                    command["_info"] = f"matplotlib error: {str(plot_error)}"
                    ui.markdown(f"*SHOW {command['_info']}*")
                    return False
            else:
                command["_info"] = tr("harv.show.bad_type", type=show_type)
                ui.markdown(f"*SHOW: {command['_info']}*")
                return False

        def _render_save(command, variables, result_map):
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
                ui.markdown(f"*SAVE: {command['_info']}*")
                return False
            if not tables_data:
                command["_info"] = tr("harv.save.no_tables")
                ui.markdown(f"*SAVE: {command['_info']}*")
                return False

            # базовое имя файла: AS filename -> имя таблицы (если одна) -> 'export'
            base_name = save_filename or (tables[0] if len(tables) == 1 else "export")

            try:
                content, filename, media_type = records_to_download(tables_data, fmt, base_name)
            except BaseException as save_error:
                command["_info"] = str(save_error)
                ui.markdown(f"*SAVE error: {command['_info']}*")
                return False

            try:
                ui.download(content, filename, media_type)
            except TypeError:
                ui.download(content, filename)
            total = sum(len(d) for d in tables_data.values())
            ui.markdown(tr("harv.save.downloading", filename=filename, tables=len(tables_data), rows=total))
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
                    suffix = f" — {info}" if (state in ("done", "error", "warning") and info) else ""
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
                        f"#{i + 1} {c.get('command', '?')}: {c.get('parsed_comment', '?')}" for i, c in parse_errors)
                    card_results.clear()
                    with card_results:
                        ui.markdown(tr("harv.parse_errors"))
                        for i, c in parse_errors:
                            ui.markdown(tr("harv.parse_error_item", n=i + 1, cmd=c.get('command', '?'), comment=c.get('parsed_comment', '?')))
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
                    # все ещё не выполненные шаги отклонены: после ошибки они не запустятся
                    for command in parsed_command:
                        if command.get("_status") == "pending":
                            command["_status"] = "rejected"
                    card_results.clear()
                    with card_results:
                        ui.markdown(tr("harv.exec_error", error=commands_executor_result[1]))
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
                # оставшиеся шаги отклонены
                try:
                    for command in parsed_command:
                        if command.get("_status") == "pending":
                            command["_status"] = "rejected"
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

        with interface_container:
            # весь блок harvester — в вертикально-прокручиваемом контейнере.
            # Глобально у body задан overflow:hidden, поэтому скролл задаём здесь,
            # ограничивая высоту вьюпортом (за вычетом шапки приложения).
            with ui.column().classes('w-full no-wrap').style('height: calc(100vh - 130px); overflow-y: auto; overflow-x: hidden'):
                with ui.tabs().classes('w-full') as tabs:
                    tab_script = ui.tab('Scripts', label=tr("harv.tab.scripts"))
                    tab_datavars = ui.tab('Data/Variables', label=tr("harv.tab.datavars"))
                with ui.tab_panels(tabs, value=tab_script).classes('w-full') as harvester_panels:
                    with ui.tab_panel(tab_script):
                        # сворачиваемый блок скрипта (вместе с кнопкой Execute) — освобождает место под результаты
                        with ui.expansion(tr("harv.script"), icon='code', value=True).classes('w-full'):
                            codemirror_script = make_codemirror(current_state).classes('w-full').style('max-height: 30vh')
                            button_script = ui.button(tr("harv.execute")).on_click(button_script_click)
                        # сворачиваемый блок прогресса шагов (вариант A): список команд со статусами
                        with ui.expansion(tr("harv.steps"), icon='list', value=True).classes('w-full'):
                            steps_panel = ui.element('div').classes('w-full').style('padding: 4px 8px')
                        # вертикальный скролл — у внешнего контейнера; здесь только
                        # горизонтальный для широких таблиц (чтобы не вылезали за страницу)
                        card_results = ui.element('div').classes('w-full').style('overflow-x: auto; padding: 8px; border: 1px solid var(--panel-bg)')

                    with ui.tab_panel(tab_datavars):
                        grid_datavars = ui.aggrid({}).classes('w-full').style('height: 60vh')
                        codemirror_datavar = make_codemirror(current_state).classes('w-full')

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    
AGENT_ACTIONS = ("run", "list_sources", "get_source_functions", "list_objects", "search_objects", "get_object")


def _extract_action(text):
    """Найти первый блок-действие агента ```<action> ...```; вернуть (action, argument) или (None, None).
    Блок ```harvester``` действием НЕ является (финальный ответ)."""
    import re
    match = re.search(r"```(" + "|".join(AGENT_ACTIONS) + r")\b[ \t]*\n?(.*?)```", text or "", flags=re.DOTALL)
    if not match:
        return None, None
    return match.group(1), match.group(2).strip()


def run_agent_script(script_text, current_state):
    """Выполнить сгенерированный агентом скрипт и вернуть компактную сводку результата (для LLM).
    Запуск идёт от имени текущего пользователя (по его ролям); пишется в историю с пометкой agent."""
    try:
        agent_run_start = time.monotonic()
        parsed = command_parser(script_text, current_state)
        parse_errors = [c for c in parsed if not c.get("parsed", True)]
        if parse_errors:
            return "ОШИБКА ПАРСИНГА: " + "; ".join(
                f"{c.get('command', '?')}: {c.get('parsed_comment', '?')}" for c in parse_errors)

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
                conversation = []  # список реплик {role, content} текущей сессии (panel persistent)

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
                            ui.markdown(f"{who}\n\n{content}", extras=['tables', 'fenced-code-blocks'])

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
                        return f"неизвестное действие: {action}"
                    except BaseException as e:
                        return f"ошибка действия {action}: {str(e)}"

                async def send_message():
                    selected = current_state.get("ui_selected_llm")
                    if not selected or not selected.get("json"):
                        ui.notify(tr("ai.select_first"), type="negative")
                        return
                    user_text = (ai_chat_input.value or "").strip()
                    if not user_text:
                        return
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
                        # лимит цикла «ответ -> действие -> ответ» — глобальная настройка (Settings → AI)
                        try:
                            max_iterations = int(get_setting("global", "agent_max_iterations", 10, current_state)[3] or 10)
                        except BaseException:
                            max_iterations = 10
                        max_iterations = max(1, min(max_iterations, 100))
                        for iteration in range(max_iterations):
                            system_prompt = build_agent_system_prompt()
                            messages = llm_build_messages(system_prompt, conversation, context_window)
                            ok, reply = await run.io_bound(llm_chat, selected["json"], messages, current_state)
                            if not ok:
                                conversation.append({"role": "assistant", "content": tr("ai.llm_error", error=reply)})
                                render_chat()
                                break
                            conversation.append({"role": "assistant", "content": reply})
                            render_chat()

                            action, argument = _extract_action(reply)
                            if not action:
                                break  # финальный ответ (текст или ```harvester) — действий нет

                            if status is not None:
                                status.set_text(tr("ai.agent_action", action=action))
                            action_result = await run.io_bound(dispatch_action, action, argument)
                            action_result = llm_truncate_to_tokens(action_result, max(512, context_window // 4))
                            conversation.append({"role": "user", "content": f"РЕЗУЛЬТАТ ДЕЙСТВИЯ [{action}]:\n{action_result}"})
                            render_chat()
                            if status is not None:
                                status.set_text(tr("ai.thinking"))
                        else:
                            # лимит действий исчерпан — просим финальный вариант без действий
                            conversation.append({"role": "user", "content":
                                "Достигнут лимит действий. Приведи лучший финальный вариант скрипта в блоке "
                                "```harvester и краткий итог (что получилось, что осталось проверить вручную). Без действий."})
                            system_prompt = build_agent_system_prompt()
                            messages = llm_build_messages(system_prompt, conversation, context_window)
                            ok, reply = await run.io_bound(llm_chat, selected["json"], messages, current_state)
                            conversation.append({"role": "assistant", "content": reply if ok else tr("ai.llm_error", error=reply)})
                            render_chat()
                    except BaseException as e:
                        conversation.append({"role": "assistant", "content": tr("ai.error", error=str(e))})
                        render_chat()
                    finally:
                        if spinner is not None:
                            spinner.visible = False
                        if status is not None:
                            status.set_text("")

                chat_display = ui.element('div').classes('w-full').style('flex: 1 1 auto; min-height: 30vh; overflow-y: auto; padding: 4px 8px; border: 1px solid var(--panel-bg)')
                with ui.row().classes('w-full items-center'):
                    ai_chat_input = ui.input(tr("ai.input_placeholder")).classes('grow')
                    ai_chat_input.on('keydown.enter', lambda: send_message())
                    ui.button(tr("ai.send"), icon='send').on_click(send_message)
                    ui.button(tr("ai.clear"), icon='delete').on_click(lambda: (conversation.clear(), render_chat()))
                render_chat()

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