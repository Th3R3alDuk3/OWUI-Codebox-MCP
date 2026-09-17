from asyncio import Semaphore
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from posixpath import dirname, join, normpath
from uuid import uuid4

from fastmcp.exceptions import ToolError
from microsandbox import (
    Network,
    NetworkProfile,
    Sandbox,
    SecurityProfile,
    Volume,
)

from config import get_settings

_settings = get_settings()

WORKDIR = "/sandbox"
LIBSDIR = "/libs"

_server_slots = Semaphore(_settings.max_concurrent_sandboxes)
_user_slots: defaultdict[str, Semaphore] = defaultdict(
    lambda: Semaphore(_settings.max_concurrent_sandboxes_per_user)
)


@asynccontextmanager
async def user_slot(
    user_id: str,
) -> AsyncGenerator[None]:

    # The acquire fast path never suspends, so this cannot race the acquire.
    if _user_slots[user_id].locked():
        raise ToolError(
            "Concurrent run limit reached "
            f"({_settings.max_concurrent_sandboxes_per_user} per user). "
            "Wait for a run to finish and try again."
        )

    async with _user_slots[user_id]:
        yield


@asynccontextmanager
async def server_slot() -> AsyncGenerator[None]:

    if _server_slots.locked():
        raise ToolError("Server at capacity. Try again later.")

    async with _server_slots:
        yield


@asynccontextmanager
async def boot_sandbox(
    online: bool = False,
    host_libs_dir: str | None = None,
) -> AsyncGenerator[Sandbox]:

    name = f"sandbox-{uuid4().hex[:8]}"

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
        volumes[LIBSDIR] = Volume.bind(host_libs_dir, readonly=not online)

    try:
        sandbox = await Sandbox.create(
            name,
            image=_settings.sandbox_image,
            memory=_settings.sandbox_max_memory,
            cpus=_settings.sandbox_max_cpus,
            workdir=WORKDIR,
            security=SecurityProfile.RESTRICTED,
            max_duration=_settings.sandbox_max_duration,
            ephemeral=True,
            # pip and Python pick up `pip install --user` packages from here.
            env={"PYTHONUSERBASE": LIBSDIR},
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

    sandbox_path = normpath(join(WORKDIR, file_path))

    if not sandbox_path.startswith(f"{WORKDIR}/"):
        raise ValueError("path outside the sandbox workdir")

    # `fs.write` does not create missing parents; `fs.mkdir` does.
    await sandbox.fs.mkdir(dirname(sandbox_path))
    await sandbox.fs.write(sandbox_path, data)


async def read_file(
    sandbox: Sandbox,
    file_path: str,
    max_size: int,
) -> bytes:

    sandbox_path = normpath(join(WORKDIR, file_path))

    if not sandbox_path.startswith(f"{WORKDIR}/"):
        raise ValueError("path outside the sandbox workdir")

    # `fs.stat` follows symlinks, coreutils `stat` does not.
    probe = await sandbox.exec("stat", ["-c", "%F", "--", sandbox_path])

    if probe.exit_code != 0:
        raise FileNotFoundError(sandbox_path)

    # Also matches "regular empty file".
    if not probe.stdout_text.startswith("regular"):
        raise ValueError("not a regular file")

    data = bytearray()

    async for chunk in await sandbox.fs.read_stream(sandbox_path):
        data += chunk
        # One byte over is enough to reject it.
        if len(data) > max_size:
            raise ValueError("output file exceeds max_size")

    return bytes(data)
