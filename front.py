import argparse

from fastapi import Request, Response, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from nicegui import app, ui, Client, run

import re

import uuid
import sys

from app.logging import currentTimestamp, get_log_message, logger_log#, currentFuncName
from app.interface import login_page
from app.interface import main_page
from app.interface import execute_script_api

from app.db import db_init
from app.db import verify_api_key, get_user_by_username

from app.crptgrphy import decrypt

from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
import syslog

from app.db import get_secret

def main():
    APP_NAME = "Universal Harvester 2 Scripted"
    APP_VERSION = "0.1.0"

    DUMMY_SESSION_ID = "00000000-0000-0000-0000-000000000000"
    DUMMY_IP = "127.0.0.1"
    DUMMY_PORT = 0
    DUMMY_USERNAME = "dummy"
    

    main_session_id = str(uuid.uuid4())
    ########################################
    # Ввод всех необходимых данных
    ########################################

    #MASTER_KEY = pwinput.pwinput(prompt='The master key: ', mask='*')
    MASTER_KEY = "***MASTER_KEY_REMOVED***"

    global args
    parser = argparse.ArgumentParser(description="Front UH")
    parser.add_argument(
        "--db_conf_object",
        type=str,
        default = '***FERNET_TOKEN_REMOVED***',
        help="Объект конфигурации БД (генерируется и шифруется вспомогательным модулем)"
    )
    parser.add_argument(
        "--nicegui_storage_key_object",
        type=str,
        default='***FERNET_TOKEN_REMOVED***',
        help="Ключ хранилища nicegui (sessions-key) (генерируется и шифруется вспомогательным модулем)"
    )
    parser.add_argument(
        "--ssl_certfile",
        type=str,
        default="crt.pem",
        help="SSL cert file path"
    )
    parser.add_argument(
        "--ssl_keyfile",
        type=str,
        default="key.pem",
        help="SSL key file path"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8082,
        help="Порт сервиса"
    )
    parser.add_argument(
        "--processes",
        type=int,
        default=4,
        help="Количество параллельно выполняемых потоков"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Хост поднимаемого сервиса (откуда ждём подключений)"
    )
    parser.add_argument(
        "--itself_link",
        type=str,
        default="https://localhost:8082",
        help="Раcположение ресурса"
    )
    parser.add_argument(
        "--keycloak_url",
        type=str,
        default="",
        help="(Опционально) keycloak URL"
    )
    parser.add_argument(
        "--keycloak_client_id",
        type=str,
        default="harvester",
        help="(Опционально) keycloak client id"
    )
    parser.add_argument(
        "--keycloak_realm_id",
        type=str,
        default="harvester",
        help="(Опционально) keycloak realm"
    )
    parser.add_argument(
        "--keycloak_key",
        type=str,
        default="keycloak:harvester",
        help="(Опционально) Ключ keycloak в таблице keys"
    )

    args = parser.parse_args()

    NICEGUI_STORAGE_KEY = args.nicegui_storage_key_object
    ITSELF_LINK = args.itself_link

    KEYCLOAK_URL = args.keycloak_url
    KEYCLOAK_CLIENT_ID = args.keycloak_client_id
    KEYCLOAK_REALM_ID = args.keycloak_realm_id
    KEYCLOAK_DB_KEY = args.keycloak_key

    ########################################
    # Подготовка первичного current_state
    ########################################

    current_state = {
        
        "app_name":APP_NAME,
        "app_version":APP_VERSION,
        "processes":args.processes,

        "main_session_id":main_session_id,
        "user_session_id":DUMMY_SESSION_ID,

        "client_ip_address":DUMMY_IP,
        "client_port":DUMMY_PORT,
        "username":"system",
        "roles":[],

        "master_key": MASTER_KEY,
        "db_conf":args.db_conf_object,

        "itself_link":ITSELF_LINK,

        "codemirror_theme":'monokai',
        "aggrid_theme":'ag-theme-balham-dark'
    }
    ########################################
    # Валидация и раскрытие введённых параметров
    ########################################
    decrypt_result = decrypt(NICEGUI_STORAGE_KEY, current_state)
    if decrypt_result[0] == False:
        error_message = f"NICEGUI_STORAGE_KEY decrypt failed: {decrypt_result[1]}"
        logger_log(syslog.LOG_CRIT, get_log_message(error_message, currentFuncName(), current_state))
        print(error_message)
        return
    NICEGUI_STORAGE_KEY = decrypt_result[3]
    ########################################
    # инициализация БД
    ########################################
    db_status = db_init(current_state)
    if db_status[0] == False:
        error_message = f"db init error: {db_status[1]}"
        logger_log(syslog.LOG_CRIT, get_log_message(error_message, currentFuncName(), current_state))
        print(error_message)
        return
    ########################################
    # Создание объекта интеграции с keycloak
    ########################################
    try:
        from keycloak.keycloak_openid import KeycloakOpenID
        db_get_key_result = get_secret(KEYCLOAK_DB_KEY.split(":")[0],KEYCLOAK_DB_KEY.split(":")[1], current_state)
        if db_get_key_result[0] == True:
            keycloak_flag = True
            keycloak_openid = KeycloakOpenID(
                server_url=KEYCLOAK_URL,
                client_id=KEYCLOAK_CLIENT_ID,
                client_secret_key=db_get_key_result[3],
                realm_name=KEYCLOAK_REALM_ID
            )
        else:
            keycloak_flag = False
            keycloak_openid = False
            error_message = f"keycloak init error: get keycloak key from db error ({db_get_key_result[1]})"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
    except BaseException as e:
        keycloak_flag = False
        keycloak_openid = False
        error_message = f"keycloak init error: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))

    ########################################
    # Красивости nicegui
    ########################################

    # ui.colors(
    #     primary="#F97316",  # Оранжевый для основных элементов
    #     secondary="#1F2937",  # Тёмно-серый для второстепенных элементов
    #     accent="#F97316"    # Оранжевый для акцентов
    # )
    # # Включаем тёмную тему
    # dark_mode = ui.dark_mode()
    # dark_mode.enable()

    ########################################
    # аутентификация
    ########################################
    unrestricted_page_routes_regex = ['/login', '/api']
    class AuthMiddleware(BaseHTTPMiddleware):
        """This middleware restricts access to all NiceGUI pages.

        It redirects the user to the login page if they are not authenticated.
        """
        async def dispatch(self, request: Request, call_next):
            if not app.storage.user.get('authenticated', False):
                if not request.url.path.startswith('/_nicegui'):
                    unrestricted_flag = False
                    for regex in unrestricted_page_routes_regex:
                        if re.search(regex, request.url.path):
                            unrestricted_flag = True
                    if unrestricted_flag == False:
                        app.storage.user['referrer_path'] = request.url.path  # remember where the user wanted to go
                        return RedirectResponse('/login')
            return await call_next(request)


    app.add_middleware(AuthMiddleware)

    ########################################
    # страница входа
    ########################################
    @ui.page('/login')
    async def _login_page(client: Client, request: Request):
        client_ip = request.client.host#client.environ['asgi.scope']['client'][0]
        client_port = request.client.port#client.environ['asgi.scope']['client'][1]
        current_state = {
            "db_conf":args.db_conf_object,
            "app_name":APP_NAME,
            "app_version":APP_VERSION,
            "main_session_id":main_session_id,
            "user_session_id":DUMMY_SESSION_ID,
            "client_ip_address":client_ip,
            "client_port":client_port,
            "username":DUMMY_USERNAME,
            "master_key": MASTER_KEY,
            "itself_link":ITSELF_LINK,
            "keycloak_flag":keycloak_flag,
            "keycloak_openid":keycloak_openid
        }
        #ui.page_title(f'{current_state["app_name"]}')
        await login_page(current_state)
    ########################################
    # callback keycloak
    ########################################
    @ui.page('/login/callback')
    async def _login_callback(client: Client, request: Request, session_state: str, code: str):
        client_ip = request.client.host#client.environ['asgi.scope']['client'][0]
        client_port = request.client.port#client.environ['asgi.scope']['client'][1]
        current_state = {
            "db_conf":args.db_conf_object,
            "app_name":APP_NAME,
            "app_version":APP_VERSION,
            "main_session_id":main_session_id,
            "user_session_id":DUMMY_SESSION_ID,
            "client_ip_address":client_ip,
            "client_port":client_port,
            "username":DUMMY_USERNAME,
            "master_key": MASTER_KEY,
            "itself_link":ITSELF_LINK,
            "keycloak_flag":keycloak_flag,
            "keycloak_openid":keycloak_openid
        }
        #ui.page_title(f'{current_state["app_name"]}')
        
        try:
            if current_state["keycloak_flag"] == True:
                access_token = keycloak_openid.token(
                    grant_type='authorization_code',
                    code=code,
                    redirect_uri=f"{current_state['itself_link']}login/callback"
                )

                new_session_id = str(uuid.uuid4())
                current_user_info = keycloak_openid.userinfo(access_token['access_token'])
                app.storage.user.update({
                    'username': current_user_info["preferred_username"], 
                    'authenticated': True, 
                    'session_id': new_session_id,
                    "access_token":access_token['access_token'],
                    "refresh_token":access_token['refresh_token'],
                    # "expires_in":access_token['expires_in'],
                    # "refresh_expires_in":access_token['refresh_expires_in']
                })
                ui.navigate.to('/')
        except BaseException as e:
            error_message = f"Keycloak access_token error: {str(e)}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))


    ########################################
    # основная страница приложения
    ########################################
    @ui.page('/')
    def _main_page(client: Client, request: Request):
        client_ip = request.client.host#client.environ['asgi.scope']['client'][0]
        client_port = request.client.port#client.environ['asgi.scope']['client'][1]
        CURRENT_SESSION_ID = app.storage.user['session_id']
        CURRENT_USERNAME = app.storage.user["username"]
        CURRENT_ROLES = app.storage.user["roles"]
        current_state = {
            "db_conf":args.db_conf_object,
            "processes":args.processes,
            "app_name":APP_NAME,
            "app_version":APP_VERSION,
            "main_session_id":main_session_id,
            "user_session_id":CURRENT_SESSION_ID,
            "client_ip_address":client_ip,
            "client_port":client_port,
            "username":CURRENT_USERNAME,
            "roles":CURRENT_ROLES,
            "master_key": MASTER_KEY,
            "codemirror_theme":'monokai',
            "aggrid_theme":'ag-theme-balham-dark',
            "itself_link":ITSELF_LINK,
            "keycloak_flag":keycloak_flag,
        }
        #ui.page_title(f'{current_state["app_name"]}')
        #main_page(keycloak_openid, current_state)
        main_page(None, current_state)

    ########################################
    # страница api запуска сценария curl
    ########################################
    # @app.get("/api/scenario/{scenario_name}/parameters/{parameters}/{output_type}", response_class=StreamingResponse, response_model=None)
    # async def download_report(scenario_name: str, parameters: str, output_type: str, request: Request):
    #     client_ip = request.client.host#client.environ['asgi.scope']['client'][0]
    #     client_port = request.client.port#client.environ['asgi.scope']['client'][1]
    #     CURRENT_SESSION_ID = str(uuid.uuid4())
    #     CURRENT_USERNAME = "api_user"
    #     current_state = {
    #         "db_conf":args.db_conf_object,
    #         "app_name":APP_NAME,
    #         "app_version":APP_VERSION,
    #         "main_session_id":main_session_id,
    #         "user_session_id":CURRENT_SESSION_ID,
    #         "client_ip_address":client_ip,
    #         "client_port":client_port,
    #         "username":CURRENT_USERNAME, 
    #         "master_key": MASTER_KEY,
    #         "codemirror_theme":'monokai',
    #         "aggrid_theme":'ag-theme-balham-dark',
    #         "itself_link":ITSELF_LINK,
    #         # "keycloak_flag":keycloak_flag,
    #         # "keycloak_openid":keycloak_openid
    #     }
    #     api_scenario_launch_page_result = await run.cpu_bound(api_scenario_launch_page, dict(request.headers), scenario_name, parameters, output_type, current_state)
    #     if api_scenario_launch_page_result[0] == False:
    #         raise HTTPException(status_code=api_scenario_launch_page_result[3]["response_code"], detail=api_scenario_launch_page_result[1])
        
    #     return StreamingResponse(
    #         api_scenario_launch_page_result[3]["buffer"],
    #         media_type=api_scenario_launch_page_result[3]["media_type"],
    #         headers={"Content-Disposition": f"attachment; filename={api_scenario_launch_page_result[3]['filename']}"})
    
    ########################################
    # API: выполнение DSL-скрипта
    ########################################
    @app.post("/api/script")
    async def api_script(request: Request):
        client_ip = request.client.host
        client_port = request.client.port

        # аутентификация по API-ключу (X-API-Key или Authorization: Bearer <token>)
        api_token = request.headers.get("x-api-key")
        if not api_token:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                api_token = auth_header[7:].strip()
        if not api_token:
            raise HTTPException(status_code=401, detail="missing API key (header X-API-Key)")

        auth_state = {
            "db_conf": args.db_conf_object, "app_name": APP_NAME, "app_version": APP_VERSION,
            "main_session_id": main_session_id, "user_session_id": str(uuid.uuid4()),
            "client_ip_address": client_ip, "client_port": client_port,
            "username": "api", "master_key": MASTER_KEY, "itself_link": ITSELF_LINK,
        }
        verify_result = verify_api_key(api_token, auth_state)
        if not verify_result[0]:
            raise HTTPException(status_code=401, detail=verify_result[1])
        owner = verify_result[3]

        owner_user = get_user_by_username(owner, auth_state)
        owner_roles = owner_user[3].get("roles", []) if owner_user[0] else []

        # тело запроса: текст скрипта; либо JSON {"script": "..."}
        raw_body = (await request.body()).decode("utf-8", errors="replace")
        script_text = raw_body
        try:
            import json as _json
            parsed_body = _json.loads(raw_body)
            if isinstance(parsed_body, dict) and "script" in parsed_body:
                script_text = parsed_body["script"]
        except BaseException:
            pass
        if not script_text or not script_text.strip():
            raise HTTPException(status_code=400, detail="empty script body")

        current_state = {
            "db_conf": args.db_conf_object, "processes": args.processes,
            "app_name": APP_NAME, "app_version": APP_VERSION,
            "main_session_id": main_session_id, "user_session_id": str(uuid.uuid4()),
            "client_ip_address": client_ip, "client_port": client_port,
            "username": owner, "roles": owner_roles, "master_key": MASTER_KEY,
            "codemirror_theme": "monokai", "aggrid_theme": "ag-theme-balham-dark",
            "itself_link": ITSELF_LINK, "keycloak_flag": keycloak_flag,
        }

        exec_result = await run.io_bound(execute_script_api, script_text, current_state)
        if not exec_result[0]:
            raise HTTPException(status_code=400, detail=exec_result[1])
        payload = exec_result[3]
        text = payload.get("text", "")
        files = payload.get("files", [])

        # нет бинарных артефактов -> простой текстовый ответ (с финальным переводом строки для CLI)
        if not files:
            return Response(content=(text + "\n") if text else "\n", media_type="text/plain; charset=utf-8")

        # есть файлы/изображения -> zip (output.txt + артефакты, имена уникализируются)
        import io
        import zipfile
        zip_buffer = io.BytesIO()
        used_names = set()

        def _unique(name):
            candidate = name
            i = 1
            while candidate in used_names:
                stem, dot, ext = name.partition(".")
                candidate = f"{stem}_{i}{dot}{ext}"
                i += 1
            used_names.add(candidate)
            return candidate

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            if text and text.strip():
                zf.writestr("output.txt", text.encode("utf-8"))
            for filename, content, _media in files:
                zf.writestr(_unique(filename), content)
        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=result.zip"},
        )

    ########################################
    # запуск
    ########################################
    ui.run(host=args.host, storage_secret=NICEGUI_STORAGE_KEY,port=args.port, favicon="favicon.ico", reload=True, show=True, ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)

main()