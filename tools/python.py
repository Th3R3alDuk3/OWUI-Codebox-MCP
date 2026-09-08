from asyncio import Lock, to_thread
from json import loads
from mimetypes import guess_type
from pathlib import Path
from shlex import quote

from fastmcp.dependencies import CurrentAccessToken, TokenClaim
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.tools import tool
from llm_sandbox import ConsoleOutput
from llm_sandbox.exceptions import SandboxTimeoutError
from pydantic import Field
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
from tools._sandbox import (
    WORKDIR,
    copy_into,
    copy_out,
    isolate_network,
    open_sandbox,
    user_slot,
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
                async with open_sandbox(
                    _settings.sandbox_image,
                    skip_environment_setup=True,
                ) as sandbox:
                    output = await to_thread(
                        sandbox.execute_commands,
                        ["pip list --format=json --disable-pip-version-check"],
                    )
            except ToolError:
                raise
            except Exception as error:
                raise ToolError(
                    f"Could not inspect sandbox image "
                    f"'{_settings.sandbox_image}'."
                ) from error

            if output.exit_code != 0:
                raise ToolError(
                    f"Could not list packages: {output.stderr or output.stdout}")

            try:
                entries = loads(output.stdout.strip())
            except ValueError as error:
                raise ToolError(
                    "Unexpected output from pip list."
                ) from error

            _packages_cache = PackageListing(
                image=_settings.sandbox_image,
                packages=[
                    InstalledPackage(name=entry["name"], version=entry["version"])
                    for entry in entries
                ],
            )

    return _packages_cache


def _clip(
    stream: str,
) -> str:

    plain = Text.from_ansi(stream).plain
    limit = _settings.sandbox_max_output

    if len(plain) <= limit:
        return plain

    return f"{plain[:limit]}\n[truncated: {len(plain):,} of {limit:,} characters]"


@tool(
    name="run_python",
    tags={"python", "execute"},
    description=(
        "Execute self-contained Python in a fresh container and return stdout, "
        "stderr and the exit code. Nothing persists between calls; the working "
        f"directory is '{WORKDIR}'. Runs are killed after "
        f"{_settings.sandbox_exec_timeout:.0f}s and output is truncated past "
        f"{_settings.sandbox_max_output:,} characters, so print summaries, not "
        "whole datasets.\n\n"
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
            "Packages to pip-install first. Required for every non-stdlib "
            "import, e.g. ['numpy', 'pandas']."
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

    async with (
        user_slot(user_id),
        open_sandbox(
            _settings.sandbox_image,
            # The venv exists only for pip installs and costs ~4s per call.
            skip_environment_setup=not libraries,
            offline=not libraries,
        ) as sandbox,
    ):

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
                await to_thread(copy_into, sandbox, input_file.path, data)
            except Exception as error:
                raise ToolError(
                    f"Could not copy '{input_file.path}' into the sandbox. "
                    f"The path must be a file under {WORKDIR}."
                ) from error

        if libraries:

            # sandbox.install() swallows pip failures, so pip runs directly.
            try:
                installed = await to_thread(
                    sandbox.execute_commands,
                    [
                        f"{sandbox.pip_executable_path} install "
                        + " ".join(quote(library) for library in libraries)
                    ],
                    WORKDIR,
                )
            except Exception as error:
                raise ToolError(
                    "Could not run pip in the sandbox."
                ) from error

            if installed.exit_code != 0:
                raise ToolError(
                    "Could not install the requested libraries. "
                    "Check the package names."
                )

            try:
                await to_thread(isolate_network, sandbox)
            except Exception as error:
                raise ToolError(
                    "Could not cut the sandbox off the network; "
                    "refusing to run the code."
                ) from error

        try:
            output: ConsoleOutput = await to_thread(sandbox.run, code)
        except SandboxTimeoutError as error:
            raise ToolError(
                f"Execution timed out after "
                f"{_settings.sandbox_exec_timeout:.0f}s. "
                "Each call runs in a fresh container with no state carried "
                "over, so splitting across calls does not help — make the code "
                "faster or do less work so it finishes within the limit."
            ) from error
        except Exception as error:
            raise ToolError(
                "Sandbox execution failed unexpectedly."
            ) from error

        # Timeout and OOM kill with exit 137, no output, no exception.
        if output.exit_code == 137:
            raise ToolError(
                "The run was killed by a resource limit — either the "
                f"{_settings.sandbox_exec_timeout:.0f}s execution timeout "
                f"or the {_settings.sandbox_max_memory} memory cap. "
                "Make the code faster or use less memory."
            )

        uploaded_files: list[OutputFile] = []

        if output.exit_code == 0:

            for output_file_path in output_files:

                try:
                    data = await to_thread(
                        copy_out,
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
                        f"Could not read '{output_file_path}' from the sandbox. "
                        "Check that the code actually wrote the file to that path."
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
        exit_code=output.exit_code,
        stdout=_clip(output.stdout),
        stderr=_clip(output.stderr),
        output_files=uploaded_files,
    )
