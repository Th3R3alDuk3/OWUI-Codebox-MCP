# OWUI-Codebox-MCP

[![Docker](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml/badge.svg)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml)
[![Version](https://img.shields.io/github/v/tag/Th3R3alDuk3/OWUI-Codebox-MCP?label=version)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/tags)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Th3R3alDuk3/OWUI-Codebox-MCP)](LICENSE)

> Disposable, stateless Python sandboxes for OpenWebUI via MCP.

Runs each script in a fresh container, accepts OpenWebUI attachments and returns
produced files as download links. Built on [llm-sandbox](https://github.com/vndee/llm-sandbox).

## 🚀 Setup

Requires **Docker** with a running daemon, and [uv](https://docs.astral.sh/uv/).
Install and register [gVisor](https://gvisor.dev/docs/user_guide/quick_start/docker/)
as `runsc` on the **Docker daemon host**. `SANDBOX_RUNTIME` accepts only `runsc`
(default) or `runc` (ordinary Docker). A missing runtime fails the call; there is
no automatic fallback.

```bash
uv sync
cp .env.example .env
docker build -f sandbox.Dockerfile -t owui-codebox-sandbox .
```

Edit `.env`:

- `JWT_SECRET`: OpenWebUI's `WEBUI_SECRET_KEY`.
- `OWUI_BASE_URL`: OpenWebUI URL reachable from this server.
- `OWUI_VERIFY_TLS`: keep `true`; use `false` only if required for self-signed certificates.

Then start with `uv run python main.py` and connect OpenWebUI to
`http://<host>:8000/mcp`. Requests need a signed JWT with the user in the `id`
claim. Use a TLS reverse proxy outside a trusted network.

## 🛠️ Tools

| Tool | Description |
|---|---|
| `run_python` | Run Python with optional packages and input/output files |
| `list_python_packages` | List packages already installed in the sandbox image |

```json
{
  "code": "import pandas as pd; pd.DataFrame({'a': [1, 2]}).to_csv('/sandbox/result.csv')",
  "output_files": ["/sandbox/result.csv"]
}
```

- `libraries`: missing packages by name, optionally with versions/extras; compatible wheels required.
- `input_files`: OpenWebUI file IDs paired with paths under `/sandbox`.
- `output_files`: files to return from the same call. Everything else is discarded.

## 🐳 Images & deployment

The sandbox image includes data, plotting, image and Office/PDF libraries plus
fonts; see [sandbox.Dockerfile](sandbox.Dockerfile) for the package list.
It is rebuilt monthly with unpinned packages. Build it on the Docker daemon host,
or set `SANDBOX_IMAGE=ghcr.io/th3r3alduk3/owui-codebox-sandbox:latest`.

A private package index can be baked in at build time:

```bash
docker build -f sandbox.Dockerfile \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
```

To run the MCP server in Docker using the same `.env`:

```bash
docker run -d -p 8000:8000 \
  --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --env-file .env \
  --name owui-codebox-mcp \
  ghcr.io/th3r3alduk3/owui-codebox-mcp:latest
```

## ⚙️ Limits & security

[.env.example](.env.example) lists all settings: RAM, CPU, timeouts, file and
output limits, parallel sandboxes and per-user rate limits.
Keep `SANDBOX_SESSION_TIMEOUT` above `SANDBOX_EXEC_TIMEOUT`; setup and installs
count toward the session lifetime.

- **Isolation:** gVisor by default, restricted capabilities and resource limits.
- **Network:** scripts run offline; network disconnection is verified. Package
  installation is online and uses prebuilt wheels only (`--only-binary=:all:`).
- **Files:** sizes are checked before any bytes move. Only regular files under
  `/sandbox` come back; directories, symlinks and paths outside it are rejected.
- **Errors:** tool errors never carry exception text, so no URLs, hosts or stack
  traces leak into the chat.
- **Host access:** the MCP server's Docker socket grants host-level privileges.
- **Known limits:** no disk quota; stdout/stderr are truncated only after execution,
  so excessive output can consume server memory.
