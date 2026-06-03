from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    owui_base_url: str = "http://localhost:3000"

    session_ttl_seconds: int = 3600
    session_sweep_interval_seconds: int = 300
    max_sessions: int = 1000

    container_backend: Literal[
        "docker", "podman", "kubernetes", "micromamba"
    ] = "docker"
    sandbox_lang: str = "python"
    sandbox_image: str | None = None
    sandbox_max_memory: str = "1GB"
    exec_timeout_seconds: float = 30.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
