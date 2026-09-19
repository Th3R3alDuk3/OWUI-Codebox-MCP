from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from os import getpid
from posixpath import dirname, join, normpath
from uuid import uuid4

from fastmcp.exceptions import ToolError
from microsandbox import (
    FsEntryKind,
    Network,
    NetworkProfile,
    Sandbox,
    SecurityProfile,
    Volume,
)

from config import get_settings

_settings = get_settings()

WORK_DIR = "/sandbox"
LIBS_DIR = "/libs"
# In the workdir for sibling imports; the name avoids input files.
CODE_FILE = f"{WORK_DIR}/__main__.py"
# Names this server's microVMs and libs dirs; a restarted container reuses the PID.
INSTANCE = f"owui-codebox-{getpid()}"


@asynccontextmanager
async def boot_sandbox(
    online: bool = False,
    host_libs_dir: str | None = None,
) -> AsyncGenerator[Sandbox]:

    name = f"{INSTANCE}-{uuid4().hex[:8]}"

    # A running microVM cannot change its network, so installs get their own VM.
    network = Network.none()
    volumes = {}

    if online:
        # PRIVATE also unblocks DNS answers pointing at a LAN package index.
        network = Network.from_profiles(
            NetworkProfile.PUBLIC,
            NetworkProfile.PRIVATE,
        )

    if host_libs_dir:
        volumes[LIBS_DIR] = Volume.bind(host_libs_dir, readonly=not online)

    try:
        sandbox = await Sandbox.create(
            name,
            image=_settings.sandbox_image,
            memory=_settings.sandbox_max_memory,
            cpus=_settings.sandbox_max_cpus,
            workdir=WORK_DIR,
            security=SecurityProfile.RESTRICTED,
            max_duration=_settings.sandbox_max_duration,
            ephemeral=True,
            # pip and Python pick up `pip install --user` packages from here.
            env={"PYTHONUSERBASE": LIBS_DIR},
            volumes=volumes,
            network=network,
        )
    except Exception as error:
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
