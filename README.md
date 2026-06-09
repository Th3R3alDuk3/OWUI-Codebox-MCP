# OWUI-Codebox-MCP

> Disposable Python sandbox via MCP for OpenWebUI. One tool, container-isolated, stateless.

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
- `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_TRUSTED_HOST` → optional private
  package index. Blank uses PyPI; point `PIP_INDEX_URL` at a Nexus Sonatype repo
  (e.g. `https://nexus.example.com/repository/pypi/simple`) to install from there
  instead. Credentials may be embedded in the URL (`https://user:pass@host/...`).
  These are injected into the sandbox as pip-honoured env vars.

## ▶️ Run

```bash
uv run python main.py
```

Runs as `streamable-http` on `HOST:PORT` from `.env`. Point OpenWebUI's MCP/tools
config at `http://<host>:<port>/mcp`. The server authenticates requests with a
JWT signed by `JWT_SECRET` carrying the OpenWebUI user's `id` claim.

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
on pushes to `main` and on version tags.

## 🛠️ Tool

A single tool, `run_python`. Every call spins up a fresh
`llm_sandbox.SandboxSession` container, runs the code, and tears the container
down again right after — nothing persists between calls, so each call must send
a complete, self-contained script.

| Parameter | |
|---|---|
| `code` | self-contained Python to execute |
| `libraries` | packages to pip-install first (required for any non-stdlib import) |
| `input_file_id` | OpenWebUI file ID to copy into the workdir under its original name before running |
| `output_file_path` | path of a file the code writes; returned to the user after a successful run |

The result carries `exit_code`, `stdout`, `stderr`, `duration_ms`, and — when
`output_file_path` is set — an `output_file` with the OpenWebUI download URL.

## 🔒 Notes

- The container is the isolation boundary; code inside runs **unrestricted** by
  design. Harden the runtime as needed (`SANDBOX_MAX_MEMORY`, network policies, a
  locked-down `SANDBOX_IMAGE`, gVisor, …). llm-sandbox also supports security
  policies if you later want to filter code.
- Every call pays the container start (and, the first time, image pull) cost.
- `EXEC_TIMEOUT_SECONDS` caps a single call; on timeout the container is torn
  down and the call returns an error.
- `MAX_CONCURRENT` caps how many sandbox containers may run concurrently; calls
  past the cap are rejected with a capacity error. No manual teardown needed —
  containers never outlive the call that created them.
- Each container is named `sandbox-<random>` (a short random suffix), so
  concurrent calls never collide on a name. The name is freed when the container
  is removed at the end of the call.
