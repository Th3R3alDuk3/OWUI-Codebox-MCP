from asyncio import to_thread
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from llm_sandbox import ConsoleOutput, SandboxBackend, SandboxSession, SecurityError
from llm_sandbox.core.session_base import BaseSession
from llm_sandbox.security import SecurityIssueSeverity, SecurityPolicy


SECURITY_POLICY = SecurityPolicy(
    severity_threshold=SecurityIssueSeverity.HIGH,
    patterns=[],
)


@asynccontextmanager
async def open_box(
    backend: str,
    image: str | None,
    environment: dict[str, str],
    max_memory: str,
    timeout: float,
) -> AsyncIterator[BaseSession]:

    def _open() -> BaseSession:
        box = SandboxSession(
            backend=SandboxBackend(backend),
            lang="python",
            image=image,
            security_policy=SECURITY_POLICY,
            runtime_configs={
                "name": f"sandbox-{uuid4().hex[:8]}",
                "environment": environment,
                "mem_limit": max_memory,
                # hardening
                "memswap_limit": max_memory,            # no swap: mem_limit is a hard ceiling
                "pids_limit": 512,                      # fork-bomb guard
                "cap_drop": ["ALL"],                    # drop every Linux capability …
                "cap_add": ["DAC_OVERRIDE"],            # … except the one llm-sandbox needs to read the injected code file
                "security_opt": ["no-new-privileges"],  # block setuid privilege escalation
            },
            execution_timeout=timeout,
            session_timeout=timeout,
            verbose=False,
        )
        box.open()
        return box

    box = await to_thread(_open)

    try:
        yield box
    finally:
        with suppress(Exception):
            await to_thread(box.close)


async def run_code(
    box: BaseSession,
    code: str,
    libraries: list[str],
    timeout: float,
) -> ConsoleOutput:

    safe, violations = box.is_safe(code)

    if not safe:
        raise SecurityError(f"Code rejected by the safety policy: {violations}.")

    return await to_thread(box.run, code, libraries, timeout)


async def copy_into(
    box: BaseSession,
    file_name: str,
    data: bytes,
) -> None:

    file_name = Path(file_name).name
    file_path = Path(box.config.workdir).joinpath(file_name)

    def _copy() -> None:
        with NamedTemporaryFile(delete=True) as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            box.copy_to_runtime(tmp_file.name, file_path.as_posix())

    await to_thread(_copy)


async def copy_out(
    box: BaseSession,
    file_path: str,
) -> bytes:

    file_path = Path(file_path)

    if not file_path.is_absolute():
        file_path = Path(box.config.workdir).joinpath(file_path)

    def _copy() -> bytes:
        with NamedTemporaryFile(delete=True) as tmp_file:
            box.copy_from_runtime(file_path.as_posix(), tmp_file.name)
            return Path(tmp_file.name).read_bytes()

    return await to_thread(_copy)
