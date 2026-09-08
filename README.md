# OWUI-Codebox-MCP

[![Docker](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml/badge.svg)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml)
[![Version](https://img.shields.io/github/v/tag/Th3R3alDuk3/OWUI-Codebox-MCP?label=version)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/tags)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Th3R3alDuk3/OWUI-Codebox-MCP)](LICENSE)

> Disposable, stateless Python sandboxes for OpenWebUI via MCP.

The model sends a self-contained script. The server runs it in a fresh, hardened
container: packages installed, attached files copied in, network cut, produced
files uploaded back as download links. Then the container is destroyed. Built on
[llm-sandbox](https://github.com/vndee/llm-sandbox).

---

## ✨ Highlights

- **Disposable** — fresh container per call, removed right after
- **Offline user code** — packages install first, then every network is cut
- **Hardened** — dropped privileges and hard RAM, CPU and time limits
- **Files in & out** — OpenWebUI attachments in, produced files back as links
- **Multi-user** — JWT auth, per-user sandbox cap and rate limit

## 🚀 Quick start

Requires **Docker** with a running daemon, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
docker build -f sandbox.Dockerfile -t owui-codebox-sandbox .
uv run python main.py
```

Set at least these in `.env`:

- `JWT_SECRET` → OpenWebUI's `WEBUI_SECRET_KEY`. Both sides need the same value.
- `OWUI_BASE_URL` → OpenWebUI URL reachable from this server
- `OWUI_VERIFY_TLS` → `false` only for self-signed or plain-HTTP setups

The server listens on `0.0.0.0:8000`. Point OpenWebUI's MCP config at
`http://<host>:8000/mcp`. Requests must carry a JWT signed with `JWT_SECRET`
that names the user in the `id` claim. The server speaks plain HTTP, so put a
TLS reverse proxy in front of it outside a trusted network.

## 🛠️ Tools

| Tool | Description |
|---|---|
| `run_python` | Run a script in a fresh sandbox: install packages, read attached files, return produced files |
| `list_python_packages` | List what is already installed in the sandbox image |

```jsonc
{
  "code": "import pandas as pd; pd.DataFrame({'a': [1, 2]}).to_csv('/sandbox/result.csv')",
  "output_files": ["/sandbox/result.csv"]
}
```

Use `libraries` only for packages the image does not already ship. `input_files`
pairs an OpenWebUI file ID with a sandbox path. Files the script writes come
back only if they are listed in `output_files` in the same call — everything
else dies with the container.

## 🐳 Images

| Image | Built from | Role | Published |
|---|---|---|---|
| `owui-codebox-mcp` | `Dockerfile` | The MCP server | ghcr.io, on `main` and tags |
| `owui-codebox-sandbox` | `sandbox.Dockerfile` | Where user code runs | ghcr.io, on Dockerfile changes and monthly |

The sandbox image is part of the standard setup. It adds a code-interpreter
stack (numpy, pandas, polars, duckdb, scikit-learn, matplotlib, OpenCV,
openpyxl, python-docx, pymupdf, reportlab, weasyprint, …) plus Latin, CJK and
emoji fonts. It ships no HTTP client on purpose, since the sandbox is offline
when the code runs. Nothing in it is version-pinned, so CI rebuilds it monthly
and `latest` picks up security updates.

Build it as shown in Quick start, or skip the build and point `SANDBOX_IMAGE` at
`ghcr.io/th3r3alduk3/owui-codebox-sandbox`. Any other image works too — a plain
`python:3.13-trixie` needs no build but pays for every package at call time.

A private package index can be baked in at build time:

```bash
docker build -f sandbox.Dockerfile \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
```

## ⚙️ Configuration

`.env.example` documents every setting.

| Variable | Purpose |
|---|---|
| `JWT_SECRET` | OpenWebUI's `WEBUI_SECRET_KEY`; required |
| `OWUI_BASE_URL` | OpenWebUI URL, reachable from this server; required |
| `OWUI_VERIFY_TLS` | Verify OpenWebUI's TLS certificate |
| `SANDBOX_IMAGE` | Image user code runs in |
| `SANDBOX_MAX_MEMORY` | Hard RAM ceiling per sandbox |
| `SANDBOX_MAX_CPUS` | CPU cap per sandbox |
| `SANDBOX_EXEC_TIMEOUT` | Seconds the script may run |
| `SANDBOX_SESSION_TIMEOUT` | Whole container lifetime; keep it well above the exec timeout, installs count against it |
| `SANDBOX_MAX_FILE_SIZE` | Bytes per transferred file |
| `SANDBOX_MAX_FILES` | Input and output files per call |
| `SANDBOX_MAX_LIBRARIES` | Packages per call |
| `SANDBOX_MAX_OUTPUT` | Characters kept per output stream |
| `MAX_CONCURRENT_SANDBOXES` | Server-wide sandbox cap |
| `MAX_CONCURRENT_SANDBOXES_PER_USER` | Per-user sandbox cap |
| `RATE_LIMIT_RPS` / `RATE_LIMIT_BURST` | Per-user request rate |

## 🔒 Security model

**Isolation.** The container is the boundary. It runs with dropped privileges
and hard limits on memory, CPU, processes and time. A Docker container is not a
complete boundary against code attacking the kernel — for untrusted or public
workloads, run Docker rootless and add gVisor or a microVM runtime.

**Network.** A call without `libraries` gets a container with no network at all.
With `libraries`, only the install step is online, and only pip runs in it.
Afterwards the container is detached from every network. That cut is verified,
and the call is refused if it cannot be confirmed. No internet, no LAN, not even
OpenWebUI.

**Files.** Sizes are checked before any bytes move: an input file stops
downloading past the limit, an output file is inspected inside the sandbox
first. Only regular files under `/sandbox` come back — directories, symlinks
and paths outside it are rejected.

**Errors.** Tool errors never carry exception text, so no URLs, hosts or stack
traces leak into the chat. Anything unexpected is masked.

**Known limits.** There is no disk quota per sandbox, so a run can fill Docker's
storage until the timeout ends it. `SANDBOX_MAX_OUTPUT` is applied after the
run, so a very noisy script can cost real server memory before the timeout stops
it. And the Docker socket the server needs is as powerful as root on the host.

## 📦 Docker deployment

The server needs a container runtime to start sandboxes. The simplest way is to
mount the host socket:

```bash
docker run -d -p 8000:8000 \
--restart unless-stopped \
-v /var/run/docker.sock:/var/run/docker.sock \
--env-file .env \
--name owui-codebox-mcp \
ghcr.io/th3r3alduk3/owui-codebox-mcp:latest
```

Build the sandbox image on the host, not inside the server container — it has to
exist on the same Docker daemon the server reaches through that socket.
