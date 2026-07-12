from asyncio import Semaphore, to_thread
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fastmcp.exceptions import ToolError
from llm_sandbox import SandboxBackend, SandboxSession
from llm_sandbox.core.session_base import BaseSession

from config import get_settings

_settings = get_settings()

WORKDIR = "/sandbox"

_server_slots = Semaphore(_settings.max_concurrent_sandboxes)
_user_slots: Counter[str] = Counter()


@asynccontextmanager
async def user_slot(
    user_id: str,
) -> AsyncIterator[None]:

    if _user_slots[user_id] >= _settings.max_concurrent_sandboxes_per_user:
        raise ToolError(
            f"You already have {_settings.max_concurrent_sandboxes_per_user} "
            "sandboxes running. Wait for one to finish and try again."
        )

    _user_slots[user_id] += 1

    try:
        yield
    finally:

        _user_slots[user_id] -= 1

        if not _user_slots[user_id]:
            del _user_slots[user_id]


@asynccontextmanager
async def open_sandbox(
    image: str,
    skip_environment_setup: bool = False,
    offline: bool = False,
) -> AsyncIterator[BaseSession]:

    if _server_slots.locked():
        raise ToolError("Server at capacity. Try again later.")

    async with _server_slots:

        try:
            sandbox = await to_thread(
                _open_sandbox, image, skip_environment_setup, offline)
        except Exception as error:
            raise ToolError(
                "Could not start the sandbox container. Try again later."
            ) from error

        try:
            yield sandbox
        finally:
            with suppress(Exception):
                await to_thread(sandbox.close)


def _open_sandbox(
    image: str,
    skip_environment_setup: bool = False,
    offline: bool = False,
) -> BaseSession:

    runtime_configs = {
        "name": f"sandbox-{uuid4().hex[:8]}",
        # The keep-alive ignores SIGTERM; SIGKILL skips Docker's 10s grace.
        "stop_signal": "SIGKILL",
        "mem_limit": _settings.sandbox_max_memory,
        "memswap_limit": _settings.sandbox_max_memory,
        "nano_cpus": int(_settings.sandbox_max_cpus * 1_000_000_000),
        "pids_limit": 512,
        "cap_drop": ["ALL"],
        "cap_add": ["DAC_OVERRIDE"],
        "security_opt": ["no-new-privileges"],
    }

    if offline:
        runtime_configs["network_mode"] = "none"

    sandbox = SandboxSession(
        skip_environment_setup=skip_environment_setup,
        backend=SandboxBackend.DOCKER,
        lang="python",
        image=image,
        workdir=WORKDIR,
        runtime_configs=runtime_configs,
        execution_timeout=_settings.sandbox_exec_timeout,
        session_timeout=_settings.sandbox_exec_timeout,
        verbose=False,
    )

    sandbox.open()
    return sandbox


def isolate_network(
    sandbox: BaseSession,
) -> None:
    # Runs after `install`, so package repos stay reachable for `libraries`.
    # Fails closed: raises unless the container ends up with no network.

    sandbox.container.reload()
    networks = sandbox.container.attrs["NetworkSettings"]["Networks"]

    if not networks:
        raise RuntimeError("container has no network to disconnect")

    for network_name in networks:
        sandbox.client.networks.get(network_name).disconnect(sandbox.container)

    sandbox.container.reload()
    remaining = sandbox.container.attrs["NetworkSettings"]["Networks"]

    if remaining:
        raise RuntimeError(f"network still attached: {', '.join(remaining)}")


def copy_into(
    sandbox: BaseSession,
    file_path: str,
    data: bytes,
) -> None:

    file_path = Path(file_path)

    if not file_path.is_absolute():
        file_path = Path(sandbox.config.workdir).joinpath(file_path)

    with NamedTemporaryFile(delete=True) as tmp_file:

        tmp_file.write(data)
        tmp_file.flush()

        sandbox.copy_to_runtime(tmp_file.name, file_path.as_posix())


def copy_out(
    sandbox: BaseSession,
    file_path: str,
) -> bytes:

    file_path = Path(file_path)

    if not file_path.is_absolute():
        file_path = Path(sandbox.config.workdir).joinpath(file_path)

    with NamedTemporaryFile(delete=True) as tmp_file:
        sandbox.copy_from_runtime(file_path.as_posix(), tmp_file.name)
        return Path(tmp_file.name).read_bytes()
