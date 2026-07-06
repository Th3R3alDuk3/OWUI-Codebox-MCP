# OWUI-Codebox-MCP

> Disposable code sandboxes via MCP for OpenWebUI. One tool per language, container-isolated, stateless.

---

## 🚀 Setup

Requires **Docker** (daemon running) or **Podman**.

```bash
uv sync
cp .env.example .env
```

Set the important values in `.env`:

- `JWT_SECRET` → OpenWebUI's `WEBUI_SECRET_KEY`
- `OWUI_BASE_URL` → OpenWebUI URL reachable from this server, e.g. `http://localhost:3000`
- `OWUI_VERIFY_TLS` → set `false` only for self-signed or plain-HTTP lab setups
- `CONTAINER_BACKEND` → `docker` or `podman`

Language-specific settings (sandbox image, package index, …) are covered per
language under [Languages](#-languages).

## ▶️ Run

```bash
uv run python main.py
```

The server listens on `0.0.0.0:8000`. Point OpenWebUI's MCP/tools config at
`http://<host>:8000/mcp`. Requests must carry a JWT signed by `JWT_SECRET`
with the OpenWebUI user's `id` claim.

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

Config is read from your `.env` via `--env-file`; to expose a different port,
change the mapping, e.g. `-p 9000:8000`. Prebuilt images are published to
**ghcr.io** on pushes to `main` and on version tags.

## 🧩 Languages

Every language follows the same pattern, so new ones (Go, Rust, …) plug in the same way:

| Piece | Convention | Python example |
| --- | --- | --- |
| MCP tools | `tools/<lang>.py` | `tools/python.py` |
| Sandbox image | `SANDBOX_IMAGE_<LANG>` in `.env` | `SANDBOX_IMAGE_PYTHON` |
| Sandbox env vars (JSON) | `SANDBOX_ENV_<LANG>` in `.env` | `SANDBOX_ENV_PYTHON` |
| Prebuilt image (optional) | `sandbox.<lang>.Dockerfile` | `sandbox.python.Dockerfile` |

Execution semantics are identical for all languages: each call gets a fresh,
hardened container that is removed right after the call finishes (see
[Sandboxing & limits](#-sandboxing--limits)).

Currently supported: **Python**.

### 🐍 Python

**Tools**

- `run_python` executes a complete Python script in a fresh sandbox. It can
  install requested packages, read one attached OpenWebUI file, and upload one
  generated output file.
- `list_python_packages` lists packages already present in the configured
  sandbox image, so models can avoid reinstalling what is already available.

**Sandbox image**

Default is the official `python:3.13-trixie` image — pull it once before the
first run:

```bash
docker pull python:3.13-trixie
```

For faster sandboxes, `sandbox.python.Dockerfile` extends it with common
code-interpreter packages (numpy, pandas, matplotlib, scikit-learn, openpyxl,
pymupdf, weasyprint, opencv, …) and fonts for HTML→PDF:

```bash
docker build -f sandbox.python.Dockerfile -t owui-codebox-sandbox .
```

Then set this in `.env`:

```env
SANDBOX_IMAGE_PYTHON=owui-codebox-sandbox
```

**Private package index**

Set pip's env vars via `SANDBOX_ENV_PYTHON` in `.env` — the dict is injected
into every `run_python` sandbox:

```env
SANDBOX_ENV_PYTHON={"PIP_INDEX_URL": "https://nexus.example.com/repository/pypi/simple", "PIP_TRUSTED_HOST": "nexus.example.com"}
```

For the prebuilt image, pass them as build args too:

```bash
docker build -f sandbox.python.Dockerfile \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
```

Runtime values from `SANDBOX_ENV_PYTHON` still take precedence.

## 🔒 Sandboxing & limits

- The container is the isolation boundary. Each sandbox runs hardened by
  default: all Linux capabilities dropped (only `DAC_OVERRIDE` kept, which
  llm-sandbox needs), `no-new-privileges`, a `pids` limit (fork-bomb guard),
  no swap (so `SANDBOX_MAX_MEMORY` is a hard ceiling) and a CPU cap
  (`SANDBOX_MAX_CPUS`, e.g. `1` = one core). Tighten further as needed
  (a locked-down sandbox image, gVisor, …).
- **Known gap — network**: the sandbox has full outbound network access (it
  needs it for package installs), so executed code can reach the internet and
  your LAN — including OpenWebUI itself. Restrict egress with a dedicated
  Docker network or firewall rules if that matters in your environment.
- **Known gap — disk**: there is no per-sandbox disk quota. Docker's
  `storage_opt size` requires overlay2 on xfs with `pquota`; on other backing
  filesystems (ext4, btrfs) a run can fill the disk under `/var/lib/docker`
  until the timeout tears the container down. Monitor free space, or move
  Docker's data-root to xfs to get a real quota.
- Every call pays the container start (and, the first time, image pull) cost.
- `SANDBOX_EXEC_TIMEOUT` (seconds) caps a single call; on timeout the container is torn
  down and the call returns an error.
- `MAX_CONCURRENT_SANDBOXES` caps concurrent sandbox containers server-wide,
  `MAX_CONCURRENT_SANDBOXES_PER_USER` per OpenWebUI user; calls past a cap are
  rejected with a capacity error.
- Requests are rate-limited per user (token bucket: `RATE_LIMIT_RPS` sustained,
  `RATE_LIMIT_BURST` burst), keyed on the JWT's `id` claim — mainly to dampen
  retry storms at capacity.
