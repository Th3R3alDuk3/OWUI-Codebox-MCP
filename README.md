# OWUI-Codebox-MCP

[![Docker](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml/badge.svg)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml)
[![Version](https://img.shields.io/github/v/tag/Th3R3alDuk3/OWUI-Codebox-MCP?label=version)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/tags)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Th3R3alDuk3/OWUI-Codebox-MCP)](LICENSE)

> Disposable, stateless Python sandboxes for OpenWebUI via MCP.

Runs each script in a fresh microVM, accepts OpenWebUI attachments and returns
produced files as download links. Built on [microsandbox](https://github.com/superradcompany/microsandbox).

## 🚀 Setup

Requires a Linux host with **KVM** and [uv](https://docs.astral.sh/uv/). The
server's user must be able to read and write `/dev/kvm`; `uv run msb doctor`
checks that.

Turn nested virtualization off on the host (`kvm_amd` on AMD). With it on,
scripts get a working `/dev/kvm` inside their microVM and reach the host's
nested-virtualization code:

```bash
echo "options kvm_intel nested=0" | sudo tee /etc/modprobe.d/kvm-nested.conf
sudo modprobe -r kvm_intel && sudo modprobe kvm_intel
```

1. Install and configure:

   ```bash
   uv sync
   cp .env.example .env
   ```

   - `JWT_SECRET`: OpenWebUI's `WEBUI_SECRET_KEY`.
   - `OWUI_BASE_URL`: OpenWebUI URL reachable from this server.
   - `OWUI_VERIFY_TLS`: keep `true`; use `false` only if required for self-signed certificates.

2. Pull the sandbox image. A missing image is pulled by the first tool call,
   which can outlast the client's tool timeout:

   ```bash
   uv run msb pull ghcr.io/th3r3alduk3/owui-codebox-sandbox:latest
   ```

3. Start the server and connect OpenWebUI to `http://<host>:8000/mcp`:

   ```bash
   uv run python main.py
   ```

Requests need a signed JWT with the user in the `id` claim. Use a TLS reverse
proxy outside a trusted network.

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

## 📦 Sandbox image

The image includes data, plotting, image and Office/PDF libraries plus fonts;
see [sandbox.Dockerfile](sandbox.Dockerfile) for the package list. It is rebuilt
monthly with unpinned packages; `msb pull --force` fetches the update.

`SANDBOX_IMAGE` accepts any OCI reference. Registry credentials, plain-HTTP
registries and custom CAs go into `~/.microsandbox/config.json`.

A locally built image is loaded into microsandbox's image store and referenced
as `SANDBOX_IMAGE=owui-codebox-sandbox:latest`. A private package index can be
baked in at build time:

```bash
docker build -f sandbox.Dockerfile \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
docker save owui-codebox-sandbox | uv run msb load
```

## 🐳 Docker

The server runs in a container with the same `.env`. It needs the KVM device,
and a volume so pulled images survive restarts:

```bash
docker build -t owui-codebox-mcp .
docker run -d -p 8000:8000 \
  --restart unless-stopped \
  --device /dev/kvm \
  -v owui-codebox-data:/root/.microsandbox \
  --env-file .env \
  --name owui-codebox-mcp \
  owui-codebox-mcp
docker exec owui-codebox-mcp uv run --no-sync msb pull \
  ghcr.io/th3r3alduk3/owui-codebox-sandbox:latest
```

A prebuilt image is available as `ghcr.io/th3r3alduk3/owui-codebox-mcp:latest`.

## ⚙️ Limits & security

[.env.example](.env.example) lists all settings: RAM, CPU, timeouts, file and
output limits, parallel sandboxes and per-user rate limits.
Keep `SANDBOX_MAX_DURATION` above `SANDBOX_EXEC_TIMEOUT`; it bounds the
lifetime of each microVM, including pip installs.

- **Isolation:** every call boots its own microVM with its own kernel (KVM via
  libkrun), the restricted in-guest security profile and fixed RAM/vCPU caps.
- **Network:** scripts run in a microVM whose network policy denies all traffic
  from boot. `libraries` are installed beforehand by a separate, online microVM
  from prebuilt wheels only (`--only-binary=:all:`) and handed over through a
  read-only mount.
- **Disk:** a microVM can write 4 GiB to its own disk, and `libraries` can take
  4 GiB in the server's `/var/tmp` (microsandbox defaults). Both are removed
  after the call.
- **Files:** transfers are cut off at the size limit, never buffered beyond it.
  Only regular files under `/sandbox` come back; directories, symlinks and paths
  outside it are rejected.
- **Errors:** tool errors never carry exception text, so no URLs, hosts or stack
  traces leak into the chat.
- **Output:** stdout/stderr are read as a stream. A run that prints more than
  `SANDBOX_MAX_FILE_SIZE` bytes is killed; the first `SANDBOX_MAX_OUTPUT`
  characters of each stream are returned.
- **Host access:** the server needs `/dev/kvm` only.
