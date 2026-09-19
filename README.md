# OWUI-Codebox-MCP

[![Docker](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml/badge.svg)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/docker.yml)
[![Version](https://img.shields.io/github/v/tag/Th3R3alDuk3/OWUI-Codebox-MCP?label=version)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/tags)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Th3R3alDuk3/OWUI-Codebox-MCP)](LICENSE)

> Isolated Python sandboxes for OpenWebUI via MCP.

Runs scripts in microVMs that stay alive for a short session, accepts OpenWebUI
attachments and returns produced files as download links. Built on [microsandbox](https://github.com/superradcompany/microsandbox).

## 🚀 Setup

Requires a Linux host with **KVM** and Docker.

Turn nested virtualization off on the host (`kvm_amd` on AMD). With it on,
scripts get a working `/dev/kvm` inside their microVM and reach the host's
nested-virtualization code:

```bash
echo "options kvm_intel nested=0" | sudo tee /etc/modprobe.d/kvm-nested.conf
sudo modprobe -r kvm_intel && sudo modprobe kvm_intel
```

1. Build the sandbox image and the server image:

   ```bash
   docker build -f docker/Dockerfile.sandbox -t owui-codebox-sandbox .
   docker build -f docker/Dockerfile.app -t owui-codebox-mcp .
   ```

   The sandbox image includes data, plotting, image and Office/PDF libraries
   plus fonts; see [Dockerfile.sandbox](docker/Dockerfile.sandbox) for the
   package list. Packages are unpinned, so a rebuild picks up updates.

2. Configure:

   ```bash
   cp .env.example .env
   ```

   - `JWT_SECRET`: OpenWebUI's `WEBUI_SECRET_KEY`.
   - `OWUI_BASE_URL`: OpenWebUI URL reachable from this server.
   - `OWUI_VERIFY_TLS`: keep `true`; use `false` only if required for self-signed certificates.

3. Start the server and load the sandbox image into it. The container needs
   the KVM device and a volume so loaded images survive restarts:

   ```bash
   docker run -d -p 8000:8000 \
     --restart unless-stopped \
     --device /dev/kvm \
     -v owui-codebox-data:/root/.microsandbox \
     --env-file .env \
     --name owui-codebox-mcp \
     owui-codebox-mcp
   docker save owui-codebox-sandbox \
     | docker exec -i owui-codebox-mcp uv run --no-sync msb load
   ```

4. Connect OpenWebUI to `http://<host>:8000/mcp`.

Requests need a signed JWT with the user in the `id` claim. Use a TLS reverse
proxy outside a trusted network.

### Without Docker

For testing, the server runs directly with [uv](https://docs.astral.sh/uv/).
Its user must be able to read and write `/dev/kvm`; `uv run msb doctor` checks
that. The sandbox image is still built with Docker as above.

```bash
uv sync
docker save owui-codebox-sandbox | uv run msb load
uv run python main.py
```

### Private package index

Both images can be built against a private index such as Nexus. The sandbox
image takes it as build arguments:

```bash
docker build -f docker/Dockerfile.sandbox \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
```

The server image takes it from the `[tool.uv]` block in
[pyproject.toml](pyproject.toml): set `index` to the private index and, for
plain HTTP, add its host to `allow-insecure-host`. `uv lock` then keeps the
locked versions and rewrites their URLs in `uv.lock`, and the build installs
from there. Both files then differ from the repository; keep those changes
local. The `FROM` images of both Dockerfiles must be reachable as well.

### Prebuilt images

CI publishes both images to ghcr.io:

- `ghcr.io/th3r3alduk3/owui-codebox-mcp:latest` replaces the local server build.
- `ghcr.io/th3r3alduk3/owui-codebox-sandbox:latest` goes into `SANDBOX_IMAGE`.
  It is rebuilt monthly; `msb pull --force` fetches the update.

The server pulls a missing `SANDBOX_IMAGE` on the first tool call, which can
outlast the client's tool timeout, so pull it beforehand:

```bash
docker exec owui-codebox-mcp uv run --no-sync msb pull \
  ghcr.io/th3r3alduk3/owui-codebox-sandbox:latest
```

`SANDBOX_IMAGE` accepts any OCI reference. Registry credentials, plain-HTTP
registries and custom CAs go into `~/.microsandbox/config.json`, which lives
in the `owui-codebox-data` volume when the server runs in Docker.

## 🛠️ Tools

| Tool | Description |
|---|---|
| `run_python` | Run Python in a session microVM with optional packages, input/output files and code edits |
| `list_python_packages` | List packages already installed in the sandbox image |

```json
{
  "code": "import pandas as pd; pd.DataFrame({'a': [1, 2]}).to_csv('/sandbox/result.csv')",
  "output_files": ["/sandbox/result.csv"]
}
```

A follow-up call in the same session changes only what differs:

```json
{
  "session_id": "3f9a1c2e",
  "edits": [{"old": "[1, 2]", "new": "[1, 2, 3]"}],
  "output_files": ["/sandbox/result.csv"]
}
```

- `session_id`: from a previous result; reuses that microVM with its packages and files.
- `edits`: exact-text replacements applied to the session's code before the run.
- `libraries`: missing packages by name, optionally with versions/extras; compatible wheels required.
- `input_files`: OpenWebUI file IDs paired with paths under `/sandbox`.
- `output_files`: files to return from this call.

A session ends `SANDBOX_IDLE_TIMEOUT` seconds after its last call, or earlier
when a new run needs its slot. Sessions live in the server process and end
with it.

## ⚙️ Limits & security

[.env.example](.env.example) lists all settings: RAM, CPU, timeouts, file and
output limits, parallel sandboxes and per-user rate limits.
Keep `SANDBOX_MAX_DURATION` above `SANDBOX_EXEC_TIMEOUT`; it bounds the
lifetime of each microVM, so of every session and pip install.

- **Isolation:** every session boots its own microVM with its own kernel (KVM
  via libkrun), the restricted in-guest security profile and fixed RAM/vCPU
  caps. A session belongs to the user who started it. Idle sessions hold a
  sandbox slot until they end and give way when a new run needs one. A
  package install briefly adds a second microVM to its session.
- **Network:** scripts run in a microVM whose network policy denies all traffic
  from boot. `libraries` are installed by a separate, online microVM
  from prebuilt wheels only (`--only-binary=:all:`) and handed over through a
  read-only mount.
- **Disk:** a microVM can write 4 GiB to its own disk, and `libraries` can take
  4 GiB in the server's `/var/tmp` (microsandbox defaults). Both are removed
  when the session ends, leftovers of an unclean shutdown at the next start.
- **Files:** transfers are cut off at the size limit, never buffered beyond it.
  Only regular files under `/sandbox` come back; directories and paths outside
  it are rejected.
- **Errors:** tool errors never carry exception text, so no URLs, hosts or stack
  traces leak into the chat.
- **Output:** stdout/stderr are read as a stream. A run that prints more than
  `SANDBOX_MAX_FILE_SIZE` bytes is killed; the last `SANDBOX_MAX_OUTPUT`
  characters of each stream are returned.
- **Host access:** the server needs `/dev/kvm` only.
