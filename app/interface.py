from app.login import try_login
from app.validation import check_current_user_status
from app.db import get_user_by_username, get_all_actual_objects, get_all_object_versions, get_object_by_name_and_version, create_new_object_version, create_new_object, db_get_secrets_list, update_secret_comment, update_secret_secret_comment, create_secret, delete_secret
import syslog
import asyncio
import json
from nicegui import ui, app, Client, run
from app.logging import get_log_message, logger_log, currentFuncName
from typing import Dict, Any, Tuple
from engine import commands_executor
from app.engine import command_parser
from app.validation import json_validate, validate_itemname, validate_comment
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
    column_defs = [{"headerName": str(c), "field": str(c), "filter": True, "sortable": True, "resizable": True}
                   for c in columns] or [{"headerName": "value", "field": "value", "filter": True, "sortable": True}]
    return {
        "columnDefs": column_defs,
        "rowData": row_data,
        "defaultColDef": {"filter": True, "sortable": True, "resizable": True},
        "pagination": True,
        "paginationPageSize": 20,
        "enableCellTextSelection": True,
        "domLayout": "normal",
    }


def render_plot_png_b64(data, params):
    """Построить график matplotlib по данным и optional_params, вернуть base64 PNG."""
    import io
    import base64
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas

    dataframe = pandas.DataFrame(data)
    figsize = params.get("figsize", [10, 5])
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
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode()


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

def update_theme(theme: str):
    css = f"""
        :root {{
            --bg-color: {THEMES[theme]['bg']};
            --text-color: {THEMES[theme]['text']};
            --accent-color: {THEMES[theme]['accent']};
            --card-bg: {THEMES[theme]['card']};
            --glow: {THEMES[theme]['glow']};
            --title-color: {THEMES[theme]['title']};
            --panel-bg: {THEMES[theme]['panel']};
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
            font-family: 'Orbitron', 'Roboto', sans-serif;
            letter-spacing: 1px;
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
                        "roles":login_data['roles']
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
            font-family: 'Orbitron', 'Roboto', sans-serif;
            letter-spacing: 1px;
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

    with ui.header(elevated=True) as top_panel:
        with ui.row():
            menu_items = [
                ("Settings", "Настройки", 'pets' , None),#lambda: draw_users(interface_container, current_state)),
                ("Secrets", "Хранилище секретов", 'key', lambda: draw_secrets(interface_container, current_state)),
                ("Objects", "Сохранённые объекты", 'source', lambda: draw_objects(interface_container, current_state)),
                ("AI", "LLM чат получения и обработки данных", 'psychology', lambda: draw_ai(interface_container, current_state)),
                ("Harvester", "Исполнение скриптов", 'rocket_launch', lambda: draw_harvester(interface_container, current_state)),
                ("Logout", "Выход", 'logout', logout)
            ]
            for item, tooltip, icon, function in menu_items:
                menu_item = ui.button(item, icon=icon).tooltip(tooltip)
                menu_item.on('click', function)
            ui.switch('Light Theme', value=theme == 'light', on_change=toggle_theme).classes('hover-glow')
            #ui.label(f"USER SESSION: {current_state['user_session_id']}").classes('panel-item')
            


    interface_container = ui.card()
    with interface_container.classes('w-full h-full'):
        pass

def draw_objects(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))

        interface_container.clear()

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
                ui.label('You do not have object_admin role')
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
                    {"headerName": "Name", "field": "name", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": "Type", "field": "type", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": "Version", "field": "version", "filter": True, "sortable": True, "minWidth": 60},
                    {"headerName": "Timestamp", "field": "timestamp", "filter": True, "sortable": True, "minWidth": 200},
                    {"headerName": "Owner", "field": "owner", "filter": True, "sortable": True, "minWidth": 120},
                    {"headerName": "Roles", "field": "roles", "filter": True, "sortable": True, "minWidth": 120},
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
                    {"headerName": "Name", "field": "name", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": "Type", "field": "type", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": "Version", "field": "version", "filter": True, "sortable": True, "minWidth": 60},
                    {"headerName": "Timestamp", "field": "timestamp", "filter": True, "sortable": True, "minWidth": 200},
                    {"headerName": "Owner", "field": "owner", "filter": True, "sortable": True, "minWidth": 120},
                    {"headerName": "Roles", "field": "roles", "filter": True, "sortable": True, "minWidth": 120},
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
                ui.notify("Roles is not a valid json list", type="negative")
                return
            # проверяем, что в codemirror валидный json
            if not json_validate(codemirror_edit_object_version.value):
                ui.notify("JSON is not a valid json", type="negative")
                return
            # проверяем, что в codemirror именно dict
            if not isinstance(json.loads(codemirror_edit_object_version.value), dict):
                ui.notify("JSON is not a valid dict", type="negative")
                return
            # проверяем, есть ли изменения
            if selected_object_version["type"] == select_edit_object_type.value and selected_object_version["roles"] == json.loads(input_edit_object_roles.value) and json.dumps(selected_object_version["json"], indent=4, ensure_ascii=False) == codemirror_edit_object_version.value:
                # полное совпадение, изменений нет
                ui.notify("There are not changes", type="negative")
            else:
                create_new_object_version_result = create_new_object_version(selected_object_version["name"], select_edit_object_type.value, json.loads(input_edit_object_roles.value), json.loads(codemirror_edit_object_version.value), current_state)
                if not create_new_object_version_result[0]:
                    ui.notify(f"{create_new_object_version_result[1]}", type="negative")
                    return
                ui.notify(f"New version of {selected_row["name"]} saved", type="positive")
                update_grid_object_versions(selected_row["name"], grid_object_versions, current_state)
                object_panels.set_value('Object info')
            # если есть, то создаём новую версию объекта

        # кнопка создания объекта
        def create_button_object():
            #сначала проверяем, заполнено ли имя
            if input_new_object_name.value == "":
                ui.notify("Empty name", type="negative")
                return
            # проверка имени на корректность
            validate_itemname_result = validate_itemname(input_new_object_name.value, current_state)
            if not validate_itemname_result[0]:
                ui.notify(f"{validate_itemname_result[1]}", type="negative")
                return
            # выбор типа
            if select_new_object_type.value not in ["script", "source", "notifier", "llm"]:
                ui.notify("Wrong object type", type="negative")
                return
            # а заполнены ли роли?
            if input_new_object_roles.value == "":
                ui.notify("Empty roles", type="negative")
                return
            # проверяем, что в roles валидный список
            if not json_validate(input_new_object_roles.value):
                ui.notify("Roles is not a valid json list", type="negative")
                return
            # проверяем, что это точно список
            if not isinstance(json.loads(input_new_object_roles.value), list):
                ui.notify("Roles is not a valid json list", type="negative")
                return
            # проверяем, что есть хотя бы одна роль
            if not len(json.loads(input_new_object_roles.value)) > 0:
                ui.notify("Empty roles list", type="negative")
                return
            # проверяем, что в codemirror валидный json
            if not json_validate(codemirror_create_new_object.value):
                ui.notify("JSON is not a valid json", type="negative")
                return
            # проверяем, что в codemirror именно dict
            if not isinstance(json.loads(codemirror_create_new_object.value), dict):
                ui.notify("JSON is not a valid dict", type="negative")
                return
            
            # проверяем, есть ли объект с таким именем
            get_all_object_versions_result = get_all_object_versions(input_new_object_name.value, current_state)
            if get_all_object_versions_result[0] == True:
                ui.notify(f"Name is used for other object", type="negative")
                return
            else:
                if get_all_object_versions_result[1] != "object not found":
                    ui.notify(f"Error with testing new object name", type="negative")
                    return
                else:
                    # имя точно уникальное, записываем в базу новый объект
                    create_new_object_result = create_new_object(input_new_object_name.value, select_new_object_type.value, json.loads(input_new_object_roles.value), json.loads(codemirror_create_new_object.value), current_state)
                    if not create_new_object_result[0]:
                        ui.notify(f"Error with creating new object", type="negative")
                        return
                    ui.notify(f"Done", type="positive")
                    update_grid_objects_list(grid_objects_list, current_state)
            
        with interface_container:
            with ui.tabs().classes('w-full h-full') as tabs:
                tab_objects_list = ui.tab('Objects list')
                tab_one_object = ui.tab('Object info')
                tab_object_editor = ui.tab('Object editor')
                tab_object_creator = ui.tab('Object creator')
            with ui.tab_panels(tabs, value=tab_objects_list).classes('w-full h-full') as object_panels:
                with ui.tab_panel(tab_objects_list):
                    grid_objects_list = ui.aggrid({}).classes('h-[calc(85vh-100px)]')
                    grid_objects_list.on("selectionChanged", grid_objects_list_click)

                with ui.tab_panel(tab_one_object):
                    grid_object_versions = ui.aggrid({})
                    grid_object_versions.on("selectionChanged", grid_object_versions_click)
                    codemirror_show_object_version = ui.codemirror()
                    

                with ui.tab_panel(tab_object_editor):
                    with ui.row():
                        label_edit_object_name = ui.label("Name")
                        select_edit_object_type = ui.select(["script", "source", "notifier", "llm"], value="script")
                        input_edit_object_roles = ui.input(label='Roles')
                        button_save_new_object_version = ui.button("Save")
                        button_save_new_object_version.on_click(save_button_of_object_editor)
                        button_delete_object = ui.button("Delete")
                        #button_delete_object.on_click(save_button_of_object_editor)
                    codemirror_edit_object_version = ui.codemirror()
                    

                with ui.tab_panel(tab_object_creator):
                    with ui.row():
                        input_new_object_name = ui.input(label='Name')
                        select_new_object_type = ui.select(["script", "source", "notifier", "llm"], value="script")
                        input_new_object_roles = ui.input(label='Roles')
                        input_new_object_roles.value = '["default"]'
                        button_create_new_object = ui.button("Create")
                        button_create_new_object.on_click(create_button_object)
                    codemirror_create_new_object = ui.codemirror()

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
            arg = (command.get("print_arg") or "").strip()
            # литерал в кавычках -> текст-комментарий
            if len(arg) >= 2 and ((arg[0] == arg[-1] == '"') or (arg[0] == arg[-1] == "'")):
                ui.markdown(arg[1:-1], extras=['tables', 'fenced-code-blocks'])
                return
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
                    return
            if data is not None:
                ui.markdown(records_to_markdown(data), extras=['tables', 'fenced-code-blocks'])
                return
            # иначе — просто текст
            ui.markdown(arg, extras=['tables', 'fenced-code-blocks'])

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
                ui.markdown(f"*SHOW: нет табличных данных «{table}»*")
                return

            if show_type == "table":
                ui.aggrid(records_to_aggrid_options(data, current_state.get("aggrid_theme", "ag-theme-balham-dark"))).classes('w-full h-[60vh]')
            elif show_type in ("matplotlib", "plot"):
                params = {}
                if params_raw:
                    if json_validate(params_raw):
                        params = json.loads(params_raw)
                    else:
                        ui.markdown("*SHOW: optional_params не является валидным JSON*")
                        return
                try:
                    b64 = render_plot_png_b64(data, params)
                    ui.image(f"data:image/png;base64,{b64}")
                except BaseException as plot_error:
                    ui.markdown(f"*SHOW matplotlib error: {str(plot_error)}*")
            else:
                ui.markdown(f"*SHOW: неизвестный тип «{show_type}» (table | matplotlib)*")

        def _update_datavars(variables, result_map):
            rows = []
            for name, res in result_map.items():
                count = len(res[3]) if (res[0] and isinstance(res[3], list)) else 0
                rows.append({"name": name, "kind": "data", "rows": count})
            for name, value in variables.items():
                rows.append({"name": name, "kind": "variable",
                             "rows": (len(value) if isinstance(value, (list, dict)) else 1)})
            grid_datavars.options['columnDefs'] = [
                {"headerName": "Name", "field": "name", "filter": True, "sortable": True},
                {"headerName": "Kind", "field": "kind", "filter": True, "sortable": True},
                {"headerName": "Rows", "field": "rows", "filter": True, "sortable": True},
            ]
            grid_datavars.options['rowData'] = rows
            grid_datavars.options['domLayout'] = "normal"
            grid_datavars.update()
            codemirror_datavar.value = json.dumps(variables, ensure_ascii=False, indent=2, default=str)

        async def button_script_click():
            try:
                parsed_command = command_parser(codemirror_script.value, current_state)

                # вывод ошибок парсинга
                parse_errors = [(i, c) for i, c in enumerate(parsed_command) if not c.get("parsed", True)]
                if parse_errors:
                    card_results.clear()
                    with card_results:
                        ui.markdown("**Ошибки парсинга:**")
                        for i, c in parse_errors:
                            ui.markdown(f"- команда {i + 1} (`{c.get('command', '?')}`): {c.get('parsed_comment', '?')}")
                    return

                commands_executor_result = await run.io_bound(commands_executor, parsed_command, current_state)
                if not commands_executor_result[0]:
                    logger_log(syslog.LOG_ERR, get_log_message(commands_executor_result[1], currentFuncName(), current_state))
                    card_results.clear()
                    with card_results:
                        ui.markdown(f"**Ошибка выполнения:** {commands_executor_result[1]}")
                    ui.notify(commands_executor_result[1], type="negative")
                    return

                variables, result_map = commands_executor_result[3]

                # последовательный вывод PRINT/SHOW в порядке их следования в скрипте
                card_results.clear()
                with card_results:
                    rendered = 0
                    for command in parsed_command:
                        if command["command"] == "PRINT":
                            _render_print(command, variables, result_map)
                            rendered += 1
                        elif command["command"] == "SHOW":
                            _render_show(command, variables, result_map)
                            rendered += 1
                    if rendered == 0:
                        ui.markdown("_Выполнено. В скрипте нет команд PRINT/SHOW для вывода._")

                _update_datavars(variables, result_map)
                ui.notify("Done", type="positive")

            except BaseException as e:
                error_message = f"fail: {str(e)}"
                logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
                ui.notify(error_message, type="negative")

        with interface_container:
            # весь блок harvester — в вертикально-прокручиваемом контейнере.
            # Глобально у body задан overflow:hidden, поэтому скролл задаём здесь,
            # ограничивая высоту вьюпортом (за вычетом шапки приложения).
            with ui.column().classes('w-full no-wrap').style('height: calc(100vh - 130px); overflow-y: auto; overflow-x: hidden'):
                with ui.tabs().classes('w-full') as tabs:
                    tab_script = ui.tab('Scripts')
                    tab_datavars = ui.tab('Data/Variables')
                with ui.tab_panels(tabs, value=tab_script).classes('w-full') as harvester_panels:
                    with ui.tab_panel(tab_script):
                        codemirror_script = ui.codemirror().classes('w-full').style('max-height: 30vh')
                        button_script = ui.button("Execute").on_click(button_script_click)
                        # вертикальный скролл — у внешнего контейнера; здесь только
                        # горизонтальный для широких таблиц (чтобы не вылезали за страницу)
                        card_results = ui.element('div').classes('w-full').style('overflow-x: auto; padding: 8px; border: 1px solid var(--panel-bg)')

                    with ui.tab_panel(tab_datavars):
                        grid_datavars = ui.aggrid({}).classes('w-full').style('height: 60vh')
                        codemirror_datavar = ui.codemirror().classes('w-full')

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    
def draw_ai(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))

        interface_container.clear()

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
        
        # скелет интерфейса
        with interface_container:
            with ui.tabs().classes('w-full h-full') as tabs:
                tab_chat = ui.tab('Chat')
                #tab_history = ui.tab('History')
                tab_knowledgebase = ui.tab('Knowledge base')
            with ui.tab_panels(tabs, value=tab_chat).classes('w-full h-full') as ai_panels:
                with ui.tab_panel(tab_chat):
                    card_aichat = ui.card()
                    codemirror_aichat = ui.codemirror()
                    with ui.row():
                        select_ai_lmm = ui.select(["empty"], value="empty")
                        button_aichat_send = ui.button("Send")#.on_click(button_script_click)
                        button_aichat_attachment = ui.button("Attach")#.on_click(button_script_click)
                        button_aichat_clear = ui.button("Clear")#.on_click(button_script_click)

                with ui.tab_panel(tab_knowledgebase):
                    grid_datavars = ui.aggrid({})
                    ui.mermaid('''
                        graph LR;
                            A --> B;
                            A --> C;
                    ''')


    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    
def draw_secrets(interface_container: ui.card, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        logger_log(syslog.LOG_INFO, get_log_message("Starting", currentFuncName(), current_state))

        interface_container.clear()

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
                ui.label('You do not have secrets_admin role')
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
                    {"headerName": "System", "field": "system", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": "Account", "field": "account", "filter": True, "sortable": True, "minWidth": 150},
                    {"headerName": "Secret", "field": "secret", "filter": True, "sortable": True, "minWidth": 60},
                    {"headerName": "Comment", "field": "comment", "filter": True, "sortable": True, "minWidth": 200},
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
                ui.notify("System is not valid", type="negative")
                return
            if not validate_itemname(account, current_state)[0]:
                ui.notify("Account is not valid", type="negative")
                return
            if not validate_comment(comment, current_state)[0]:
                ui.notify("Comment is not valid", type="negative")
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
                    success_message = f"Comment updated for {system}:{account}"
                else:
                    result = update_secret_secret_comment(system, account, comment, secret, current_state)
                    success_message = f"Secret and comment updated for {system}:{account}"
            else:
                # создание нового секрета
                if secret == "" or secret == SECRET_MASK:
                    ui.notify("Empty secret", type="negative")
                    return
                result = create_secret(system, account, comment, secret, current_state)
                success_message = f"Secret {system}:{account} created"

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
                ui.notify("System is not valid", type="negative")
                return
            if not validate_itemname(account, current_state)[0]:
                ui.notify("Account is not valid", type="negative")
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
                ui.notify(f"Secret {system}:{account} not found", type="negative")
                return

            delete_secret_result = delete_secret(system, account, current_state)
            if not delete_secret_result[0]:
                ui.notify(delete_secret_result[1], type="negative")
                return

            ui.notify(f"Secret {system}:{account} deleted", type="positive")
            update_grid_secrets_list(grid_secrets_list, current_state)
            secrets_panels.set_value('Secrets')

        # скелет интерфейса
        with interface_container:
            with ui.tabs().classes('w-full h-full') as tabs:
                tab_secrets = ui.tab('Secrets')
                tab_edit_secrets = ui.tab('Edit/create')
            with ui.tab_panels(tabs, value=tab_secrets).classes('w-full h-full') as secrets_panels:
                with ui.tab_panel(tab_secrets):
                    grid_secrets_list = ui.aggrid({}).classes('h-[calc(85vh-100px)]')
                    grid_secrets_list.on("selectionChanged", grid_secrets_list_click)

                with ui.tab_panel(tab_edit_secrets):
                    input_edit_secret_system  = ui.input(label='System')
                    input_edit_secret_account = ui.input(label='Account')
                    input_edit_secret_secret  = ui.input(label='Secret', password=True)
                    input_edit_secret_comment = ui.input(label='Comment')
                    with ui.row():
                        button_secret_new    = ui.button("New").on_click(new_button_of_secret_editor)
                        button_secret_save   = ui.button("Save").on_click(save_button_of_secret_editor)
                        button_secret_delete = ui.button("Delete").on_click(delete_button_of_secret_editor)

        update_grid_secrets_list(grid_secrets_list, current_state)

    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None