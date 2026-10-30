# OWUI-Codebox-MCP

[![App image](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/app.yml/badge.svg)](https://github.com/Th3R3alDuk3/OWUI-Codebox-MCP/actions/workflows/app.yml)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Th3R3alDuk3/OWUI-Codebox-MCP)](LICENSE)

> Isolated Python sandboxes for OpenWebUI via MCP.

- **Notebook sessions:** Python runs as cells in a Jupyter kernel; variables,
  packages and files carry over.
- **Isolated:** each user gets a microVM with its own kernel, built on
  [microsandbox](https://github.com/superradcompany/microsandbox).
- **Files:** OpenWebUI attachments go in, produced files come back as download
  links.
- **Closed network:** a microVM reaches only the package index.

## 🚀 Setup

Requires a Linux host with **KVM** and Docker.

1. Build the images. [Dockerfile.sandbox](docker/Dockerfile.sandbox) lists the
   preinstalled packages; they are unpinned, so a rebuild updates them.

   ```bash
   docker build -f docker/Dockerfile.sandbox -t owui-codebox-sandbox .
   docker build -f docker/Dockerfile.app -t owui-codebox-mcp .
   ```

2. Create the configuration, then set `JWT_SECRET` to OpenWebUI's
   `WEBUI_SECRET_KEY` and `OWUI_BASE_URL` to an OpenWebUI URL this server can
   reach.

   ```bash
   cp .env.example .env
   ```

3. Start the server and load the sandbox image into it. The volume keeps loaded
   images across restarts.

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

### Prebuilt images

CI publishes both images to ghcr.io. The sandbox image is rebuilt monthly.

| Image | Use |
|---|---|
| `ghcr.io/th3r3alduk3/owui-codebox-mcp:latest` | Replaces the local server build |
| `ghcr.io/th3r3alduk3/owui-codebox-sandbox:latest` | Goes into `SANDBOX_IMAGE` |

Pull the sandbox image beforehand, as a pull on the first tool call can outlast
the client's tool timeout. `--force` fetches updates.

```bash
docker exec owui-codebox-mcp uv run --no-sync msb pull \
  ghcr.io/th3r3alduk3/owui-codebox-sandbox:latest
```

`SANDBOX_IMAGE` accepts any OCI reference to an image with `uv` and
`ipykernel`. Registry credentials, plain-HTTP registries and custom CAs go into
`~/.microsandbox/config.json` in the `owui-codebox-data` volume.

### Own package index

`packages` are installed as prebuilt wheels from `SANDBOX_INDEX_URL`, by default
PyPI. For an index in the LAN such as Nexus or devpi:

| Setting | Value |
|---|---|
| `SANDBOX_INDEX_URL` | Index URL, by name and not by IP address |
| `SANDBOX_INSECURE_HOST` | Host name, if the index uses an own certificate |
| `SANDBOX_PROXY` | `IP:port`, if the index is only reachable through an HTTP proxy |

> [!WARNING]
> Code in the sandbox can reach the index too, so it must not accept anonymous
> uploads.

A proxy receives `http://` and `https://` targets as CONNECT by IP address and
has to allow that address and port. Names must still resolve on the server, and
image pulls bypass the proxy.

Build the sandbox image against the same index. Behind a proxy, add
`--build-arg HTTPS_PROXY=http://<ip>:<port>`.

```bash
docker build -f docker/Dockerfile.sandbox \
  --build-arg UV_DEFAULT_INDEX=https://nexus.example.com/repository/pypi/simple \
  --build-arg UV_INSECURE_HOST=nexus.example.com \
  -t owui-codebox-sandbox .
```

For the server image, set the index in the `[tool.uv]` block of
[pyproject.toml](pyproject.toml) and run `uv lock`.

## 🛠️ Tools

| Tool | Description |
|---|---|
| `run_python` | Run Python as notebook cells in the user's microVM with optional packages, input/output files and image viewing |
| `list_python_packages` | List packages already installed in the sandbox image |

Each user has one session: a microVM with its kernel, packages and files. It
ends `SANDBOX_IDLE_TIMEOUT` seconds after its last call, after
`SANDBOX_MAX_DURATION` at the latest, or with the server. The first result of a
new session carries `new_session: true`, so the model knows earlier state is
gone.

`view_images` shows the model up to three images and needs a vision model.
Images stay in the chat's context and are sent again with every later message,
so the tool description reserves this for exceptions.

## 🔒 Security & limits

[.env.example](.env.example) lists all settings.

- **Access:** requests need a JWT signed with `JWT_SECRET`, with the user in the
  `id` claim. Use a TLS reverse proxy outside a trusted network.
- **Isolation:** every session boots its own microVM with its own Linux kernel
  (KVM via libkrun), the restricted in-guest security profile and fixed
  RAM/vCPU caps. Plan for up to `MAX_CONCURRENT_SANDBOXES` × `SANDBOX_MEMORY`
  of RAM.
- **Network:** a microVM's policy allows the host and port of
  `SANDBOX_INDEX_URL` and `files.pythonhosted.org`, both by name, and denies
  everything else, name resolution included. HTTPS is intercepted, so an
  allowed host cannot be asked for another site.
- **Disk:** a microVM can write 4 GiB to its own disk (microsandbox default),
  `packages` included; it is removed when the session ends.
- **Files:** only regular files under `/sandbox` come back.

microsandbox keeps `/dev/kvm` out of the microVM. As a second layer, turn nested
virtualization off on the host (`kvm_amd` on AMD), so its code stays out of
reach if that ever changes:

```bash
echo "options kvm_intel nested=0" | sudo tee /etc/modprobe.d/kvm-nested.conf
sudo modprobe -r kvm_intel && sudo modprobe kvm_intel
```
