# OWUI-Codebox-MCP

> Per-session Python sandbox over MCP for OpenWebUI. Each user gets one
> private, **stateful**, container-isolated interpreter; code runs, results
> come back.

Execution is handled entirely by
[**llm-sandbox**](https://github.com/vndee/llm-sandbox) — no hand-rolled
sandbox. The MCP layer only does OpenWebUI integration: JWT auth, the
`services/owui.py` file bridge, and per-user session lifecycle (sliding TTL +
sweep). Same skeleton as
[OWUI-Office-MCP](https://github.com/Th3R3alDuk3/OWUI-Office-MCP).

---

## 🧩 How it works

- Each OpenWebUI user (JWT claim `id`) gets one
  `llm_sandbox.InteractiveSandboxSession` — an **IPython kernel in a
  container**. Variables, imports and files persist across `run_code` calls.
- llm-sandbox is synchronous (container SDKs), so every call is offloaded with
  `asyncio.to_thread` to keep the MCP event loop free.
- Sessions live in a `TTLCache` with a sliding idle TTL; llm-sandbox closes
  the container when the hard-cap lifetime is reached. No disk writes on the MCP side.

```
main.py                  root FastMCP server: JWT auth + mounts the subserver
config.py                pydantic-settings (.env)
services/owui.py         upload/download files to OpenWebUI (Bearer = JWT)
models/                  pydantic models (owui, sandbox)
subservers/
  _store.py              Session + SessionStore (TTLCache per user, sliding idle TTL)
  codebox/server.py      the py_* tools (call llm-sandbox directly)
scripts/dev_token.py     mint a test JWT signed with JWT_SECRET
```

## 🛠️ Tools (namespace `py`)

| Tool | |
|---|---|
| `py_run_code` | run Python in the user's container; optional `libraries` to pip-install first |
| `py_reset_session` | clear all state, start a fresh container |
| `py_session_info` | inspect the session (age, idle, backend) |
| `py_attach_file` | copy an attached OpenWebUI file into the sandbox |
| `py_list_files` | list files inside the sandbox at a given path |
| `py_save_file` | upload a sandbox-produced file back to OpenWebUI |

## 🚀 Setup

Requires a container runtime — **Docker** by default (daemon must be running);
`podman`, `kubernetes` and `micromamba` are also supported by llm-sandbox.

```bash
uv sync
cp .env.example .env
```

In `.env`:
- `JWT_SECRET` → OpenWebUI's `WEBUI_SECRET_KEY`
- `OWUI_BASE_URL` → e.g. `http://localhost:3000` (reachable from the server)
- `CONTAINER_BACKEND` → `docker` (default) / `podman` / `kubernetes` / `micromamba`
- `SANDBOX_IMAGE` → optional custom image; blank uses llm-sandbox's default

## ▶️ Run

```bash
uv run python main.py
```

Runs as `streamable-http` on `HOST:PORT`. Point OpenWebUI's MCP/tools config at
`http://<host>:<port>/mcp`.

A test token for poking at it directly:

```bash
JWT_SECRET=yoursecret uv run python scripts/dev_token.py
```

## 🐳 Docker

The server itself runs fine in a container, but it needs to reach a container
runtime to start sandboxes. The simplest setup is Docker-out-of-Docker — mount
the host Docker socket:

```bash
docker build -t owui-codebox-mcp .
docker run -d --restart unless-stopped \
  -p 8000:8000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --env-file .env \
  owui-codebox-mcp
```

## 🔒 Notes

- The container is the isolation boundary; code inside runs **unrestricted** by
  design. Harden the runtime as needed (resource limits via `SANDBOX_MAX_MEMORY`,
  network policies, a locked-down `SANDBOX_IMAGE`, gVisor, etc.). llm-sandbox
  also supports security policies if you later want to filter code.
- First `run_code` per user pays the container start (and image pull) cost.
- Sessions expire automatically: idle sessions via `SESSION_IDLE_TIMEOUT_SECONDS` (sliding TTL), containers via `SESSION_MAX_LIFETIME_SECONDS` (hard cap). No manual teardown needed.
