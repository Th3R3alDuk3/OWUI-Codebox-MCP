from asyncio import to_thread
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from cachetools import TTLCache
from fastmcp import FastMCP

from models.sandbox import Session


class SessionStore:

    def __init__(self,
        max_size: int,
        ttl: float,
    ) -> None:
        self._sessions: TTLCache[str, Session] = TTLCache(max_size, ttl)

    def is_full(self,
        user_id: str,
    ) -> bool:
        return (
            len(self._sessions) >= self._sessions.maxsize
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

    @asynccontextmanager
    async def lifespan(self,
        _server: FastMCP,
    ) -> AsyncIterator[None]:

        try:
            yield
        finally:

            for session in list(self._sessions.values()):
                with suppress(Exception):
                    await to_thread(session.box.close)

            self._sessions.clear()
