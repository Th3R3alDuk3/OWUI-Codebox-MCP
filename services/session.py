from asyncio import CancelledError, Lock, Semaphore, create_task, shield, sleep
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from time import monotonic
from weakref import WeakValueDictionary

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.logging import get_logger
from microsandbox import Job, MicrosandboxError, Sandbox

from config import get_settings
from services.sandbox import boot_sandbox

_settings = get_settings()
logger = get_logger(__name__)


@dataclass
class Session:
    sandbox: Sandbox
    # Holds the slot and the microVM until closed.
    stack: AsyncExitStack
    idle_since: float = field(default_factory=monotonic)
    # Started by run_python when needed.
    kernel: Job | None = None
    # Until a result has told the model.
    new: bool = True


# One session per user, keyed by user ID.
_sessions: dict[str, Session] = {}

# Exists only while a call of the user holds it.
_user_locks: WeakValueDictionary[str, Lock] = WeakValueDictionary()
_server_slots = Semaphore(_settings.max_concurrent_sandboxes)


@asynccontextmanager
async def server_slot() -> AsyncGenerator[None]:

    # Idle sessions keep their slots; the reaper frees them.
    if _server_slots.locked():
        raise ToolError("Server at capacity. Try again later.")

    async with _server_slots:
        yield


@asynccontextmanager
async def use_session(
    user_id: str,
) -> AsyncGenerator[Session]:

    lock = _user_locks.setdefault(user_id, Lock())

    # The acquire fast path never suspends, so this cannot race the acquire.
    if lock.locked():
        raise ToolError(
            "Another run of this user is still going. Wait for it to finish.")

    async with lock:

        session = _sessions.get(user_id)

        if session is not None:
            try:
                await session.sandbox.ping()
            except MicrosandboxError:
                # Past SANDBOX_MAX_DURATION; a new microVM replaces it.
                await close_session(user_id)
                session = None

        if session is None:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(server_slot())
                sandbox = await stack.enter_async_context(boot_sandbox())
                session = _sessions[user_id] = Session(sandbox, stack.pop_all())
            logger.info("session %s: opened", user_id)

        try:
            yield session
        except ToolError as error:
            logger.warning("session %s: %s", user_id, error)
            raise
        except MicrosandboxError as error:
            logger.warning("session %s: %s", user_id, error)
            raise ToolError(
                "The sandbox failed during this call, usually because the "
                f"session reached its {_settings.sandbox_max_duration:.0f}s "
                "limit. Then variables and files are lost and the next call "
                "starts a new session."
            ) from error
        finally:
            session.idle_since = monotonic()


async def close_session(
    user_id: str,
) -> None:

    session = _sessions.pop(user_id, None)

    if session is not None:
        # Released even if the VM is gone; a client abort must not cut it short.
        with suppress(Exception):
            await shield(session.stack.aclose())
        logger.info("session %s: closed", user_id)


async def _reap_sessions() -> None:

    while True:
        await sleep(1)
        for user_id, session in list(_sessions.items()):
            idle = monotonic() - session.idle_since
            if idle > _settings.sandbox_idle_timeout \
                and user_id not in _user_locks:
                await close_session(user_id)


@asynccontextmanager
async def session_lifespan(
    server: FastMCP,
) -> AsyncGenerator[None]:

    reaper = create_task(_reap_sessions())

    try:
        yield
    finally:

        reaper.cancel()

        # Ctrl-C cancels this once; the remaining sessions still close.
        for user_id in list(_sessions):
            with suppress(CancelledError):
                await close_session(user_id)
