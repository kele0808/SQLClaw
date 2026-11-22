from pathlib import Path

import pytest
from pydantic import ValidationError

from text2sql.common.config import Settings, get_settings


def test_defaults_and_required_urls() -> None:
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.env == "dev"
    assert settings.log_level == "INFO"
    assert settings.business_db_timeout_s == 5
    assert settings.job_poll_interval_s == 1.0
    assert settings.job_lease_s == 300
    assert settings.system_db_url.get_secret_value().startswith("postgresql+psycopg://")


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_ENV", "test")
    monkeypatch.setenv("T2S_BUSINESS_DB_TIMEOUT_S", "9")
    monkeypatch.setenv("T2S_JOB_LEASE_S", "60")
    settings = get_settings()
    assert settings.env == "test"
    assert settings.business_db_timeout_s == 9
    assert settings.job_lease_s == 60


def test_missing_db_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("T2S_SYSTEM_DB_URL")
    with pytest.raises(ValidationError):
        get_settings()


def test_unknown_env_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_ENV", "staging")
    with pytest.raises(ValidationError):
        get_settings()


def test_reads_dotenv_in_working_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("T2S_BUSINESS_DB_URL")
    (tmp_path / ".env").write_text(
        "T2S_BUSINESS_DB_URL=mysql+pymysql://ro:dotenv@127.0.0.1:3308/t2s_business\n"
        "T2S_TEST_SYSTEM_DB_URL=ignored-extra-key\n",
        encoding="utf-8",
    )
    settings = get_settings()
    assert settings.business_db_url.get_secret_value().endswith(":3308/t2s_business")


def test_secrets_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", "postgresql+psycopg://u:topsecret@h/db")
    settings = get_settings()
    assert "topsecret" not in repr(settings)
    assert "topsecret" not in str(settings.model_dump())


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_datasource_id_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_settings().datasource_id == "local"
    get_settings.cache_clear()
    monkeypatch.setenv("T2S_DATASOURCE_ID", "local_mysql57")
    assert get_settings().datasource_id == "local_mysql57"


@pytest.mark.parametrize("bad", ["Local", "has-dash", "", "x" * 65, "a b"])
def test_datasource_id_pattern_is_enforced(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("T2S_DATASOURCE_ID", bad)
    with pytest.raises(ValidationError):
        get_settings()


def test_value_index_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    assert settings.value_index_max_distinct == 5000
    assert settings.value_index_max_rows == 2_000_000
    assert settings.value_index_max_value_length == 128
    assert settings.value_index_timeout_ms == 3000
    assert settings.value_index_tenant_column == "tenant_id"
    get_settings.cache_clear()
    monkeypatch.setenv("T2S_VALUE_INDEX_TENANT_COLUMN", "")
    assert get_settings().value_index_tenant_column == ""
