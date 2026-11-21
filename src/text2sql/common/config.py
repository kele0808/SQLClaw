from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings from T2S_* environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="T2S_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    system_db_url: SecretStr
    business_db_url: SecretStr
    # Connect/read timeout of the t2s_ro connection only (metadata collection,
    # /readyz). Gateway query and EXPLAIN timeouts are separate (Spec 07).
    business_db_timeout_s: int = 5
    job_poll_interval_s: float = 1.0
    job_lease_s: int = 300


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg, unused-ignore]
