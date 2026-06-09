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

    max_concurrent: int

    container_backend: Literal["docker", "podman", "kubernetes"]
    sandbox_image: str = ""
    sandbox_max_memory: str
    exec_timeout_seconds: float
    max_file_size_bytes: int

    pip_index_url: str = ""
    pip_extra_index_url: str = ""
    pip_trusted_host: str = ""

    @property
    def pip_environment(self) -> dict[str, str]:
        return {
            "PIP_INDEX_URL": self.pip_index_url,
            "PIP_EXTRA_INDEX_URL": self.pip_extra_index_url,
            "PIP_TRUSTED_HOST": self.pip_trusted_host,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
