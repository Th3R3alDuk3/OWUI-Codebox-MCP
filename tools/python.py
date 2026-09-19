from asyncio import Lock, timeout
from mimetypes import guess_type
from pathlib import Path

from fastmcp.dependencies import CurrentAccessToken, TokenClaim
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.tools import tool
from microsandbox import ExecEventType
from packaging.requirements import InvalidRequirement, Requirement
from pydantic import Field, TypeAdapter
from rich.text import Text

from config import get_settings
from models.sandbox import (
    Edit,
    ExecResult,
    InputFile,
    InstalledPackage,
    OutputFile,
    PackageListing,
)
from services.owui import download_file, upload_file
from services.sandbox import CODE_FILE, WORK_DIR, boot_sandbox, read_file, write_file
from services.session import open_session, server_slot, use_session

_settings = get_settings()


# The image is fixed for the process lifetime, so one listing serves all calls.
_packages_lock = Lock()
_packages_cache: PackageListing | None = None


@tool(
    name="list_python_packages",
    tags={"python", "packages"},
    description=(
        "List the packages preinstalled in the sandbox image. Call this before "
        "using run_python's `libraries`: preinstalled packages resolve "
        "instantly, anything else is downloaded at call time."
    ),
)
async def list_python_packages() -> PackageListing:

    global _packages_cache

    async with _packages_lock:

        if _packages_cache is None:

            async with server_slot(), boot_sandbox() as sandbox:
                output = await sandbox.exec(
                    "pip",
                    ["list", "--format=json", "--disable-pip-version-check"],
                )

            try:
                packages = TypeAdapter(list[InstalledPackage]).validate_json(
                    output.stdout_bytes)
            except ValueError as error:
                raise ToolError(
                    "Unexpected output from pip list."
                ) from error

            _packages_cache = PackageListing(
                image=_settings.sandbox_image,
                packages=packages,
            )

    return _packages_cache


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
    tags={"python", "execute"},
    description=(
        "Run Python in an isolated microVM and return stdout, stderr and the "
        f"exit code. The working directory is '{WORK_DIR}'. Runs are killed "
        f"after {_settings.sandbox_exec_timeout:.0f}s and only the last "
        f"{_settings.sandbox_max_output:,} characters of output are returned, "
        "so print summaries, not whole datasets.\n\n"
        "The sandbox is offline: get packages via `libraries` and data via "
        "`input_files`, never by downloading in the code. Files the code "
        "writes come back only when listed in `output_files`.\n\n"
        "Each result carries a `session_id`. Pass it to keep the same microVM "
        "with its packages and files, and fix the code with `edits` instead "
        "of resending it. A session ends after "
        f"{_settings.sandbox_idle_timeout:.0f}s without calls; a call without "
        "`session_id` may replace an idle one."
    ),
)
async def run_python(
    code: str = Field(
        default="",
        description="Python source to run. Omit to rerun the session's code.",
    ),
    session_id: str = Field(
        default="",
        description="From a previous result. Omit to start a new session.",
    ),
    edits: list[Edit] = Field(
        default_factory=list,
        description="Text replacements applied to the session's code before the run.",
    ),
    libraries: list[str] = Field(
        default_factory=list,
        max_length=_settings.sandbox_max_libraries,
        description=(
            "Only packages missing from the sandbox image; check "
            "list_python_packages first. They stay installed for the session. "
            "Use package names with optional versions or extras. Only "
            "compatible prebuilt wheels are accepted; source builds, URLs, "
            "local paths and pip options are not supported."
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
        description=(
            "Paths of files the code writes that the user should receive, "
            f"e.g. ['{WORK_DIR}/plot.png']."
        ),
    ),
    token: AccessToken = CurrentAccessToken(),
    user_id: str = TokenClaim("id"),
) -> ExecResult:

    # Explicit source URLs/paths and pip options could bypass wheel-only installs.
    for library in libraries:
        try:
            requirement = Requirement(library)
        except InvalidRequirement as error:
            raise ToolError(
                "Libraries must be package names with optional versions or extras. "
                "URLs, local paths and pip options are not supported."
            ) from error
        if requirement.url is not None:
            raise ToolError("Library URLs are not supported; use package names.")

    if not session_id:
        session_id = await open_session(user_id)

    async with use_session(session_id, user_id) as session:

        if not code and not await session.sandbox.fs.exists(CODE_FILE):
            raise ToolError("Pass code; the session has none yet.")

        if code:
            await session.sandbox.fs.write(CODE_FILE, code.encode())

        if libraries:

            async with boot_sandbox(
                online=True,
                host_libs_dir=session.libs_dir,
            ) as installer:

                installed = await installer.exec("pip", [
                    "install", "--user", "--only-binary=:all:",
                    "--", *libraries
                ])

            if installed.exit_code != 0:
                raise ToolError(
                    "Could not install the requested libraries. "
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
                    f"The path must be a file under {WORK_DIR} other than "
                    f"{CODE_FILE}."
                ) from error

        # Applied last: a failed install or input file must not consume them.
        if edits:

            text = (await read_file(
                session.sandbox,
                CODE_FILE,
                _settings.sandbox_max_file_size,
            )).decode()

            for index, edit in enumerate(edits):
                found = text.count(edit.old)
                if found != 1:
                    raise ToolError(
                        f"edits[{index}].old must occur exactly once in the "
                        f"code, found {found} times."
                    )
                text = text.replace(edit.old, edit.new)

            await session.sandbox.fs.write(CODE_FILE, text.encode())

        stdout, stderr = bytearray(), bytearray()
        # Stays -1 when the run is cut short for printing too much.
        exit_code = -1

        try:
            # `exec_stream` ignores its own `timeout`.
            async with timeout(_settings.sandbox_exec_timeout):

                # Empty stdin makes `input()` fail at once.
                run = await session.sandbox.exec_stream(
                    "python",
                    [CODE_FILE],
                    stdin=b"",
                )

                try:
                    # The SDK queues unread output without bound; the kill ends a flood.
                    async for event in run:

                        if event.event_type == ExecEventType.STDOUT:
                            stdout += event.data or b""
                        elif event.event_type == ExecEventType.STDERR:
                            stderr += event.data or b""
                        elif event.code is not None:
                            exit_code = event.code

                        printed = len(stdout) + len(stderr)

                        if printed > _settings.sandbox_max_file_size:
                            break
                finally:
                    # Ends a run that timed out or printed too much; no-op after exit.
                    await run.kill()

        except TimeoutError as error:
            raise ToolError(
                f"Execution timed out after "
                f"{_settings.sandbox_exec_timeout:.0f}s and was killed. Make "
                "the code faster, or split the work across calls and keep "
                "intermediate results in files."
            ) from error

        uploaded_files: list[OutputFile] = []

        if exit_code == 0:

            for output_file_path in output_files:

                try:
                    data = await read_file(
                        session.sandbox,
                        output_file_path,
                        _settings.sandbox_max_file_size,
                    )
                except ValueError as error:
                    raise ToolError(
                        f"'{output_file_path}' cannot be returned. It must be "
                        f"a regular file under {WORK_DIR}, at most "
                        f"{_settings.sandbox_max_file_size:,} bytes."
                    ) from error
                except Exception as error:
                    raise ToolError(
                        f"Could not read '{output_file_path}' from the "
                        "sandbox. Check that the code actually wrote the "
                        "file to that path."
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

    return ExecResult(
        exit_code=exit_code,
        stdout=_clip(stdout),
        stderr=_clip(stderr),
        output_files=uploaded_files,
        session_id=session_id,
    )
