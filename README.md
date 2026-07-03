# OWUI-Codebox-MCP

> Disposable Python sandbox via MCP for OpenWebUI. One tool, container-isolated, stateless.

---

## 🚀 Setup

Requires a container runtime — **Docker** (daemon must be running) or
**Podman**.

```bash
uv sync
cp .env.example .env
```

In `.env`:
- `JWT_SECRET` → OpenWebUI's `WEBUI_SECRET_KEY`
- `OWUI_BASE_URL` → e.g. `http://localhost:3000` (reachable from the MCP server)
- `OWUI_VERIFY_TLS` → verify OpenWebUI's TLS certificate; set `false` only for
  self-signed or plain-HTTP lab setups
- `CONTAINER_BACKEND` → `docker` or `podman`
- `SANDBOX_IMAGE` → optional custom image; blank uses llm-sandbox's default
- `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_TRUSTED_HOST` → optional private
  package index. Blank uses PyPI; point `PIP_INDEX_URL` at a Nexus Sonatype repo
  (e.g. `https://nexus.example.com/repository/pypi/simple`) to install from there
  instead. Credentials may be embedded in the URL (`https://user:pass@host/...`).
  These are injected into the sandbox as pip-honoured env vars.

## ▶️ Run

```bash
docker pull python:3.13-trixie
uv run python main.py
```

Runs as Streamable HTTP on `HOST:PORT` from `.env`. Point OpenWebUI's MCP/tools
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
ANSI escape codes (colors, progress bars, …) are stripped from `stdout` and
`stderr` so the model sees clean text.

## 🔒 Notes

- The container is the isolation boundary. Each sandbox runs hardened by
  default: all Linux capabilities dropped (only `DAC_OVERRIDE` kept, which
  llm-sandbox needs), `no-new-privileges`, a `pids` limit (fork-bomb guard),
  no swap (so `SANDBOX_MAX_MEMORY` is a hard ceiling) and a CPU cap
  (`SANDBOX_MAX_CPUS`, e.g. `1` = one core). Tighten further as needed
  (a locked-down `SANDBOX_IMAGE`, gVisor, …).
- **Known gap — network**: the sandbox has full outbound network access (it
  needs it for `pip install`), so executed code can reach the internet and
  your LAN — including OpenWebUI itself. Restrict egress with a dedicated
  Docker network or firewall rules if that matters in your environment.
- **Known gap — disk**: there is no per-sandbox disk quota. Docker's
  `storage_opt size` requires overlay2 on xfs with `pquota`; on other backing
  filesystems (ext4, btrfs) a run can fill the disk under `/var/lib/docker`
  until the timeout tears the container down. Monitor free space, or move
  Docker's data-root to xfs to get a real quota.
- Every call pays the container start (and, the first time, image pull) cost.
- `EXEC_TIMEOUT_SECONDS` caps a single call; on timeout the container is torn
  down and the call returns an error.
- `MAX_CONCURRENT_SANDBOXES` caps how many sandbox containers may run concurrently; calls
  past the cap are rejected with a capacity error. No manual teardown needed —
  containers never outlive the call that created them.
- Each container is named `sandbox-<random>` (a short random suffix), so
  concurrent calls never collide on a name. The name is freed when the container
  is removed at the end of the call.
