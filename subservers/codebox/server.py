from asyncio import Semaphore
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

from config import get_settings
from models.sandbox import ExecResult, OutputFile
from services.owui import DOWNLOAD_FILE_URL, download_file, upload_file
from subservers.codebox._utils import (
    copy_into,
    copy_out,
    open_box,
    run_code,
)


_settings = get_settings()
_slots = Semaphore(_settings.max_concurrent)

mcp = FastMCP(name="codebox")


@mcp.tool(
    name="run_python",
    description=(
        "Execute Python in a fresh, isolated sandbox container and return "
        "stdout, stderr and the exit code. A new container is created for this "
        "call and destroyed as soon as it returns, so NOTHING persists between "
        "calls — send a complete, self-contained script every time.\n\n"
        "IMPORTANT – the sandbox has NO pre-installed third-party packages. "
        "You MUST list every package the code imports via `libraries` "
        "(e.g. ['pandas', 'matplotlib']). Omitting required packages will "
        "cause an ImportError.\n\n"
        "Input file: set `input_file_id` to a file the user attached in "
        "OpenWebUI; it is placed in the working directory under its original "
        "name, so the code can open it by that filename.\n\n"
        "Returning a file: to give the user a file your code produces (CSV, "
        "image, plot, PDF, …), set `output_file_path` to that file's path in "
        "this same call. The container is destroyed the moment the call "
        "returns, so a file not named in `output_file_path` cannot be retrieved "
        "afterwards — there is no separate save step. Only the named file is "
        "uploaded back to OpenWebUI with a download URL."
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

    async with _slots, open_box(
        backend=_settings.container_backend,
        image=_settings.sandbox_image,
        environment=_settings.pip_environment,
        max_memory=_settings.sandbox_max_memory,
        timeout=_settings.exec_timeout_seconds,
    ) as box:

        if input_file_id:
            await _download_input(box, input_file_id, token.token)

        start = monotonic()

        try:
            output: ConsoleOutput = await run_code(
                box,
                code,
                libraries,
                _settings.exec_timeout_seconds,
            )
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
                box, output_file_path, token.token
            )

    return ExecResult(
        exit_code=output.exit_code,
        stdout=output.stdout,
        stderr=output.stderr,
        duration_ms=duration_ms,
        output_file=output_file,
    )


async def _download_input(
    box: BaseSession,
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
        raise ToolError(str(error)) from error

    if len(data) > _settings.max_file_size_bytes:
        raise ToolError(
            f"Input file too large ({len(data):,} bytes). "
            f"Limit is {_settings.max_file_size_bytes:,} bytes."
        )

    try:
        await copy_into(box, file_name, data)
    except Exception as error:
        raise ToolError(
            f"Could not copy '{file_name}' into the sandbox: "
            f"{type(error).__name__}: {error}."
        ) from error


async def _upload_output(
    box: BaseSession,
    file_path: str,
    token: str,
) -> OutputFile:

    try:
        data = await copy_out(box, file_path)
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
        raise ToolError(str(error)) from error

    return OutputFile(
        file_name=file_name,
        file_size=len(data),
        download_url=DOWNLOAD_FILE_URL.format(
            base_url=_settings.owui_base_url, file_id=uploaded.id),
    )
