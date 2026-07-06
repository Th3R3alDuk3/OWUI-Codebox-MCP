# OWUI-Codebox-MCP

> Disposable Python sandbox via MCP for OpenWebUI. One tool, container-isolated, stateless.

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
- `SANDBOX_IMAGE_PYTHON` → optional custom Python sandbox image; blank uses
  llm-sandbox's default
- `PIP_INDEX_URL` / `PIP_TRUSTED_HOST` → optional private package index settings
  injected into the sandbox

## ▶️ Run

```bash
docker pull python:3.13-trixie
uv run python main.py
```

The server runs on `HOST:PORT` from `.env`. Point OpenWebUI's MCP/tools config
at `http://<host>:<port>/mcp`. Requests must carry a JWT signed by `JWT_SECRET`
with the OpenWebUI user's `id` claim.

### Faster sandboxes: prebuilt image

`sandbox.python.Dockerfile` extends `python:3.13-trixie` with common
code-interpreter packages (numpy, pandas, matplotlib, scikit-learn, openpyxl,
pymupdf, weasyprint, opencv, …) and fonts for HTML→PDF.

```bash
docker build -f sandbox.python.Dockerfile -t owui-codebox-sandbox .
```

Then set this in `.env`:

```env
SANDBOX_IMAGE_PYTHON=owui-codebox-sandbox
```

For a private package index, pass build args:

```bash
docker build -f sandbox.python.Dockerfile \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
```

Runtime `PIP_*` values from `.env` still take precedence.

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

## 🛠️ Tools

Tools are grouped by language. The current Python tools are:

- `run_python` executes a complete Python script in a fresh sandbox. It can
  install requested packages, read one attached OpenWebUI file, and upload one
  generated output file.
- `list_python_packages` lists packages already present in the configured
  sandbox image, so models can avoid reinstalling what is already available.

Each execution call is stateless: the container is created for that call and
removed right after it finishes. Tool parameters and response fields are
published through the MCP schema.

## 🔒 Notes

- The container is the isolation boundary. Each sandbox runs hardened by
  default: all Linux capabilities dropped (only `DAC_OVERRIDE` kept, which
  llm-sandbox needs), `no-new-privileges`, a `pids` limit (fork-bomb guard),
  no swap (so `SANDBOX_MAX_MEMORY` is a hard ceiling) and a CPU cap
  (`SANDBOX_MAX_CPUS`, e.g. `1` = one core). Tighten further as needed
  (a locked-down `SANDBOX_IMAGE_PYTHON`, gVisor, …).
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
- `SANDBOX_EXEC_TIMEOUT` (seconds) caps a single call; on timeout the container is torn
  down and the call returns an error.
- `MAX_CONCURRENT_SANDBOXES` caps how many sandbox containers may run concurrently; calls
  past the cap are rejected with a capacity error. `MAX_CONCURRENT_SANDBOXES_PER_USER`
  additionally caps concurrent sandboxes per OpenWebUI user, so one user cannot
  occupy all slots. No manual teardown needed — containers never outlive the
  call that created them.
- Requests are rate-limited per user (token bucket: `RATE_LIMIT_RPS` sustained,
  `RATE_LIMIT_BURST` burst), keyed on the `id` claim of the OpenWebUI JWT. This
  mainly dampens retry storms when the server is at capacity.
- Each container is named `sandbox-<random>` (a short random suffix), so
  concurrent calls never collide on a name. The name is freed when the container
  is removed at the end of the call.
