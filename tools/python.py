from asyncio import Lock, to_thread
from json import loads
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
    InstalledPackage,
    OutputFile,
    PackageListing,
)
from services.owui import download_file, upload_file
from tools._sandbox import copy_into, copy_out, open_sandbox, user_slot

_settings = get_settings()

# The sandbox image is fixed for the process lifetime (settings are cached),
# so one listing serves every call after the first.
_packages_lock = Lock()
_packages_cache: PackageListing | None = None


@tool(
    name="run_python",
    tags={"python", "execute"},
    description=(
        "Execute self-contained Python in a fresh sandbox and return stdout, "
        "stderr and the exit code. The container is destroyed after each call, "
        "so nothing persists between calls.\n\n"
        "List every non-stdlib import in `libraries` (e.g. ['pandas', "
        "'matplotlib']). Packages preinstalled in the sandbox image (see "
        "the `list_python_packages` tool) resolve instantly; anything else is "
        "downloaded before the run.\n\n"
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

    async with user_slot(user_id):

        async with open_sandbox(
            "python", _settings.sandbox_image_python
        ) as sandbox:

            if input_file_id:

                try:
                    file_name, data = await download_file(
                        file_id=input_file_id,
                        token=token.token,
                    )
                except RuntimeError as error:
                    raise ToolError(
                        f"Could not fetch input file '{input_file_id}' "
                        f"from OpenWebUI: {error}"
                    ) from error

                if len(data) > _settings.sandbox_max_file_size:
                    raise ToolError(
                        f"Input file too large ({len(data):,} bytes). "
                        f"Limit is {_settings.sandbox_max_file_size:,} bytes."
                    )

                try:
                    await to_thread(copy_into, sandbox, file_name, data)
                except Exception as error:
                    raise ToolError(
                        f"Could not copy '{file_name}' into the sandbox: "
                        f"{type(error).__name__}: {error}."
                    ) from error

            try:
                output: ConsoleOutput = await to_thread(sandbox.run, code, libraries)
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
                    f"Sandbox execution failed: {error}"
                ) from error

            output_file = None

            if output_file_path and output.exit_code == 0:

                try:
                    data = await to_thread(copy_out, sandbox, output_file_path)
                except Exception as error:
                    raise ToolError(
                        f"Could not read '{output_file_path}' from the sandbox: "
                        f"{type(error).__name__}: {error}. "
                        "Check that the code actually wrote the file to that path."
                    ) from error

                if len(data) > _settings.sandbox_max_file_size:
                    raise ToolError(
                        f"Output file too large ({len(data):,} bytes). "
                        f"Limit is {_settings.sandbox_max_file_size:,} bytes."
                    )

                file_name = Path(output_file_path).name

                try:
                    uploaded = await upload_file(
                        file_name=file_name,
                        data=data,
                        content_type="application/octet-stream",
                        token=token.token,
                    )
                except RuntimeError as error:
                    raise ToolError(
                        f"Could not upload output file '{file_name}' "
                        f"to OpenWebUI: {error}"
                    ) from error

                output_file = OutputFile(
                    file_name=uploaded.file_name or file_name,
                    file_size=len(data),
                    download_url=uploaded.download_url,
                )

    return ExecResult(
        exit_code=output.exit_code,
        stdout=Text.from_ansi(output.stdout).plain,
        stderr=Text.from_ansi(output.stderr).plain,
        output_file=output_file,
    )


@tool(
    name="list_python_packages",
    tags={"python", "packages"},
    description=(
        "List the Python packages preinstalled in the Python sandbox image. "
        "Packages listed here resolve instantly when named in run_python's "
        "`libraries`; anything else is downloaded at call time. The listing "
        "is cached, so only the first call starts a container."
    ),
)
async def list_python_packages() -> PackageListing:

    global _packages_cache

    if _packages_cache is not None:
        return _packages_cache

    async with _packages_lock:

        if _packages_cache is None:

            try:
                async with open_sandbox(
                    "python", _settings.sandbox_image_python, skip_environment_setup=True
                ) as sandbox:
                    output = await to_thread(
                        sandbox.execute_commands,
                        ["pip list --format=json --disable-pip-version-check"],
                    )
            except Exception as error:
                raise ToolError(
                    f"Could not inspect sandbox image "
                    f"'{_settings.sandbox_image_python}': "
                    f"{type(error).__name__}: {error}"
                ) from error

            if output.exit_code != 0:
                raise ToolError(
                    f"Could not list packages: {output.stderr or output.stdout}"
                )

            try:
                entries = loads(output.stdout.strip())
            except ValueError as error:
                raise ToolError(
                    f"Unexpected output from pip list: {error}"
                ) from error

            _packages_cache = PackageListing(
                image=_settings.sandbox_image_python,
                packages=[
                    InstalledPackage(name=entry["name"], version=entry["version"])
                    for entry in entries
                ],
            )

    return _packages_cache

