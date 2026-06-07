from pathlib import Path
from tempfile import NamedTemporaryFile

from llm_sandbox import InteractiveSandboxSession, SandboxBackend


def open_box(
    backend: str,
    image: str | None,
    max_memory: str,
    max_lifetime: int,
) -> InteractiveSandboxSession:

    box = InteractiveSandboxSession(
        backend=SandboxBackend(backend),
        lang="python",
        image=image,
        max_memory=max_memory,
        session_timeout=max_lifetime or None,  # 0 -> no hard cap
        verbose=False,
    )

    box.open()
    return box


def copy_into(
    box: InteractiveSandboxSession,
    file_path: str,
    data: bytes,
) -> None:

    with NamedTemporaryFile(delete=True) as tmp_file:
        tmp_file.write(data)
        tmp_file.flush()
        box.copy_to_runtime(tmp_file.name, file_path)


def copy_out(
    box: InteractiveSandboxSession,
    file_path: str,
) -> bytes:

    with NamedTemporaryFile(delete=True) as tmp_file:
        box.copy_from_runtime(file_path, tmp_file.name)
        return Path(tmp_file.name).read_bytes()
