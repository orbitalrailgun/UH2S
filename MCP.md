# Universal Harvester — MCP server (outward)

Outward-facing **MCP server** that exposes Harvester capabilities to external MCP
clients (agents/LLMs): run DSL scripts, discover sources/functions, list/search/get
saved objects. Runs as a **separate process** from the web app, against the **same
database**. Related: [`API.md`](API.md) (REST), [`HARVESTER_DSL.md`](HARVESTER_DSL.md).

---

## 1. Install & run

```bash
pip install fastmcp
python mcp_server.py --host 127.0.0.1 --port 8090 --path /mcp
```

Arguments (defaults match the web app's dev stand, same DB):
- `--db_conf_object` — encrypted DB config object (same as `front.py`);
- `--master_key` — master key to decrypt the DB config / secrets;
- `--host` / `--port` / `--path` — HTTP bind and MCP endpoint path;
- `--transport` — `streamable-http` (default) | `sse` | `stdio`.

The server reaches the same database as the web app, so objects, secrets and API
keys are shared. On first run it ensures tables exist (`db_init`).

> Requires `fastmcp` (FastMCP v2). It is **optional** — the web app and REST API
> run without it. `mcp.run(...)` argument names may vary slightly between FastMCP
> versions; adjust `--transport`/`--path` if your version differs.

---

## 2. Authentication

Every tool takes an **`api_key`** argument — a Harvester API key created in
**Settings → API keys** (roles `fullmaster`/`apiadmin`; see [`API.md`](API.md)).
The call runs in that key **owner's context** (their roles): object visibility and
source/notifier access follow the owner's permissions. Disabled/expired keys and
keys of blocked owners are rejected.

---

## 3. Tools

| Tool | Args | Returns |
|------|------|---------|
| `run_script` | `api_key`, `script` | PRINT text; if the script has `SHOW matplotlib`/`SAVE`, a manifest of artifacts (download binaries via REST `POST /api/script`) |
| `list_sources` | `api_key` | available source types (connectors) |
| `get_source_functions` | `api_key`, `source_type` | functions of a source + required/optional params |
| `list_objects` | `api_key`, `type_filter?` | accessible objects (scripts include DEF params) |
| `search_objects` | `api_key`, `query` | objects matching a content query (role-filtered) |
| `get_object` | `api_key`, `name` | full object JSON (scripts include DEF params) |

Note: `run_script` returns **text only**. For binary artifacts (matplotlib PNG,
`SAVE` files) use the REST endpoint `POST /api/script` (see [`API.md`](API.md)),
which returns a zip.

---

## 4. MCP client configuration

Streamable-HTTP endpoint: `http://<host>:<port><path>` (e.g. `http://127.0.0.1:8090/mcp`).

Example (Claude Desktop / generic MCP client `mcpServers` block):

```json
{
  "mcpServers": {
    "universal-harvester": {
      "transport": "http",
      "url": "http://127.0.0.1:8090/mcp"
    }
  }
}
```

For stdio transport (client launches the process directly):

```json
{
  "mcpServers": {
    "universal-harvester": {
      "command": "python",
      "args": ["/path/to/mcp_server.py", "--transport", "stdio"]
    }
  }
}
```

Then call a tool, passing your API key, e.g. `run_script`:

```
api_key = "uh_xxxxxxxx..."
script  = "GET sqlite:query(\"SELECT 1 AS n\") AS t | PRINT(t)"
```

---

## 5. Security notes

- A key authorizes execution of **arbitrary DSL** in the owner's context (including
  SQL — the accepted model for a trusted audience). Issue keys to owners with the
  least necessary roles.
- Restrict network access to the MCP port at the infrastructure level
  (reverse-proxy / firewall / Istio) per your policies.
- The server decrypts the DB config with the master key — protect the host and the
  `--master_key`/`--db_conf_object` values as you do for the web app.

---

## 6. Relation to the rest

- **REST** (`API.md`) — single `POST /api/script`, returns text or a zip with binary
  artifacts; best for file outputs and simple automation.
- **MCP** (this) — tool-based discovery + execution for MCP-aware agents; best for
  LLM/agent integrations that browse capabilities and call them as tools.
- Both authenticate with the same API keys and run in the owner's context.
