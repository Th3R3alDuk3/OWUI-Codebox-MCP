from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from llm_sandbox import SandboxBackend, SandboxSession
from llm_sandbox.core.session_base import BaseSession


def open_sandbox(
    backend: str,
    image: str | None,
    environment: dict[str, str],
    max_memory: str,
    max_cpus: float,
    timeout: float,
) -> BaseSession:

    sandbox = SandboxSession(
        backend=SandboxBackend(backend),
        lang="python",
        image=image,
        workdir="/sandbox",
        runtime_configs={
            "name": f"sandbox-{uuid4().hex[:8]}",
            "environment": environment,
            "mem_limit": max_memory,
            "memswap_limit": max_memory,
            "nano_cpus": int(max_cpus * 1_000_000_000),
            "pids_limit": 512,
            "cap_drop": ["ALL"],
            "cap_add": ["DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges"],
        },
        execution_timeout=timeout,
        session_timeout=timeout,
        verbose=False,
    )
    sandbox.open()
    return sandbox


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

    path = Path(file_path)

    if not path.is_absolute():
        path = Path(sandbox.config.workdir).joinpath(path)

    with NamedTemporaryFile(delete=True) as tmp_file:
        sandbox.copy_from_runtime(path.as_posix(), tmp_file.name)
        return Path(tmp_file.name).read_bytes()
