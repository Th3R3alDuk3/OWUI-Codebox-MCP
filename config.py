from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str
    port: int

    jwt_secret: str
    jwt_algorithm: str
    owui_base_url: str

    session_ttl_seconds: int
    max_sessions: int

    container_backend: Literal["docker", "podman", "kubernetes", "micromamba"]
    sandbox_lang: str
    sandbox_image: str | None
    sandbox_max_memory: str
    exec_timeout_seconds: float
    max_file_size_bytes: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
