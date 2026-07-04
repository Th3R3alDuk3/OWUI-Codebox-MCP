from asyncio import to_thread
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from llm_sandbox import SandboxBackend, SandboxSession
from llm_sandbox.core.session_base import BaseSession

from config import get_settings

_settings = get_settings()


def _open_sandbox() -> BaseSession:

    sandbox = SandboxSession(
        backend=SandboxBackend(_settings.container_backend),
        lang="python",
        image=_settings.sandbox_image or None,
        workdir="/sandbox",
        runtime_configs={
            "name": f"sandbox-{uuid4().hex[:8]}",
            "environment": _settings.pip_environment,
            "mem_limit": _settings.sandbox_max_memory,
            "memswap_limit": _settings.sandbox_max_memory,
            "nano_cpus": int(_settings.sandbox_max_cpus * 1_000_000_000),
            "pids_limit": 512,
            "cap_drop": ["ALL"],
            "cap_add": ["DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges"],
        },
        execution_timeout=_settings.exec_timeout_seconds,
        session_timeout=_settings.exec_timeout_seconds,
        verbose=False,
    )

    sandbox.open()
    return sandbox


@asynccontextmanager
async def open_sandbox() -> AsyncIterator[BaseSession]:

    sandbox = await to_thread(_open_sandbox)

    try:
        yield sandbox
    finally:
        with suppress(Exception):
            await to_thread(sandbox.close)


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
