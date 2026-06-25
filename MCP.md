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

## 3. Data model (read this first)

Harvester distinguishes three things — keep them straight:

- **source_object** — a *configured* object (type `source`) with a **name**, e.g. `netbox`,
  `thehive`, `sqlite3`. This is what you actually call.
- **source_type** — the *connector kind* behind it (`source_object.json["type"]`), e.g.
  `netbox`, `sqlite3_im`. It determines which functions exist.
- **functions** — operations of a source_type (e.g. `query`, `search`, `get_alerts`).

In a DSL script you fetch data by the source **object name**:
`GET <source_object_name>:<function>(params...) AS table`.

So the flow for an agent is: `list_objects(type_filter="source")` → pick an object and read its
`source_type` → `get_source_functions(source_type)` → build `GET <name>:<function>(...)` →
`run_script`. `list_sources` is **only** the catalog of supported connector TYPES (diagnostics /
proposing a new source object) — it is **not** the list of things you can query.

## 4. Tools (all return JSON)

| Tool | Args | Returns (JSON) |
|------|------|----------------|
| `run_script` | `api_key`, `script` | `{ok, print:[{type:text\|table\|value,…}], tables:{name:rows}, variables:{…}, artifacts:[…]}` |
| `list_sources` | `api_key` | `{supported_source_types:[…], note}` — connector TYPES (diagnostic), not queryable directly |
| `get_source_functions` | `api_key`, `source_type` | `{source_type, source_object_config_required, source_object_config_optional, functions:[{function, required, optional}]}` |
| `list_objects` | `api_key`, `type_filter?` | `{objects:[{name, type, source_type?/params?/return?}], note}` |
| `search_objects` | `api_key`, `query` | `{results:[{name, type, source_type?, match}]}` |
| `get_object` | `api_key`, `name` | `{name, type, roles, json, source_type?/params?/return?}` |

`run_script` returns structured data (PRINT as text/table/value). Binary artifacts from
`SHOW(…,matplotlib)` and `SAVE(…)` are only **listed** in `artifacts` — fetch their bytes via the
REST endpoint `POST /api/script` (see [`API.md`](API.md)), which returns a zip.

---

## 5. Testing the server

1. **In-process smoke test (no network)** — fastest correctness check (tools/auth/DB):
   ```bash
   pip install fastmcp
   python mcp_smoke.py uh_YOUR_KEY
   ```
2. **MCP Inspector (GUI, over HTTP)** — start the server, then connect the inspector:
   ```bash
   python mcp_server.py --port 8090            # terminal 1
   npx @modelcontextprotocol/inspector         # terminal 2 (version-independent)
   # in the UI: Transport = Streamable HTTP, URL = http://127.0.0.1:8090/mcp
   ```
   The bundled FastMCP CLI varies by version — check `fastmcp --help` / `fastmcp inspector --help`
   (some builds expose only `inspector`/`apps`, without `dev`/`run`). The `npx` inspector above
   works regardless of the FastMCP CLI.
3. **Real MCP client** (Claude Desktop / IDE) — see §6.

> DSL note: the in-memory SQL source is `sqlite3` and its function takes a list:
> `GET sqlite3:query(queries=["SELECT 1 AS n"]) AS t | PRINT(t)`.

## 6. MCP client configuration

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
script  = "GET sqlite3:query(queries=[\"SELECT 1 AS n\"]) AS t | PRINT(t)"
```

---

## 7. Security notes

- A key authorizes execution of **arbitrary DSL** in the owner's context (including
  SQL — the accepted model for a trusted audience). Issue keys to owners with the
  least necessary roles.
- Restrict network access to the MCP port at the infrastructure level
  (reverse-proxy / firewall / Istio) per your policies.
- The server decrypts the DB config with the master key — protect the host and the
  `--master_key`/`--db_conf_object` values as you do for the web app.

---

## 8. Relation to the rest

- **REST** (`API.md`) — single `POST /api/script`, returns text or a zip with binary
  artifacts; best for file outputs and simple automation.
- **MCP** (this) — tool-based discovery + execution for MCP-aware agents; best for
  LLM/agent integrations that browse capabilities and call them as tools.
- Both authenticate with the same API keys and run in the owner's context.
