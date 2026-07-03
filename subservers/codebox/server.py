from asyncio import Semaphore, to_thread
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import monotonic

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentAccessToken
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from llm_sandbox import ConsoleOutput
from llm_sandbox.core.session_base import BaseSession
from llm_sandbox.exceptions import SandboxTimeoutError
from pydantic import Field
from rich.text import Text

from config import get_settings
from models.sandbox import ExecResult, OutputFile
from services.owui import DOWNLOAD_FILE_URL, download_file, upload_file
from subservers.codebox._sandbox import copy_into, copy_out, open_sandbox


_settings = get_settings()
_slots = Semaphore(_settings.max_concurrent_sandboxes)

mcp = FastMCP(name="codebox")


@asynccontextmanager
async def _open_sandbox() -> AsyncIterator[BaseSession]:

    sandbox = await to_thread(
        open_sandbox,
        _settings.container_backend,
        _settings.sandbox_image or None,
        _settings.pip_environment,
        _settings.sandbox_max_memory,
        _settings.sandbox_max_cpus,
        _settings.exec_timeout_seconds,
    )

    try:
        yield sandbox
    finally:
        with suppress(Exception):
            await to_thread(sandbox.close)


@mcp.tool(
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
) -> ExecResult:

    if _slots.locked():
        raise ToolError("Server at capacity. Try again later.")

    async with _slots, _open_sandbox() as sandbox:

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
            base_url=_settings.owui_base_url,
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
            base_url=_settings.owui_base_url,
        )
    except RuntimeError as error:
        raise ToolError(
            f"Could not upload output file '{file_name}' to OpenWebUI: {error}"
        ) from error

    return OutputFile(
        file_name=file_name,
        file_size=len(data),
        download_url=DOWNLOAD_FILE_URL.format(
            base_url=_settings.owui_base_url, file_id=uploaded.id),
    )
