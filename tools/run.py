from asyncio import Semaphore, to_thread
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic

from fastmcp.dependencies import CurrentAccessToken, TokenClaim
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.tools import tool
from llm_sandbox import ConsoleOutput
from llm_sandbox.core.session_base import BaseSession
from llm_sandbox.exceptions import SandboxTimeoutError
from pydantic import Field
from rich.text import Text

from config import get_settings
from models.sandbox import ExecResult, OutputFile
from services.owui import download_file, upload_file
from tools._sandbox import copy_into, copy_out, open_sandbox

_settings = get_settings()
_server_slots = Semaphore(_settings.max_concurrent_sandboxes)
_user_slots: Counter[str] = Counter()


@contextmanager
def _user_slot(
    user_id: str,
) -> Iterator[None]:

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


@tool(
    name="run_python",
    description=(
        "Execute self-contained Python in a fresh sandbox and return stdout, "
        "stderr and the exit code. The container is destroyed after each call, "
        "so nothing persists between calls.\n\n"
        "No third-party packages are pre-installed: list every non-stdlib "
        "import in `libraries` (e.g. ['pandas', 'matplotlib']).\n\n"
        "Set `input_file_id` to an OpenWebUI file the user attached to use it "
        "as input. To return a file the code produces, set `output_file_path` "
        "to its path in the same call — otherwise it is lost when the container "
        "is destroyed."
    ),
)
async def run_python(
    code: str = Field(
        description="Self-contained Python source to execute in the sandbox.",
    ),
    libraries: list[str] = Field(
        default_factory=list,
        description=(
            "Packages to pip-install before running the code. "
            "REQUIRED for any non-stdlib import (e.g. ['numpy', 'pandas']). "
            "Do not leave this empty when the code uses third-party libraries."
        ),
    ),
    input_file_id: str = Field(
        default="",
        description=(
            "OpenWebUI file ID of a file the user attached, to copy into the "
            "sandbox before running. The file lands in the working directory "
            "under its original name. Leave empty if there is no input file; "
            "never invent an ID."
        ),
    ),
    output_file_path: str = Field(
        default="",
        description=(
            "Path of a file the code writes that the user should receive "
            "(e.g. 'result.csv' or '/sandbox/plot.png'). Set it in the same "
            "call that writes the file, or it is lost when the sandbox is "
            "destroyed. Leave empty when no file should be returned."
        ),
    ),
    token: AccessToken = CurrentAccessToken(),
    user_id: str = TokenClaim("id"),
) -> ExecResult:

    if _server_slots.locked():
        raise ToolError("Server at capacity. Try again later.")

    with _user_slot(user_id):

        async with _server_slots, open_sandbox() as sandbox:

            if input_file_id:
                await _download_input(sandbox, input_file_id, token.token)

            start = monotonic()

            try:
                output: ConsoleOutput = await to_thread(sandbox.run, code, libraries)
            except SandboxTimeoutError as error:
                raise ToolError(
                    f"Execution timed out after "
                    f"{_settings.exec_timeout_seconds:.0f}s. "
                    "Each call runs in a fresh container with no state carried "
                    "over, so splitting across calls does not help — make the code "
                    "faster or do less work so it finishes within the limit."
                ) from error
            except Exception as error:
                raise ToolError(
                    f"Sandbox execution failed: {error}"
                ) from error

            duration_ms = int((monotonic() - start) * 1000)

            output_file = None

            if output_file_path and output.exit_code == 0:
                output_file = await _upload_output(
                    sandbox, output_file_path, token.token
                )

    return ExecResult(
        exit_code=output.exit_code,
        stdout=Text.from_ansi(output.stdout).plain,
        stderr=Text.from_ansi(output.stderr).plain,
        duration_ms=duration_ms,
        output_file=output_file,
    )


async def _download_input(
    sandbox: BaseSession,
    file_id: str,
    token: str,
) -> None:

    try:
        file_name, data = await download_file(
            file_id=file_id,
            token=token,
        )
    except RuntimeError as error:
        raise ToolError(
            f"Could not fetch input file '{file_id}' from OpenWebUI: {error}"
        ) from error

    if len(data) > _settings.max_file_size_bytes:
        raise ToolError(
            f"Input file too large ({len(data):,} bytes). "
            f"Limit is {_settings.max_file_size_bytes:,} bytes."
        )

    try:
        await to_thread(copy_into, sandbox, file_name, data)
    except Exception as error:
        raise ToolError(
            f"Could not copy '{file_name}' into the sandbox: "
            f"{type(error).__name__}: {error}."
        ) from error


async def _upload_output(
    sandbox: BaseSession,
    file_path: str,
    token: str,
) -> OutputFile:

    try:
        data = await to_thread(copy_out, sandbox, file_path)
    except Exception as error:
        raise ToolError(
            f"Could not read '{file_path}' from the sandbox: "
            f"{type(error).__name__}: {error}. "
            "Check that the code actually wrote the file to that path."
        ) from error

    if len(data) > _settings.max_file_size_bytes:
        raise ToolError(
            f"Output file too large ({len(data):,} bytes). "
            f"Limit is {_settings.max_file_size_bytes:,} bytes."
        )

    file_name = Path(file_path).name

    try:
        uploaded = await upload_file(
            file_name=file_name,
            data=data,
            content_type="application/octet-stream",
            token=token,
        )
    except RuntimeError as error:
        raise ToolError(
            f"Could not upload output file '{file_name}' to OpenWebUI: {error}"
        ) from error

    return OutputFile(
        file_name=uploaded.file_name or file_name,
        file_size=len(data),
        download_url=uploaded.download_url,
    )
