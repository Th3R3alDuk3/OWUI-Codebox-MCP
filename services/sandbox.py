from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from functools import cache
from posixpath import dirname, join, normpath
from urllib.parse import urlsplit
from uuid import uuid4

from fastmcp.exceptions import ToolError
from fastmcp.utilities.logging import get_logger
from microsandbox import (
    Action,
    FsEntryKind,
    Network,
    NetworkPolicy,
    Rule,
    Sandbox,
    SecurityProfile,
)
from microsandbox.types import DnsConfig

from config import get_settings

_settings = get_settings()
logger = get_logger(__name__)

WORK_DIR = "/sandbox"
# In the workdir for sibling imports; the name avoids input files.
CODE_FILE = f"{WORK_DIR}/__main__.py"


@cache
def _network() -> Network:

    url = urlsplit(_settings.sandbox_index_url)

    # Deny all but these; rebind protection would block a LAN index.
    return Network(
        policy=NetworkPolicy(default_ingress=Action.DENY, rules=(
            Rule.allow(destination="files.pythonhosted.org", port=443),
            Rule.allow(
                destination=url.hostname or "",
                port=url.port or (443 if url.scheme == "https" else 80),
            ),
        )),
        dns=DnsConfig(rebind_protection=False),
    )


@asynccontextmanager
async def boot_sandbox() -> AsyncGenerator[Sandbox]:

    name = f"owui-codebox-{uuid4().hex[:8]}"

    try:
        sandbox = await Sandbox.create(
            name,
            image=_settings.sandbox_image,
            memory=_settings.sandbox_memory,
            cpus=_settings.sandbox_cpus,
            workdir=WORK_DIR,
            security=SecurityProfile.RESTRICTED,
            max_duration=_settings.sandbox_max_duration,
            ephemeral=True,
            env={
                "UV_DEFAULT_INDEX": _settings.sandbox_index_url,
                "UV_INSECURE_HOST": _settings.sandbox_insecure_host,
            },
            network=_network(),
        )
    except Exception as error:
        logger.exception("sandbox %s: start failed", name)
        # A failed start leaves a stopped sandbox record behind.
        with suppress(Exception):
            await Sandbox.remove(name)
        raise ToolError(
            "Could not start the sandbox. Try again later."
        ) from error

    async with sandbox:
        yield sandbox


async def write_file(
    sandbox: Sandbox,
    file_path: str,
    data: bytes,
) -> None:

    sandbox_path = normpath(join(WORK_DIR, file_path))

    if not sandbox_path.startswith(f"{WORK_DIR}/") \
        or sandbox_path == CODE_FILE:
        raise ValueError("path outside the sandbox workdir or the code file")

    # `fs.write` does not create missing parents; `fs.mkdir` does.
    await sandbox.fs.mkdir(dirname(sandbox_path))
    await sandbox.fs.write(sandbox_path, data)


async def read_file(
    sandbox: Sandbox,
    file_path: str,
    max_size: int,
) -> bytes:

    sandbox_path = normpath(join(WORK_DIR, file_path))

    if not sandbox_path.startswith(f"{WORK_DIR}/"):
        raise ValueError("path outside the sandbox workdir")

    # Follows symlinks. Reading a FIFO would block forever.
    metadata = await sandbox.fs.stat(sandbox_path)

    if metadata.kind != FsEntryKind.FILE:
        raise ValueError("not a regular file")

    data = bytearray()

    async for chunk in await sandbox.fs.read_stream(sandbox_path):
        data += chunk
        # One byte over is enough to reject it.
        if len(data) > max_size:
            raise ValueError("output file exceeds max_size")

    return bytes(data)
