# OWUI-Codebox-MCP

> Per-session Python sandbox via MCP for OpenWebUI. Private, stateful, container-isolated.

---

## 🚀 Setup

Requires a container runtime — **Docker** by default (daemon must be running);
`podman` and `kubernetes` also work via llm-sandbox.

```bash
uv sync
cp .env.example .env
```

In `.env`:
- `JWT_SECRET` → OpenWebUI's `WEBUI_SECRET_KEY`
- `OWUI_BASE_URL` → e.g. `http://localhost:3000` (reachable from the MCP server)
- `CONTAINER_BACKEND` → `docker` (default) / `podman` / `kubernetes`
- `SANDBOX_IMAGE` → optional custom image; blank uses llm-sandbox's default

## ▶️ Run

```bash
uv run python main.py
```

Runs as `streamable-http` on `HOST:PORT` from `.env`. Point OpenWebUI's MCP/tools
config at `http://<host>:<port>/mcp`. Mint a test token with
`JWT_SECRET=yoursecret uv run python scripts/dev_token.py`.

## 🐳 Docker (optional)

The server runs fine in a container but needs to reach a container runtime to
start sandboxes. Simplest is Docker-out-of-Docker — mount the host socket:

```bash
docker build -t owui-codebox-mcp .

docker run -d --restart unless-stopped \
-p 8000:8000 \
-v /var/run/docker.sock:/var/run/docker.sock \
--env-file .env \
owui-codebox-mcp
```

Config is read from your `.env` via `--env-file`; `HOST=0.0.0.0` makes the server
reachable from outside the container. Prebuilt images are published to **ghcr.io**
on every push — see [GITHUB.md](GITHUB.md).

## 🛠️ Tools (namespace `py`)

Each user (JWT claim `id`) gets one private `llm_sandbox.InteractiveSandboxSession`
— an IPython kernel in a container. Variables, imports and files persist across
`run_python` calls; sessions live in a dict with a sliding idle sweep, no persistent 
disk writes on the MCP side.

| Tool | |
|---|---|
| `py_run_python` | run Python in the user's container; `libraries` to pip-install first |
| `py_reset_session` | clear all state, start a fresh container |
| `py_session_info` | inspect the session (age, idle, backend) |
| `py_attach_file` | copy an attached OpenWebUI file into the sandbox |
| `py_run_command` | run a shell command in the sandbox (stdout/stderr/exit code) |
| `py_save_file` | upload a sandbox-produced file back to OpenWebUI |

## 🔒 Notes

- The container is the isolation boundary; code inside runs **unrestricted** by
  design. Harden the runtime as needed (`SANDBOX_MAX_MEMORY`, network policies, a
  locked-down `SANDBOX_IMAGE`, gVisor, …). llm-sandbox also supports security
  policies if you later want to filter code.
- First `run_python` per user pays the container start (and image pull) cost.
- Sessions expire automatically. Three independent timeouts apply:
  - `SESSION_IDLE_TIMEOUT_SECONDS` — a background sweep (every
    `SESSION_SWEEP_INTERVAL_SECONDS`) reaps containers idle longer than this; the
    idle clock resets on every call.
  - `SESSION_MAX_LIFETIME_SECONDS` — a hard wall-clock cap (default `1800`; `0`
    disables) that closes a container that many seconds after it started,
    regardless of activity. The next call then reports a timeout.
  - `EXEC_TIMEOUT_SECONDS` — caps a single call; it aborts that one call but
    keeps the session alive.

  Logs (`fastmcp.codebox`) record every start, reap and capacity rejection. No
  manual teardown needed.
