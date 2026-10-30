from asyncio import Lock, timeout
from contextlib import suppress
from mimetypes import guess_type
from pathlib import Path
from time import monotonic

from fastmcp.dependencies import CurrentAccessToken, TokenClaim
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.tools import ToolResult, tool
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import Image
from mcp.types import TextContent
from microsandbox import ExecEventType, JobError
from pydantic import Field, TypeAdapter
from rich.text import Text

from config import get_settings
from models.python import (
    InputFile,
    InstalledPackage,
    OutputFile,
    PackageList,
    RunResult,
)
from services.owui import download_file, upload_file
from services.sandbox import (
    KERNEL_CLIENT,
    KERNEL_CONNECTION_FILE,
    WORK_DIR,
    boot_sandbox,
    read_file,
    write_file,
)
from services.session import server_slot, use_session

_settings = get_settings()
logger = get_logger(__name__)


# The image is fixed for the process lifetime, so one listing serves all calls.
_packages_lock = Lock()
_packages_cache: list[InstalledPackage] | None = None


@tool(
    name="list_python_packages",
    description="List the packages preinstalled in the sandbox image.",
)
async def list_python_packages() -> PackageList:

    global _packages_cache

    async with _packages_lock:

        if _packages_cache is None:

            async with server_slot(), boot_sandbox() as sandbox:
                output = await sandbox.exec("uv", [
                    "pip", "list", "--format=json"
                ])

            _packages_cache = TypeAdapter(
                list[InstalledPackage]
            ).validate_json(output.stdout_bytes)

    return PackageList(packages=_packages_cache)


def _clip(
    stream: bytearray,
) -> str:

    limit = _settings.sandbox_max_output
    # UTF-8 needs at most 4 bytes per character; the rest is never decoded.
    tail = stream[-4 * limit:]
    plain = Text.from_ansi(tail.decode(errors="replace")).plain

    if len(tail) == len(stream) and len(plain) <= limit:
        return plain

    return f"[truncated: output exceeds {limit:,} characters]\n{plain[-limit:]}"


@tool(
    name="run_python",
    description=(
        "Run Python in a Jupyter kernel inside an isolated microVM and return "
        "stdout, stderr and the exit code. The working directory is "
        f"'{WORK_DIR}'. Runs are killed after "
        f"{_settings.sandbox_exec_timeout:.0f}s and only the last "
        f"{_settings.sandbox_max_output:,} characters of each stream are returned, "
        "so print summaries, not whole datasets. Exit code -1 means the run "
        "printed too much and the kernel restarted, losing its variables.\n\n"
        "The sandbox reaches only its package index: packages come from "
        "`packages` and data from `input_files`, never by downloading in the "
        "code. Files the code writes reach the user only when listed in "
        "`output_files`, in the same or a later call; pass on their "
        "`download_url`.\n\n"
        "Each call runs `code` as the next cell in the user's kernel; "
        "variables, imports, packages and files carry over until it has been "
        f"idle for {_settings.sandbox_idle_timeout:.0f}s, and for at most "
        f"{_settings.sandbox_max_duration:.0f}s in total. A result with "
        "`new_session` true started without them, so redo what the task "
        "still needs.\n\n"
        "Viewing images is expensive and they stay in the chat's context, so "
        "`view_images` is the exception: use it only when the user asks or "
        "the task is impossible without seeing an image, and list as few as "
        "possible. Do not use it to check your own plots or to show images to "
        "the user; `output_files` delivers those."
    ),
    # A ToolResult return carries no schema of its own.
    output_schema=RunResult.model_json_schema(),
)
async def run_python(
    code: str = Field(
        description="Python code; the value of its last expression is printed.",
    ),
    packages: list[str] = Field(
        default_factory=list,
        description=(
            "Requirement specifiers to install; prebuilt wheels only, "
            "preinstalled ones are skipped."
        ),
    ),
    input_files: list[InputFile] = Field(
        default_factory=list,
        max_length=_settings.sandbox_max_files,
        description=(
            "Files the user attached, copied in before the run. "
            "Never invent an ID."
        ),
    ),
    output_files: list[str] = Field(
        default_factory=list,
        max_length=_settings.sandbox_max_files,
        description=f"Paths of files for the user, e.g. ['{WORK_DIR}/plot.png'].",
    ),
    view_images: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Images the code wrote that you must see yourself; PNG, JPEG, GIF "
            "or WebP up to 5 MB."
        ),
    ),
    token: AccessToken = CurrentAccessToken(),
    user_id: str = TokenClaim("id"),
) -> ToolResult:

    async with use_session(user_id) as session:

        kernel = session.kernel

        # Boots while packages and input files arrive.
        if kernel is None or (await kernel.inspect()).state != "running":
            kernel = session.kernel = await session.sandbox.exec_detached("python", [
                "-m", "ipykernel_launcher", "-f", KERNEL_CONNECTION_FILE,
                "--InteractiveShell.colors=nocolor",
                "--InteractiveShell.xmode=Plain",
                # `Out` would keep results in RAM after `del`.
                "--InteractiveShell.cache_size=0",
            ], stdin=b"")

        if packages:

            installed = await session.sandbox.exec("uv", [
                "pip", "install", "--only-binary=:all:", "--", *packages
            ])

            if installed.exit_code != 0:
                logger.warning("session %s: package install failed\n%s",
                    user_id, installed.stderr_text.strip())
                raise ToolError(
                    "Could not install the requested packages. "
                    "Check the package names and versions. Each package and "
                    "its dependencies must have a compatible prebuilt wheel "
                    "for the sandbox's Python version and platform; source "
                    "builds are disabled."
                )

        for input_file in input_files:

            try:
                data = await download_file(
                    file_id=input_file.id,
                    token=token.token,
                    max_bytes=_settings.sandbox_max_file_size,
                )
            except RuntimeError as error:
                raise ToolError(
                    f"Could not fetch input file '{input_file.id}' "
                    "from OpenWebUI. Check that the ID belongs to a file "
                    "the user actually attached."
                ) from error

            if len(data) > _settings.sandbox_max_file_size:
                raise ToolError(
                    f"Input file too large ({len(data):,} bytes). "
                    f"Limit is {_settings.sandbox_max_file_size:,} bytes."
                )

            try:
                await write_file(session.sandbox, input_file.path, data)
            except Exception as error:
                raise ToolError(
                    f"Could not copy '{input_file.path}' into the sandbox. "
                    f"The path must be a file under {WORK_DIR}."
                ) from error

        stdout, stderr = bytearray(), bytearray()
        # Stays -1 when the run is cut short for printing too much.
        exit_code = -1
        exited = False
        started = monotonic()

        try:
            # The SDK `timeout` ends in a plain exit event, like a crash.
            async with timeout(_settings.sandbox_exec_timeout):

                kernel_pid = (await kernel.inspect()).pid

                # Isolated, so files in the workdir cannot shadow its imports.
                run = await session.sandbox.exec_stream("python", [
                    "-I", "-c", KERNEL_CLIENT, KERNEL_CONNECTION_FILE, str(kernel_pid)
                ], stdin=code.encode())

                try:
                    # Caps the total output; the kill ends a flood.
                    async for event in run:

                        if event.event_type == ExecEventType.STDOUT:
                            stdout += event.data or b""
                        elif event.event_type == ExecEventType.STDERR:
                            stderr += event.data or b""
                        elif event.code is not None:
                            exit_code = event.code
                            exited = True

                        printed = len(stdout) + len(stderr)

                        if printed > _settings.sandbox_max_file_size:
                            break
                finally:
                    # Ends a timed-out or flooding run; after exit, `kill` raises.
                    if not exited:
                        await run.kill()
                        # It may still run the cell; the next call starts a new one.
                        with suppress(JobError):
                            await kernel.kill()
                        session.kernel = None

        except TimeoutError as error:
            raise ToolError(
                f"Execution timed out after "
                f"{_settings.sandbox_exec_timeout:.0f}s; the kernel restarts, "
                "so variables are lost, files remain. Make the code faster, "
                "or split the work across calls."
            ) from error

        logger.info("session %s: exit %d after %.1fs",
            user_id, exit_code, monotonic() - started)

        uploaded_files: list[OutputFile] = []
        images: list[Image] = []

        if exit_code == 0:

            for output_file_path in output_files:

                try:
                    data = await read_file(
                        session.sandbox,
                        output_file_path,
                        _settings.sandbox_max_file_size,
                    )
                except Exception as error:
                    raise ToolError(
                        f"'{output_file_path}' cannot be returned. It must be "
                        f"a regular file under {WORK_DIR} that the code wrote, "
                        f"at most {_settings.sandbox_max_file_size:,} bytes."
                    ) from error

                file_name = Path(output_file_path).name

                try:
                    download_url = await upload_file(
                        file_name=file_name,
                        data=data,
                        content_type=(
                            guess_type(file_name)[0]
                            or "application/octet-stream"
                        ),
                        token=token.token,
                    )
                except RuntimeError as error:
                    raise ToolError(
                        f"Could not upload output file '{file_name}' "
                        "to OpenWebUI."
                    ) from error

                uploaded_files.append(OutputFile(
                    name=file_name,
                    size=len(data),
                    download_url=download_url,
                ))

            for image_path in view_images:

                image_format = Path(image_path).suffix[1:].lower()

                try:
                    if image_format not in ("png", "jpg", "jpeg", "gif", "webp"):
                        raise ValueError("not an image format models accept")
                    # Model APIs reject larger images.
                    data = await read_file(session.sandbox, image_path, 5 * 2**20)
                except Exception as error:
                    raise ToolError(
                        f"'{image_path}' cannot be viewed. It must be a PNG, "
                        f"JPEG, GIF or WebP file under {WORK_DIR} that the code "
                        "wrote, at most 5 MB."
                    ) from error

                images.append(Image(data=data, format=image_format))

    result = RunResult(
        exit_code=exit_code,
        stdout=_clip(stdout),
        stderr=_clip(stderr),
        output_files=uploaded_files,
        new_session=session.new,
    )
    session.new = False

    # One JSON text block, so clients do not append the structured copy again.
    return ToolResult(
        content=[TextContent(type="text", text=result.model_dump_json()), *images],
        structured_content=result,
    )
