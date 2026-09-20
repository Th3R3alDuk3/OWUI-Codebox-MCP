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
    # MiB
    sandbox_memory: int
    sandbox_cpus: int
    # seconds; the code run
    sandbox_exec_timeout: float
    # seconds; how long a session waits for the next call
    sandbox_idle_timeout: float
    # seconds; lifetime of each microVM
    sandbox_max_duration: float
    # bytes; per file, and for what a run prints
    sandbox_max_file_size: int
    # characters returned per stream
    sandbox_max_output: int
    # per call, for input_files and output_files each
    sandbox_max_files: int
    # per call
    sandbox_max_packages: int


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
