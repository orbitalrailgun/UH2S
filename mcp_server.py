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
    """Собрать FastMCP-инстанс с инструментами Harvester (lazy import fastmcp)."""
    from fastmcp import FastMCP
    from app.interface import execute_script_api
    from app.engine import list_source_types, describe_source_functions
    from app import agent_actions

    mcp = FastMCP(APP_NAME)

    @mcp.tool
    def run_script(api_key: str, script: str) -> str:
        """Execute a Harvester DSL script in the API key owner's context.
        Returns PRINT text and, if any, a manifest of produced artifacts
        (SHOW matplotlib images / SAVE files). Download binary artifacts via the
        REST endpoint POST /api/script (this MCP tool returns text only)."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return f"auth error: {owner}"
        result = execute_script_api(script, state)
        if not result[0]:
            return f"error: {result[1]}"
        payload = result[3]
        text = payload.get("text", "")
        files = payload.get("files", [])
        out = text or "(no PRINT output)"
        if files:
            manifest = "\n".join(f"- {name} ({len(content)} bytes, {media})" for name, content, media in files)
            out += (f"\n\n[artifacts: {len(files)}]\n{manifest}\n"
                    "(download binary artifacts via REST: POST /api/script)")
        return out

    @mcp.tool
    def list_sources(api_key: str) -> str:
        """List available source types (connectors)."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return f"auth error: {owner}"
        return list_source_types()

    @mcp.tool
    def get_source_functions(api_key: str, source_type: str) -> str:
        """Describe the functions of a source type and their required/optional parameters."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return f"auth error: {owner}"
        return describe_source_functions((source_type or "").strip())

    @mcp.tool
    def list_objects(api_key: str, type_filter: str = "") -> str:
        """List saved objects accessible to the key owner (scripts include their DEF params)."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return f"auth error: {owner}"
        return agent_actions.list_objects(state, type_filter)

    @mcp.tool
    def search_objects(api_key: str, query: str) -> str:
        """Search saved objects by content (role-filtered)."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return f"auth error: {owner}"
        return agent_actions.search_objects(state, query)

    @mcp.tool
    def get_object(api_key: str, name: str) -> str:
        """Get a saved object by name (full JSON; scripts include DEF params)."""
        ok, owner, state = _auth(api_key)
        if not ok:
            return f"auth error: {owner}"
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
