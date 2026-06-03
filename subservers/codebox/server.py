from asyncio import Lock, to_thread
from time import monotonic, time

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentAccessToken, TokenClaim
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from llm_sandbox import ConsoleOutput
from pydantic import Field

from config import get_settings
from models.sandbox import ExecResult, FileResponse, Session, SessionInfo
from services.owui import download_file, upload_file
from subservers._store import SessionStore
from subservers.codebox._utils import (
    copy_into,
    copy_out,
    open_box,
    runtime_path,
)


_WORKDIR = "/sandbox"

_settings = get_settings()


#


_store = SessionStore(
    max_size=_settings.max_sessions,
    ttl=_settings.session_ttl_seconds,
    sweep_interval=_settings.session_sweep_interval_seconds,
)

lifespan = _store.lifespan


#


_create_lock = Lock()


async def _start_session(
    user_id: str,
) -> Session:

    box = await to_thread(
        open_box,
        _settings.container_backend,
        _settings.sandbox_lang,
        _settings.sandbox_image,
        _settings.sandbox_max_memory,
    )

    session = Session(box=box)

    _store.set(user_id, session)

    return session


async def _ensure_session(
    user_id: str,
) -> Session:

    session = _store.get(user_id)

    if session is not None:
        return session

    async with _create_lock:

        session = _store.get(user_id)

        if session is None:
            session = await _start_session(user_id)

        return session

   
#


mcp = FastMCP(name="codebox")


@mcp.tool(
    name="run_code",
    description=(
        "Execute Python in the user's persistent sandbox container and return "
        "stdout, stderr and the exit code. Variables, imports and files "
        "persist across calls (IPython kernel), so build the computation up "
        "step by step. The container is created automatically on first use. "
        "Pass `libraries` to pip-install packages before running."
    ),
)
async def run_code(
    code: str = Field(
        description="Python source to execute in the user's sandbox.",
    ),
    libraries: list[str] = Field(
        default_factory=list,
        description="Optional packages to pip-install first, e.g. ['numpy'].",
    ),
    user_id: str = TokenClaim("id"),
) -> ExecResult:

    session = await _ensure_session(user_id)

    async with session.lock:

        start = monotonic()

        try:
            output: ConsoleOutput = await to_thread(
                session.box.run,
                code,
                libraries,
                _settings.exec_timeout_seconds,
            )
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
        _store.touch(user_id, session)

    return result


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

    new_session = await _ensure_session(user_id)

    return (
        f"Started a fresh sandbox ('{new_session.session_id}'). "
        "All previous variables and files are gone."
    )


@mcp.tool(
    name="stop_session",
    description=(
        "Tear the user's sandbox container down completely and free its "
        "resources. A later `run_code` will start a new one."
    ),
)
async def stop_session(
    user_id: str = TokenClaim("id"),
) -> str:

    session = _store.pop(user_id)

    if session is None:
        return "No active sandbox session."

    async with session.lock:
        await to_thread(session.box.close)

    return f"Stopped sandbox session '{session.session_id}'."


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
    name="attach_file",
    description=(
        "Download a file the user attached in OpenWebUI (by `file_id`) into "
        "the sandbox working directory so code can read it. Use only when the "
        "user actually attached a file; never invent a `file_id`."
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
    user_id: str = TokenClaim("id"),
) -> str:

    data = await download_file(
        file_id=file_id,
        token=token.token,
        base_url=_settings.owui_base_url,
    )

    session = await _ensure_session(user_id)

    file_path = runtime_path(_WORKDIR, file_name)

    async with session.lock:
        await to_thread(copy_into, session.box, file_path, data)
        _store.touch(user_id, session)

    return (
        f"Wrote {len(data)} bytes to '{file_path}'. "
        f"Read it from your code, e.g. open('{file_path}', 'rb')."
    )


@mcp.tool(
    name="save_file",
    description=(
        "Read a file the sandbox produced (by `path`) and upload it to "
        "OpenWebUI so the user can download it. Returns the OpenWebUI URL."
    ),
)
async def save_file(
    file_path: str = Field(
        description="Path of the file inside the sandbox, e.g. 'plot.png'.",
    ),
    file_name: str = Field(
        default="",
        description="Name to store it under in OpenWebUI. Defaults to the basename of `path`.",
    ),
    content_type: str = Field(
        default="application/octet-stream",
        description="MIME type of the file.",
    ),
    token: AccessToken = CurrentAccessToken(),
    user_id: str = TokenClaim("id"),
) -> FileResponse:

    session = _store.get(user_id)

    if session is None:
        raise ValueError("No active sandbox session. Run code first.")

    file_path = runtime_path(_WORKDIR, file_path)

    async with session.lock:
        data = await to_thread(copy_out, session.box, file_path)
        _store.touch(user_id, session)

    uploaded = await upload_file(
        file_name=file_name,
        data=data,
        content_type=content_type,
        token=token.token,
        base_url=_settings.owui_base_url,
    )

    return FileResponse(
        file_name=file_name,
        file_size=len(data),
        owui_url=f"{_settings.owui_base_url}/api/v1/files/{uploaded.id}/content",
    )
