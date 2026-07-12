# OWUI-Codebox-MCP

[![Docker](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml/badge.svg)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml)
[![Version](https://img.shields.io/github/v/tag/Th3R3alDuk3/OWUI-Codebox-MCP?label=version)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/tags)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Th3R3alDuk3/OWUI-Codebox-MCP)](LICENSE)

> Disposable Python sandboxes via MCP for OpenWebUI. Container-isolated, stateless, offline.

Python execution for OpenWebUI over the Model Context Protocol. The model
sends a self-contained script; the server runs it in a fresh, hardened
container (powered by [llm-sandbox](https://github.com/vndee/llm-sandbox)) —
packages installed, attached files copied in, network cut, produced files
uploaded back as download links — then the container is destroyed.

---

## ✨ Highlights

- **Disposable sandboxes** — every call gets a fresh container, removed
  right after; nothing persists between calls
- **Hardened by default** — capabilities dropped, `no-new-privileges`,
  pids limit, hard RAM/CPU caps, execution timeout
- **Network isolation** — packages install first, then the container is
  detached from every network before user code runs
- **Files in & out** — attached OpenWebUI files land at chosen sandbox
  paths; produced files come back as download links
- **Multi-user by design** — JWT auth against OpenWebUI's secret, per-user
  sandbox cap, per-user rate limiting
- **Prebuilt sandbox image** — optional code-interpreter stack (pandas,
  matplotlib, opencv, PDF/office libs, …) with private-index support

## 🚀 Setup

Requires **Docker** (daemon running).

```bash
uv sync
cp .env.example .env
```

`.env.example` documents every setting; the ones you must set:

- `JWT_SECRET` → OpenWebUI's `WEBUI_SECRET_KEY`
- `OWUI_BASE_URL` → OpenWebUI URL reachable from this server, e.g. `http://localhost:3000`
- `OWUI_VERIFY_TLS` → set `false` only for self-signed or plain-HTTP lab setups

## 🏃 Run

```bash
uv run python main.py
```

The server listens on `0.0.0.0:8000`. Point OpenWebUI's MCP/tools config at
`http://<host>:8000/mcp`. Requests must carry a JWT signed by `JWT_SECRET`
with the OpenWebUI user's `id` claim.

## 🐳 Docker (optional)

Prebuilt images are published to **ghcr.io** on pushes to `main` (`latest`)
and on version tags (`X.Y.Z`). The server needs a container runtime to start
sandboxes — simplest is mounting the host socket (Docker-out-of-Docker):

```bash
docker run -d -p 8000:8000 \
--restart unless-stopped \
-v /var/run/docker.sock:/var/run/docker.sock \
--env-file .env \
--name owui-codebox-mcp \
ghcr.io/th3r3alduk3/owui-codebox-mcp:latest
```

Or build the image locally: `docker build -t owui-codebox-mcp .`

## 🛠️ Tools

| Tool | Description |
|---|---|
| `run_python` | execute a script in a fresh sandbox: install packages, read attached OpenWebUI files, upload produced files |
| `list_python_packages` | list packages preinstalled in the sandbox image (cached) |

The default sandbox image is the official `python:3.13-trixie` (pulled
automatically on first use). For faster sandboxes, `sandbox.Dockerfile`
extends it with a code-interpreter stack (numpy, pandas, matplotlib,
scikit-learn, openpyxl, pymupdf, weasyprint, opencv, …) and fonts for
HTML→PDF; a private package index can be baked in via build args:

```bash
docker build -f sandbox.Dockerfile \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
```

Then set `SANDBOX_IMAGE=owui-codebox-sandbox` in `.env`.

## 🔒 Sandboxing & limits

- The container is the isolation boundary: all capabilities dropped (only
  `DAC_OVERRIDE` kept, which llm-sandbox needs), `no-new-privileges`, pids
  limit, no swap (`SANDBOX_MAX_MEMORY` is a hard ceiling), CPU cap
  (`SANDBOX_MAX_CPUS`). Tighten further as needed (locked-down image, gVisor, …).
- **Network**: user code always runs offline. A call without `libraries`
  gets a container with no network at all; a call with `libraries` installs
  them first (the only online window, and only pip runs in it), then the
  container is detached from every network — verified cut, or the call is
  refused — before the code runs. No internet, no LAN, not even OpenWebUI.
- **Known gap — disk**: no per-sandbox quota (Docker's `storage_opt` needs
  overlay2 on xfs); a run can fill `/var/lib/docker` until the timeout
  tears the container down — monitor free space.
- Every call pays the container start (plus, the first time, the image
  pull) — well under a second without `libraries`; requesting `libraries`
  adds the venv setup and pip installs.
- `SANDBOX_EXEC_TIMEOUT` (seconds) caps a call; `SANDBOX_MAX_FILE_SIZE`
  (bytes) caps each transferred file.
- `MAX_CONCURRENT_SANDBOXES` caps sandboxes server-wide,
  `MAX_CONCURRENT_SANDBOXES_PER_USER` per user; on top, requests are
  rate-limited per user (`RATE_LIMIT_RPS` sustained, `RATE_LIMIT_BURST`
  burst), keyed on the JWT's `id` claim.
- **No internals in errors**: only deliberately raised tool errors reach the
  model, and they never embed exception text — no URLs, hosts or stack
  traces leak into the chat; anything unexpected is masked by the server.
- The server speaks plain HTTP — put it behind a reverse proxy for TLS.
