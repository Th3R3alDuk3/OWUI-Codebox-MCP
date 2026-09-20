from asyncio import CancelledError, Lock, Semaphore, create_task, shield, sleep
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from os import getpid
from pathlib import Path
from shutil import rmtree
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.logging import get_logger
from microsandbox import Sandbox

from config import get_settings
from services.sandbox import INSTANCE, boot_sandbox

_settings = get_settings()
logger = get_logger(__name__)


@dataclass
class Session:
    user_id: str
    sandbox: Sandbox
    libs_dir: str
    # Holds the slots, the libs dir and the microVM until closed.
    stack: AsyncExitStack
    # Held while a call uses the session.
    lock: Lock = field(default_factory=Lock)
    idle_since: float = field(default_factory=monotonic)


# `pip install --user` target of each session; /tmp is often RAM.
_libs_root = Path("/var/tmp", INSTANCE)

_sessions: dict[str, Session] = {}

_user_slots: defaultdict[str, Semaphore] = defaultdict(
    lambda: Semaphore(_settings.max_concurrent_sandboxes_per_user))
_server_slots = Semaphore(_settings.max_concurrent_sandboxes)


@asynccontextmanager
async def user_slot(
    user_id: str,
) -> AsyncGenerator[None]:

    # An idle session of the user makes room for a new run.
    if _user_slots[user_id].locked():
        for session_id, session in sorted(
            _sessions.items(), key=lambda item: item[1].idle_since):
            if session.user_id == user_id and not session.lock.locked():
                await close_session(session_id)
                break

    # The acquire fast path never suspends, so this cannot race the acquire.
    if _user_slots[user_id].locked():
        raise ToolError(
            "Concurrent run limit reached "
            f"({_settings.max_concurrent_sandboxes_per_user} per user). "
            "Wait for a run to finish and try again."
        )

    async with _user_slots[user_id]:
        yield


@asynccontextmanager
async def server_slot() -> AsyncGenerator[None]:

    # Other users' idle sessions stay; the reaper frees their slots.
    if _server_slots.locked():
        raise ToolError("Server at capacity. Try again later.")

    async with _server_slots:
        yield


async def open_session(
    user_id: str,
) -> str:

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(user_slot(user_id))
        await stack.enter_async_context(server_slot())
        libs_dir = stack.enter_context(
            TemporaryDirectory(dir=_libs_root, ignore_cleanup_errors=True))
        sandbox = await stack.enter_async_context(
            boot_sandbox(host_libs_dir=libs_dir))
        session_id = uuid4().hex[:8]
        _sessions[session_id] = Session(user_id, sandbox, libs_dir, stack.pop_all())

    logger.info("session %s: opened for user %s", session_id, user_id)
    return session_id


@asynccontextmanager
async def use_session(
    session_id: str,
    user_id: str,
) -> AsyncGenerator[Session]:

    session = _sessions.get(session_id)

    if session is None or session.user_id != user_id:
        raise ToolError(
            "Unknown or expired session_id. Omit it to start a new session.")

    if session.lock.locked():
        raise ToolError(
            "The session is busy with another run. Wait for it to finish.")

    async with session.lock:

        try:
            await session.sandbox.ping()
        except Exception as error:
            await close_session(session_id)
            raise ToolError(
                "The session has ended; sessions last at most "
                f"{_settings.sandbox_max_duration:.0f}s. Omit session_id to "
                "start a new one."
            ) from error

        try:
            yield session
        except ToolError as error:
            logger.warning("session %s: %s", session_id, error)
            raise ToolError(
                f"{error} The session stays open as session_id '{session_id}'."
            ) from error
        finally:
            session.idle_since = monotonic()


async def close_session(
    session_id: str,
) -> None:

    session = _sessions.pop(session_id, None)

    if session is not None:
        # Released even if the VM is gone; a client abort must not cut it short.
        with suppress(Exception):
            await shield(session.stack.aclose())
        logger.info("session %s: closed", session_id)


async def _reap_sessions() -> None:

    while True:
        await sleep(1)
        for session_id, session in list(_sessions.items()):
            idle = monotonic() - session.idle_since
            if idle > _settings.sandbox_idle_timeout and not session.lock.locked():
                await close_session(session_id)


async def _cleanup() -> None:

    # Ctrl-C cancels this task once; the remaining sessions still close.
    for session_id in list(_sessions):
        with suppress(CancelledError):
            await close_session(session_id)

    # Libs dirs of this instance and of dead ones.
    for path in Path("/var/tmp").glob("owui-codebox-*"):
        pid = int(path.name.removeprefix("owui-codebox-"))
        if pid == getpid() or not Path(f"/proc/{pid}").exists():
            rmtree(path, ignore_errors=True)


@asynccontextmanager
async def session_lifespan(
    server: FastMCP,
) -> AsyncGenerator[None]:

    await _cleanup()

    _libs_root.mkdir()

    reaper = create_task(_reap_sessions())

    try:
        yield
    finally:

        reaper.cancel()

        await _cleanup()
