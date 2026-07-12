from asyncio import Lock, to_thread
from json import loads
from mimetypes import guess_type
from pathlib import Path

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
        "List the Python packages preinstalled in the Python sandbox image. "
        "Call this once before the first `run_python` call that needs "
        "third-party libraries and prefer preinstalled packages: they resolve "
        "instantly when named in run_python's `libraries`, anything else is "
        "downloaded at call time. The listing is cached, so only the first "
        "call starts a container."
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


@tool(
    name="run_python",
    tags={"python", "execute"},
    description=(
        "Execute self-contained Python in a fresh sandbox and return stdout, "
        "stderr and the exit code. The container is destroyed after each call, "
        "so nothing persists between calls. The working directory is "
        f"'{WORKDIR}' — read and write files there. Runs are killed after "
        f"{_settings.sandbox_exec_timeout:.0f}s; files are limited to "
        f"{_settings.sandbox_max_file_size:,} bytes each.\n\n"
        "List every non-stdlib import in `libraries` (e.g. ['pandas', "
        "'matplotlib']). Check `list_python_packages` first and prefer "
        "preinstalled packages — they resolve instantly; anything else is "
        "downloaded before the run.\n\n"
        "To use files the user attached, list them in `input_files`, each with "
        "its OpenWebUI file ID and the sandbox path the code reads it from. "
        "To return files the code produces, list their paths in "
        "`output_files` in the same call — otherwise they are lost when the "
        "container is destroyed.\n\n"
        "The sandbox is cut off the network before the code runs, so the code "
        "itself cannot download anything. Get packages via `libraries` "
        "(installed while the network is still up) and data via `input_files` — "
        "never via URLs in the code."
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
    input_files: list[InputFile] = Field(
        default_factory=list,
        description=(
            "Files the user attached, to copy into the sandbox before "
            "running. Each entry pairs an OpenWebUI file ID with the absolute "
            "sandbox path the code expects it at (e.g. '/sandbox/data.csv'). "
            "Leave empty if there are no input files; never invent an ID."
        ),
    ),
    output_files: list[str] = Field(
        default_factory=list,
        description=(
            "Paths of files the code writes that the user should receive "
            "(e.g. ['/sandbox/plot.png', '/sandbox/result.csv']). Set them in "
            "the same call that writes the files, or they are lost when the "
            "sandbox is destroyed. Leave empty when no files should be "
            "returned."
        ),
    ),
    token: AccessToken = CurrentAccessToken(),
    user_id: str = TokenClaim("id"),
) -> ExecResult:

    async with user_slot(user_id):

        async with open_sandbox(
            _settings.sandbox_image,
            # The venv exists only for pip installs and costs ~4s per call.
            skip_environment_setup=not libraries,
            # Nothing to install → never give the container a network.
            offline=not libraries,
        ) as sandbox:

            for input_file in input_files:

                try:
                    data = await download_file(
                        file_id=input_file.id,
                        token=token.token,
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
                        "Check that the path is a valid absolute file path."
                    ) from error

            if libraries:

                try:
                    await to_thread(sandbox.install, libraries)
                except Exception as error:
                    raise ToolError(
                        "Could not install the requested libraries. "
                        "Check the package names."
                    ) from error

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
                        data = await to_thread(copy_out, sandbox, output_file_path)
                    except Exception as error:
                        raise ToolError(
                            f"Could not read '{output_file_path}' from the sandbox. "
                            "Check that the code actually wrote the file to that path."
                        ) from error

                    if len(data) > _settings.sandbox_max_file_size:
                        raise ToolError(
                            f"Output file too large ({len(data):,} bytes). "
                            f"Limit is {_settings.sandbox_max_file_size:,} bytes."
                        )

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
        stdout=Text.from_ansi(output.stdout).plain,
        stderr=Text.from_ansi(output.stderr).plain,
        output_files=uploaded_files,
    )
