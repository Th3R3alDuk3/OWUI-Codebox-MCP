from asyncio import Lock, to_thread
from pathlib import Path
from time import monotonic, time

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentAccessToken, Depends, TokenClaim
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from llm_sandbox import ConsoleOutput
from llm_sandbox.exceptions import SandboxTimeoutError
from pydantic import Field

from config import get_settings
from models.sandbox import ExecResult, FileResponse, Session, SessionInfo
from services.owui import DOWNLOAD_FILE_URL, download_file, upload_file
from subservers._store import SessionStore
from subservers.codebox._utils import (
    copy_into,
    copy_out,
    open_box,
)


_settings = get_settings()


#


_store = SessionStore(
    max_size=_settings.max_sessions,
    idle_timeout=_settings.session_idle_timeout_seconds,
    sweep_interval=_settings.session_sweep_interval_seconds,
)

lifespan = _store.lifespan


#


_create_lock = Lock()


async def _start_session(
    user_id: str,
) -> Session:

    if _store.is_full(user_id):
        raise ToolError("Server at capacity. Try again later.")

    box = await to_thread(
        open_box,
        _settings.container_backend,
        _settings.sandbox_lang,
        _settings.sandbox_image,
        _settings.sandbox_max_memory,
        _settings.session_max_lifetime_seconds,
    )

    session = Session(box=box)
    _store.set(user_id, session)
    return session


async def ensure_session(
    user_id: str = TokenClaim("id"),
) -> Session:

    session = _store.get(user_id)

    if session is not None:
        return session

    async with _create_lock:

        session = _store.get(user_id)

        if session is None:
            session = await _start_session(user_id)

        return session


async def require_session(
    user_id: str = TokenClaim("id"),
) -> Session:

    session = _store.get(user_id)

    if session is None:
        raise ToolError("No active sandbox session. Run code first.")

    return session


#


mcp = FastMCP(name="codebox")


# --- execution ---


@mcp.tool(
    name="run_code",
    description=(
        "Execute Python in the user's persistent sandbox container and return "
        "stdout, stderr and the exit code. Variables, imports and files "
        "persist across calls (IPython kernel), so build the computation up "
        "step by step. The container is created automatically on first use.\n\n"
        "IMPORTANT – the sandbox has NO pre-installed third-party packages. "
        "You MUST list every package the code imports via `libraries` "
        "(e.g. ['pandas', 'matplotlib']). Always populate `libraries` whenever "
        "the code uses any import that is not part of the Python standard "
        "library. Omitting required packages will cause an ImportError."
    ),
)
async def run_code(
    code: str = Field(
        description="Python source to execute in the user's sandbox.",
    ),
    libraries: list[str] = Field(
        default_factory=list,
        description=(
            "Packages to pip-install before running the code. "
            "REQUIRED for any non-stdlib import (e.g. ['numpy', 'pandas']). "
            "Do not leave this empty when the code uses third-party libraries."
        ),
    ),
    session: Session = Depends(ensure_session),
) -> ExecResult:

    async with session.lock:

        start = monotonic()

        try:
            output: ConsoleOutput = await to_thread(
                session.box.run,
                code,
                libraries,
                _settings.exec_timeout_seconds,
            )
        except SandboxTimeoutError as error:
            session.last_used = time()
            raise ToolError(
                f"Execution timed out after {_settings.exec_timeout_seconds:.0f}s. "
                "The session is still alive — simplify the code or split it "
                "across calls."
            ) from error
        except Exception as error:
            raise ToolError(
                f"Sandbox execution failed: {type(error).__name__}: {error}"
            ) from error

        result = ExecResult(
            exit_code=output.exit_code,
            stdout=output.stdout,
            stderr=output.stderr,
            duration_ms=int((monotonic() - start) * 1000),
        )

        session.last_used = time()

    return result


@mcp.tool(
    name="run_command",
    description=(
        "Run a shell command inside the user's sandbox container and return "
        "stdout, stderr and the exit code. Runs as a separate process: it "
        "shares the container's filesystem but NOT the IPython kernel's "
        "variables, cwd or env — use `run_code` for stateful Python work. "
        "Handy for quick inspection, e.g. 'ls -la /tmp', "
        "'find /tmp -name \"*.csv\"' or 'cat out.txt', to locate a produced "
        "file before calling `save_file`."
    ),
)
async def run_command(
    command: str = Field(
        description=(
            "Shell command to execute, e.g. 'ls -la /tmp' or "
            "'find /tmp -name \"*.png\"'."
        ),
    ),
    session: Session = Depends(require_session),
) -> ExecResult:

    async with session.lock:
        start = monotonic()
        output: ConsoleOutput = await to_thread(
            session.box.execute_command, command
        )
        duration_ms = int((monotonic() - start) * 1000)
        session.last_used = time()

    return ExecResult(
        exit_code=output.exit_code,
        stdout=output.stdout,
        stderr=output.stderr,
        duration_ms=duration_ms,
    )


# --- files ---


@mcp.tool(
    name="attach_file",
    description=(
        "Download a file the user attached in OpenWebUI (by `file_id`) into "
        "the sandbox at `/tmp/<file_name>`. After this call your code can "
        "read it with open('/tmp/<file_name>', ...). "
        "Use only when the user actually attached a file; never invent a `file_id`."
    ),
)
async def attach_file(
    file_id: str = Field(
        description="OpenWebUI file ID of a file the user attached.",
    ),
    file_name: str = Field(
        description="Relative name to write, e.g. 'data.csv'.",
    ),
    token: AccessToken = CurrentAccessToken(),
    session: Session = Depends(ensure_session),
) -> str:

    try:
        data = await download_file(
            file_id=file_id,
            token=token.token,
            base_url=_settings.owui_base_url,
        )
    except RuntimeError as error:
        raise ToolError(str(error)) from error

    if len(data) > _settings.max_file_size_bytes:
        raise ToolError(
            f"File too large ({len(data):,} bytes). "
            f"Limit is {_settings.max_file_size_bytes:,} bytes."
        )

    file_name = Path(file_name).name
    file_path = Path("/tmp").joinpath(file_name)

    async with session.lock:
        await to_thread(copy_into, session.box, file_path, data)
        session.last_used = time()

    return (
        f"Wrote {len(data)} bytes to '{file_path}'. "
        f"Read it from your code, e.g. open('{file_path}', 'rb')."
    )


@mcp.tool(
    name="save_file",
    description=(
        "Upload a file the sandbox produced to OpenWebUI so the user can "
        "download it. Pass the exact path where the code wrote the file "
        "(absolute or relative to the sandbox working directory), "
        "e.g. '/tmp/result.csv' or 'plot.png'. Returns the download URL."
    ),
)
async def save_file(
    file_path: str = Field(
        description=(
            "Absolute or relative path of the file inside the sandbox, "
            "e.g. '/tmp/result.csv' or 'plot.png'. "
            "Use run_command (e.g. 'ls -la /tmp') first if unsure of the path."
        ),
    ),
    file_name: str = Field(
        default="",
        description="Name to store it under in OpenWebUI. Defaults to the basename of `file_path`.",
    ),
    content_type: str = Field(
        default="application/octet-stream",
        description="MIME type of the file.",
    ),
    token: AccessToken = CurrentAccessToken(),
    session: Session = Depends(require_session),
) -> FileResponse:

    async with session.lock:
        data = await to_thread(copy_out, session.box, file_path)
        session.last_used = time()

    file_name = Path(file_name).name or Path(file_path).name

    try:
        uploaded = await upload_file(
            file_name=file_name,
            data=data,
            content_type=content_type,
            token=token.token,
            base_url=_settings.owui_base_url,
        )
    except RuntimeError as error:
        raise ToolError(str(error)) from error

    return FileResponse(
        file_name=file_name,
        file_size=len(data),
        owui_url=DOWNLOAD_FILE_URL.format(
            base_url=_settings.owui_base_url, file_id=uploaded.id),
    )


# --- session management ---


@mcp.tool(
    name="session_info",
    description=(
        "Report the user's current sandbox session: whether one is active, "
        "its id, the container backend, and how old / idle it is."
    ),
)
async def session_info(
    user_id: str = TokenClaim("id"),
) -> SessionInfo:

    session = _store.get(user_id)

    if session is None:
        return SessionInfo(
            active=False,
            session_id=None,
            backend=_settings.container_backend,
            age_seconds=0,
            idle_seconds=0,
        )

    now = time()

    return SessionInfo(
        active=True,
        session_id=session.session_id,
        backend=_settings.container_backend,
        age_seconds=int(now - session.created_at),
        idle_seconds=int(now - session.last_used),
    )


@mcp.tool(
    name="reset_session",
    description=(
        "Discard all variables and files and start a fresh sandbox container "
        "for the user. Use this when the user wants a clean slate."
    ),
)
async def reset_session(
    user_id: str = TokenClaim("id"),
) -> str:

    old_session = _store.pop(user_id)

    if old_session is not None:
        async with old_session.lock:
            await to_thread(old_session.box.close)

    new_session = await ensure_session(user_id)

    return (
        f"Started a fresh sandbox ('{new_session.session_id}'). "
        "All previous variables and files are gone."
    )
