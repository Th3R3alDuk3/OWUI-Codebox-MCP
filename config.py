from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_secret: str
    jwt_algorithm: str

    owui_base_url: str
    owui_verify_tls: bool

    max_concurrent_sandboxes: int
    max_concurrent_sandboxes_per_user: int

    rate_limit_rps: float
    rate_limit_burst: int

    sandbox_image: str
    sandbox_max_memory: str
    sandbox_max_cpus: float
    # seconds
    sandbox_exec_timeout: float
    # bytes
    sandbox_max_file_size: int


@cache
def get_settings() -> Settings:
    return Settings()
