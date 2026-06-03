from asyncio import CancelledError, create_task, sleep, to_thread
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from cachetools import TTLCache
from fastmcp import FastMCP

from models.sandbox import Session


class SessionStore:

    def __init__(self,
        max_size: int,
        ttl: float,
        sweep_interval: float,
    ) -> None:
        self._sessions: TTLCache[str, Session] = TTLCache(max_size, ttl)
        self._sweep_interval = sweep_interval

    def set(self,
        user_id: str,
        session: Session,
    ) -> None:
        self._sessions[user_id] = session

    def get(self,
        user_id: str,
    ) -> Session | None:
        return self._sessions.get(user_id)

    def touch(self,
        user_id: str,
        session: Session,
    ) -> None:
        if self._sessions.get(user_id) is session:
            self._sessions[user_id] = session

    def pop(self,
        user_id: str,
    ) -> Session | None:
        return self._sessions.pop(user_id, None)

    async def _sweep_task(self) -> None:
        while True:
            await sleep(self._sweep_interval)
            for _user_id, session in self._sessions.expire() or []:
                with suppress(Exception):
                    await to_thread(session.box.close)

    @asynccontextmanager
    async def lifespan(
        self,
        server: FastMCP,
    ) -> AsyncIterator[None]:

        task = create_task(self._sweep_task())

        try:
            yield
        finally:

            task.cancel()

            with suppress(CancelledError):
                await task

            for _user_id, session in list(self._sessions.items()):
                with suppress(Exception):
                    await to_thread(session.box.close)

            self._sessions.clear()
