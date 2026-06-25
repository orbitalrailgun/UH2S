#!/usr/bin/env python3
"""Быстрая проверка MCP-сервера Harvester (in-memory, без сети).

Запуск (после `pip install fastmcp`):
    python mcp_smoke.py <API_KEY> [script]

Использует встроенный клиент FastMCP, который вызывает инструменты сервера в том же
процессе (HTTP не нужен). Проверяет: список инструментов, list_sources, list_objects
и run_script. API-ключ создаётся в Settings -> API keys (см. API.md).
"""

import argparse
import asyncio


def _build_args_namespace():
    # ARGS как у mcp_server, но без чтения sys.argv (используем дефолты той же БД)
    import mcp_server
    return argparse.Namespace(
        db_conf_object=mcp_server.DEFAULT_DB_CONF,
        master_key=mcp_server.DEFAULT_MASTER_KEY,
        host="127.0.0.1", port=8090, path="/mcp", transport="streamable-http",
    )


async def main():
    parser = argparse.ArgumentParser(description="Harvester MCP smoke test")
    parser.add_argument("api_key", help="Harvester API key (Settings -> API keys)")
    parser.add_argument("script", nargs="?",
                        default='GET sqlite:query("SELECT 1 AS n, \'ok\' AS status") AS t | PRINT(t)',
                        help="DSL script for run_script")
    cli = parser.parse_args()

    import mcp_server
    from fastmcp import Client
    mcp_server.ARGS = _build_args_namespace()
    mcp = mcp_server.build_mcp()

    async with Client(mcp) as client:
        tools = await client.list_tools()
        print("tools:", [t.name for t in tools])

        async def call(name, **kwargs):
            print(f"\n=== {name}({', '.join(k for k in kwargs if k != 'api_key')}) ===")
            result = await client.call_tool(name, {"api_key": cli.api_key, **kwargs})
            # совместимость с разными версиями fastmcp: data / content[].text / str
            text = getattr(result, "data", None)
            if text is None:
                content = getattr(result, "content", None)
                if content:
                    text = "\n".join(getattr(c, "text", str(c)) for c in content)
            print(text if text is not None else result)

        await call("list_sources")
        await call("list_objects", type_filter="")
        await call("run_script", script=cli.script)


if __name__ == "__main__":
    asyncio.run(main())
