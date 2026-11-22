from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    # One process serves one business data source (Spec 02 4.1).
    datasource_id: str = Field(default="local", pattern=r"^[a-z0-9_]{1,64}$")
    # Value index (Spec 02 M10): distinct values of low-cardinality text columns.
    value_index_max_distinct: int = Field(default=5000, ge=1)
    value_index_max_rows: int = Field(default=2_000_000, ge=1)
    value_index_max_value_length: int = Field(default=128, ge=1, le=512)
    # Must stay below business_db_timeout_s, the client read timeout.
    value_index_timeout_ms: int = Field(default=3000, ge=100)
    # Tables without this column are skipped so values never cross tenants.
    # Empty string disables the check (single-tenant databases).
    value_index_tenant_column: str = "tenant_id"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg, unused-ignore]
