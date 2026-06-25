#!/usr/bin/env python3
"""Outward-facing MCP server for Universal Harvester 2 Scripted.

Exposes Harvester capabilities to external MCP clients (agents/LLMs):
run a DSL script, discover sources/functions, list/search/get saved objects.

Runs as a SEPARATE process from the web app, against the SAME database:

    pip install fastmcp
    python mcp_server.py --host 127.0.0.1 --port 8090 --path /mcp

Authentication: every tool takes an `api_key` argument — a Harvester API key
(Settings -> API keys). The call runs in that key owner's context (their roles).
See API.md for how to create keys. See MCP.md for client configuration.
"""

import argparse
import uuid

APP_NAME = "Universal Harvester 2 Scripted"
APP_VERSION = "0.4.0"

# Те же значения по умолчанию, что и у фронта (dev-стенд против той же БД).
# В проде переопределяйте через аргументы/окружение.
DEFAULT_DB_CONF = "***FERNET_TOKEN_REMOVED***"
DEFAULT_MASTER_KEY = "***MASTER_KEY_REMOVED***"

ARGS = None


def _parse_args():
    parser = argparse.ArgumentParser(description="Universal Harvester MCP server")
    parser.add_argument("--db_conf_object", type=str, default=DEFAULT_DB_CONF, help="Encrypted DB config object (same as front)")
    parser.add_argument("--master_key", type=str, default=DEFAULT_MASTER_KEY, help="Master key to decrypt DB config / secrets")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8090, help="Bind port")
    parser.add_argument("--path", type=str, default="/mcp", help="HTTP path of the MCP endpoint")
    parser.add_argument("--transport", type=str, default="streamable-http",
                        help="MCP transport: streamable-http | sse | stdio")
    return parser.parse_args()


def _base_state():
    """Базовый current_state (без пользователя) — для аутентификации и инициализации БД."""
    return {
        "app_name": APP_NAME, "app_version": APP_VERSION, "processes": 4,
        "main_session_id": str(uuid.uuid4()), "user_session_id": str(uuid.uuid4()),
        "client_ip_address": "mcp", "client_port": 0,
        "username": "mcp", "roles": [],
        "master_key": ARGS.master_key, "db_conf": ARGS.db_conf_object,
        "codemirror_theme": "monokai", "aggrid_theme": "ag-theme-balham-dark",
    }


def _auth(api_key):
    """Проверить API-ключ и собрать current_state в контексте владельца. -> (ok, owner_or_error, state|None)."""
    from app.db import verify_api_key, get_user_by_username
    state = _base_state()
    verify_result = verify_api_key(api_key, state)
    if not verify_result[0]:
        return False, verify_result[1], None
    owner = verify_result[3]
    owner_user = get_user_by_username(owner, state)
    roles = owner_user[3].get("roles", []) if owner_user[0] else []
    state = {**state, "username": owner, "roles": roles}
    return True, owner, state


def build_mcp():
    """Собрать FastMCP-инстанс с инструментами Harvester (lazy import fastmcp).

    Все инструменты возвращают структурированный JSON (dict/list).
    Модель данных: source_object (конфиг, type='source', со ИМЕНЕМ) -> source_type
    (тип коннектора = source_object.json['type']) -> functions. В DSL источник
    вызывается по ИМЕНИ объекта: GET <source_object_name>:<function>(...)."""
    from fastmcp import FastMCP
    from app.engine import list_source_types_struct, describe_source_functions_struct
    from app import agent_actions

    mcp = FastMCP(APP_NAME)

    @mcp.tool
    def run_script(api_key: str, script: str) -> dict:
        """Execute a Harvester DSL script in the API key owner's context. Returns JSON:
        {ok, print:[{type:text|table|value, ...}], tables:{name:rows}, variables:{...}, artifacts:[...]}.
        PRINT output is structured (text / table rows / named value). Binary artifacts from
        SHOW(...,matplotlib) and SAVE(...) are only listed in `artifacts` — fetch their bytes via
        the REST endpoint POST /api/script. In DSL, fetch data via GET <source_object_name>:<function>(...);
        find available source objects with list_objects(type_filter="source")."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return {"ok": False, "error": f"auth error: {owner}"}
        return agent_actions.run_script_structured(script, state)

    @mcp.tool
    def get_dsl_reference(api_key: str) -> dict:
        """Get the Harvester DSL reference: pipeline, commands (DEF/CALC/GET/GET script/GET APPLY/
        PRINT/SHOW/SAVE/NOTIFY), parameter injection %(name)X types, in-memory SQL, and examples.
        Read this before writing scripts for run_script."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return {"error": f"auth error: {owner}"}
        return {"dsl_reference": agent_actions.dsl_reference()}

    @mcp.tool
    def list_sources(api_key: str) -> dict:
        """List SUPPORTED connector TYPES (source_type), NOT data you can query directly.
        Use this only for diagnostics or to propose configuring a NEW source object.
        To actually fetch data you must reference a configured source OBJECT by its name
        (see list_objects(type_filter="source")) — its `source_type` maps to the functions
        returned by get_source_functions."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return {"error": f"auth error: {owner}"}
        return {
            "supported_source_types": list_source_types_struct(),
            "note": ("These are connector TYPES, not queryable sources. To fetch data, reference a "
                     "configured source OBJECT by name via list_objects(type_filter='source') and call "
                     "GET <source_object_name>:<function>(...) in a DSL script."),
        }

    @mcp.tool
    def get_source_functions(api_key: str, source_type: str) -> dict:
        """Describe functions of a connector TYPE (source_type), with required/optional params per
        function and the source-object config params. Note: this is per source_type, NOT per concrete
        source object. A source object's source_type is shown by list_objects/get_object. Returns JSON
        {source_type, source_object_config_required, source_object_config_optional, functions:[...]}."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return {"error": f"auth error: {owner}"}
        return describe_source_functions_struct((source_type or "").strip())

    @mcp.tool
    def list_objects(api_key: str, type_filter: str = "") -> dict:
        """List saved objects accessible to the key owner. JSON: {objects:[{name, type, ...}], note}.
        For type='source' each item includes `source_type` (use it with get_source_functions, and call
        the source in DSL by its `name`). For type='script' items include `params` (DEF) and `return`.
        Optional type_filter: source | script | notifier | llm."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return {"error": f"auth error: {owner}"}
        return {
            "objects": agent_actions.list_objects(state, type_filter),
            "note": ("Reference a source in DSL by its 'name': GET <name>:<function>(...). "
                     "'source_type' maps to functions via get_source_functions."),
        }

    @mcp.tool
    def search_objects(api_key: str, query: str) -> dict:
        """Search saved objects by content (role-filtered). JSON: {results:[{name, type, source_type?, match}]}."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return {"error": f"auth error: {owner}"}
        return {"results": agent_actions.search_objects(state, query)}

    @mcp.tool
    def get_object(api_key: str, name: str) -> dict:
        """Get a saved object by name. JSON: {name, type, roles, json, source_type?/params?/return?}.
        For type='source' includes source_type; for type='script' includes params (DEF) and return."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return {"error": f"auth error: {owner}"}
        return agent_actions.get_object(state, name)

    return mcp


def main():
    global ARGS
    ARGS = _parse_args()
    # убедимся, что таблицы существуют (на случай первого запуска против чистой БД)
    try:
        from app.db import db_init
        db_init(_base_state())
    except BaseException as e:
        print(f"db_init warning: {e}")
    mcp = build_mcp()
    if ARGS.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=ARGS.transport, host=ARGS.host, port=ARGS.port, path=ARGS.path)


if __name__ == "__main__":
    main()
