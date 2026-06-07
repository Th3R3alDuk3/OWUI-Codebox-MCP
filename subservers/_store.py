from asyncio import CancelledError, create_task, sleep, to_thread
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from time import time

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger

from models.sandbox import Session


logger = get_logger("codebox.store")


class SessionStore:

    def __init__(self,
        max_size: int,
        idle_timeout: float,
        sweep_interval: float,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._max_size = max_size
        self._idle_timeout = idle_timeout
        self._sweep_interval = sweep_interval

    def is_full(self,
        user_id: str,
    ) -> bool:
        return (
            len(self._sessions) >= self._max_size
            and user_id not in self._sessions
        )

    def set(self,
        user_id: str,
        session: Session,
    ) -> None:
        self._sessions[user_id] = session

    def get(self,
        user_id: str,
    ) -> Session | None:
        return self._sessions.get(user_id)

    def pop(self,
        user_id: str,
    ) -> Session | None:
        return self._sessions.pop(user_id, None)

    async def _reap_idle(self) -> None:

        now = time()

        for user_id, session in list(self._sessions.items()):

            idle = now - session.last_used

            if idle <= self._idle_timeout:
                continue

            if session.lock.locked():
                continue

            if self._sessions.get(user_id) is not session:
                continue

            del self._sessions[user_id]

            logger.info(
                f"Reaping idle session {session.session_id} "
                f"(user {user_id}) after {idle:.0f}s idle"
            )

            with suppress(Exception):
                await to_thread(session.box.close)

    async def _sweep_task(self) -> None:
        while True:
            await sleep(self._sweep_interval)
            with suppress(Exception):
                await self._reap_idle()

    @asynccontextmanager
    async def lifespan(self,
        _server: FastMCP,
    ) -> AsyncIterator[None]:

        task = create_task(self._sweep_task())

        try:
            yield
        finally:

            task.cancel()

            with suppress(CancelledError):
                await task

            for session in list(self._sessions.values()):
                with suppress(Exception):
                    await to_thread(session.box.close)

            self._sessions.clear()
