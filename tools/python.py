from asyncio import Lock, timeout
from contextlib import nullcontext
from mimetypes import guess_type
from pathlib import Path
from tempfile import TemporaryDirectory

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
    ExecResult,
    InputFile,
    InstalledPackage,
    OutputFile,
    PackageListing,
)
from services.owui import download_file, upload_file
from tools._python.sandbox import (
    WORKDIR,
    boot_sandbox,
    read_file,
    server_slot,
    user_slot,
    write_file,
)

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

            try:
                async with server_slot(), boot_sandbox() as sandbox:
                    output = await sandbox.exec(
                        "pip",
                        ["list", "--format=json", "--disable-pip-version-check"],
                    )
            except ToolError:
                raise
            except Exception as error:
                raise ToolError(
                    f"Could not inspect sandbox image "
                    f"'{_settings.sandbox_image}'."
                ) from error

            if output.exit_code != 0:
                details = output.stderr_bytes or output.stdout_bytes
                raise ToolError(
                    f"Could not list packages: {details.decode(errors='replace')}")

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
        "Execute self-contained Python in a fresh microVM and return stdout, "
        "stderr and the exit code. Nothing persists between calls; the working "
        f"directory is '{WORKDIR}'. Runs are killed after "
        f"{_settings.sandbox_exec_timeout:.0f}s and only the last "
        f"{_settings.sandbox_max_output:,} characters of output are returned, "
        "so print summaries, not whole datasets.\n\n"
        "The sandbox is offline while the code runs: get packages via "
        "`libraries` and data via `input_files`, never by downloading in the "
        "code. Files the code writes are lost unless listed in `output_files` "
        "in the same call."
    ),
)
async def run_python(
    code: str = Field(
        description="Self-contained Python source to execute.",
    ),
    libraries: list[str] = Field(
        default_factory=list,
        max_length=_settings.sandbox_max_libraries,
        description=(
            "Only packages missing from the sandbox image; check "
            "list_python_packages first. Use package names with optional "
            "versions or extras. Only compatible prebuilt wheels are accepted; "
            "source builds, URLs, local paths and pip options are not supported."
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
            f"e.g. ['{WORKDIR}/plot.png']."
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

    async with user_slot(user_id), server_slot():

        # `pip install --user` target for both VMs; on disk, /tmp is often RAM.
        with (
            TemporaryDirectory(dir="/var/tmp", ignore_cleanup_errors=True)
            if libraries else nullcontext()
        ) as host_libs_dir:

            if libraries:

                async with boot_sandbox(
                    online=True,
                    host_libs_dir=host_libs_dir,
                ) as installer:

                    try:
                        installed = await installer.exec(
                            "pip",
                            [
                                "install", "--user", "--only-binary=:all:",
                                "--", *libraries,
                            ],
                        )
                    except Exception as error:
                        raise ToolError(
                            "Could not run pip in the sandbox."
                        ) from error

                if installed.exit_code != 0:
                    raise ToolError(
                        "Could not install the requested libraries. "
                        "Check the package names and versions. Each package and "
                        "its dependencies must have a compatible prebuilt wheel "
                        "for the sandbox's Python version and platform; source "
                        "builds are disabled."
                    )

            async with boot_sandbox(host_libs_dir=host_libs_dir) as sandbox:

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
                        await write_file(sandbox, input_file.path, data)
                    except Exception as error:
                        raise ToolError(
                            f"Could not copy '{input_file.path}' into the sandbox. "
                            f"The path must be a file under {WORKDIR}."
                        ) from error

                # In the workdir for sibling imports; the name avoids input files.
                code_path = f"{WORKDIR}/__main__.py"

                stdout, stderr = bytearray(), bytearray()
                # Stays -1 when the run is cut short for printing too much.
                exit_code = -1

                try:
                    await sandbox.fs.write(code_path, code.encode())

                    # `exec_stream` ignores its own `timeout`.
                    async with timeout(_settings.sandbox_exec_timeout):

                        # Empty stdin makes `input()` fail at once.
                        run = await sandbox.exec_stream(
                            "python",
                            [code_path],
                            stdin=b"",
                        )

                        # The SDK queues unread output without bound, so a
                        # flood cannot be drained: leaving the VM kills it.
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

                except TimeoutError as error:
                    raise ToolError(
                        f"Execution timed out after "
                        f"{_settings.sandbox_exec_timeout:.0f}s. "
                        "Each call runs in a fresh microVM with no state carried "
                        "over, so splitting across calls does not help — make the code "
                        "faster or do less work so it finishes within the limit."
                    ) from error
                except Exception as error:
                    raise ToolError(
                        "Sandbox execution failed unexpectedly."
                    ) from error

                uploaded_files: list[OutputFile] = []

                if exit_code == 0:

                    for output_file_path in output_files:

                        try:
                            data = await read_file(
                                sandbox,
                                output_file_path,
                                _settings.sandbox_max_file_size,
                            )
                        except ValueError as error:
                            raise ToolError(
                                f"'{output_file_path}' cannot be returned. It must be "
                                f"a regular file under {WORKDIR}, at most "
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
    )
