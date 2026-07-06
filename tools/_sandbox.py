from asyncio import Semaphore, to_thread
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from uuid import uuid4

from fastmcp.exceptions import ToolError
from llm_sandbox import SandboxBackend, SandboxSession
from llm_sandbox.core.session_base import BaseSession

from config import get_settings

_settings = get_settings()

# Languages llm-sandbox ships handlers for.
Lang = Literal["python", "java", "javascript", "cpp", "go", "ruby", "r"]

# Server-wide and per-user concurrency limits, shared by every language tool.
_server_slots = Semaphore(_settings.max_concurrent_sandboxes)
_user_slots: Counter[str] = Counter()


@asynccontextmanager
async def open_sandbox(
    lang: Lang,
    image: str | None = None,
    environment: dict[str, str] | None = None,
    skip_environment_setup: bool = False,
) -> AsyncIterator[BaseSession]:

    if _server_slots.locked():
        raise ToolError("Server at capacity. Try again later.")

    async with _server_slots:

        sandbox = await to_thread(
            _open_sandbox, lang, image, environment, skip_environment_setup)

        try:
            yield sandbox
        finally:
            with suppress(Exception):
                await to_thread(sandbox.close)


def _open_sandbox(
    lang: Lang,
    image: str | None = None,
    environment: dict[str, str] | None = None,
    skip_environment_setup: bool = False,
) -> BaseSession:

    sandbox = SandboxSession(
        skip_environment_setup=skip_environment_setup,
        backend=SandboxBackend(_settings.container_backend),
        lang=lang,
        image=image or None,
        workdir="/sandbox",
        runtime_configs={
            "name": f"sandbox-{uuid4().hex[:8]}",
            "environment": environment or {},
            "mem_limit": _settings.sandbox_max_memory,
            "memswap_limit": _settings.sandbox_max_memory,
            "nano_cpus": int(_settings.sandbox_max_cpus * 1_000_000_000),
            "pids_limit": 512,
            "cap_drop": ["ALL"],
            "cap_add": ["DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges"],
        },
        execution_timeout=_settings.sandbox_exec_timeout,
        session_timeout=_settings.sandbox_exec_timeout,
        verbose=False,
    )

    sandbox.open()
    return sandbox


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


def copy_into(
    sandbox: BaseSession,
    file_name: str,
    data: bytes,
) -> None:

    file_name = Path(file_name).name
    file_path = Path(sandbox.config.workdir).joinpath(file_name)

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
