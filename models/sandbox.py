from asyncio import Lock
from dataclasses import dataclass, field
from time import time
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


@dataclass
class Session:
    box: Any
    session_id: str = field(default_factory=lambda: uuid4().hex)
    lock: Lock = field(default_factory=Lock)
    created_at: float = field(default_factory=time)
    last_used: float = field(default_factory=time)


class ExecResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class SessionInfo(BaseModel):
    active: bool
    session_id: str | None
    backend: str
    age_seconds: int
    idle_seconds: int


class FileResponse(BaseModel):
    file_name: str
    file_size: int
    owui_url: str
