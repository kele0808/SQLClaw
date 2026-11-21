# Spec 01 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/specs/01-foundation.md`（状态：已确认）搭好所有后续子项目共用的运行底座：配置、JSON 日志与 `trace_id`、FastAPI 骨架、PostgreSQL（SQLAlchemy 2.x + Alembic）、只读 MySQL 连接、PostgreSQL 任务队列与 worker、LangGraph 挂载点、本地 Docker Compose 与测试分层。不含任何 Text2SQL 业务逻辑。

**Architecture:** 代码在 `src/text2sql/` 下按职责分包：`common`（配置、日志、追踪、错误）、`api`（应用工厂、中间件、路由）、`db`（系统库、业务库、ORM 模型）、`jobs`（队列与 worker）、`agent`（空图）。所有数据库访问是同步 SQLAlchemy；引擎由 `lru_cache` 懒加载，测试通过清缓存与环境变量隔离。单元测试不连数据库；集成测试以 `integration` 标记，依赖 `deployment/compose.yaml` 起的三个数据库。

**Tech Stack:** Python 3.12、uv、FastAPI、Starlette、pydantic-settings、SQLAlchemy 2.x（psycopg v3 / PyMySQL）、Alembic、LangGraph、httpx（TestClient）、pytest、ruff、mypy strict、Docker Compose（pgvector/pgvector:pg16、mysql:5.7、mysql:8.0）。

## Global Constraints

- Source spec: `docs/specs/01-foundation.md`; if code and spec disagree, stop and flag it (AGENTS.md).
- Python 3.12 via uv; dependency changes only through `uv add`; new runtime deps exactly: `sqlalchemy`, `alembic`, `httpx`. No new dev deps.
- Quality gates (must pass at the end of every task): `uv run ruff format --check .`, `uv run ruff check .` (rules E,F,W,I,B,UP,SIM,S,N; tests ignore S101 only), `uv run mypy` (strict, src and tests), `uv run pytest`.
- Settings: `text2sql.common.config.Settings`, prefix `T2S_`, reads `.env`; fields `env: Literal["dev","test","prod"] = "dev"`, `log_level: str = "INFO"`, `system_db_url: SecretStr` (required), `business_db_url: SecretStr` (required), `business_db_timeout_s: int = 5`, `job_poll_interval_s: float = 1.0`, `job_lease_s: int = 300`.
- Connection strings are `SecretStr`; never print plaintext in logs or error responses.
- `trace_id`: `contextvars.ContextVar`; `new_trace_id()` = `uuid4().hex` (32 hex chars); incoming `X-Trace-Id` accepted only if it matches `^[A-Za-z0-9-]{8,64}$`; always returned in response header `X-Trace-Id`; reset after the request.
- Log line fields: `ts` (ISO8601, UTC), `level`, `logger`, `msg`, `trace_id`, plus `extra` keys. Standard library `logging` only. One `http_request` line per request with method, path, status code, duration in ms.
- Error body for every non-2xx: `{"error": {"code": "...", "message": "...", "trace_id": "..."}}`; uncaught exceptions → `500 INTERNAL_ERROR`, message exactly `"Internal server error"`, stack only in logs; validation → `422 VALIDATION_ERROR`.
- `/healthz` and `/readyz` have no prefix; every other JSON API route lives under `APIRouter(prefix="/api/v1")`.
- `/readyz`: `SELECT 1` on PG and on MySQL (`business_db_url`, i.e. `t2s_ro`), each limited to 2 seconds; 200 `{"status": "ready", "checks": {"system_db": "ok", "business_db": "ok"}}`; otherwise 503 with failing checks `"error"`, no exception details.
- System DB: PostgreSQL, psycopg v3, URL `postgresql+psycopg://…`, sync `create_engine` + `sessionmaker`; `session_scope()` commits on success, rolls back on exception.
- Business DB: PyMySQL, URL `mysql+pymysql://…`; every connection runs `SET SESSION TRANSACTION READ ONLY`; PyMySQL `connect_timeout` and `read_timeout` = `business_db_timeout_s`; module exposes only `ping() -> bool` and `server_version() -> str`; `t2s_ro` is only for metadata collection and `/readyz`, never for user queries.
- `jobs` table columns exactly as spec 4.6, statuses `queued`/`running`/`succeeded`/`failed`, index `(status, run_after)`; queue API exactly `enqueue`, `claim`, `complete`, `fail(..., *, retryable: bool = True)`, `NonRetryableJobError`; backoff `2^attempts` seconds; `last_error` truncated to 2000 characters.
- Worker: `register(kind)` decorator, handler `handler(payload: dict[str, Any]) -> None`; `run_worker(stop_event: threading.Event)`; built-in `noop`; started with `python -m text2sql.jobs.worker`; SIGINT/SIGTERM finish the current job then exit.
- LangGraph mount check: `build_graph()` with single node `echo`; `POST /api/v1/agent/echo` (temporary; Spec 08 deletes it).
- Compose: `pg` (`pgvector/pgvector:pg16`, host 5433), `mysql57` (`mysql:5.7`, host 3307, `platform: linux/amd64`), `mysql80` (`mysql:8.0`, host 3308); healthchecks; named volumes; databases only by default; `api`/`worker` reserved for `profiles: [app]`, added by Spec 09 (no Dockerfile here).
- MySQL init in `deployment/mysql/init/`: database `t2s_business`, account `t2s_ro` with `SELECT` only, table `_t2s_probe`.
- Local credentials appear only in compose and `.env.example`; README states they must not be used anywhere else.
- Tests: marker `integration`; `uv run pytest` = unit only; `uv run pytest -m integration` = integration; integration tests read `T2S_TEST_SYSTEM_DB_URL`, `T2S_TEST_MYSQL57_URL`, `T2S_TEST_MYSQL80_URL` and skip (not fail) when unset; separate test database; clean data before and after each test.
- Out of scope: metadata collection, synthetic ticket DB, auth/tenants, frontend, Dockerfile/app containers, OpenTelemetry/Redis/Celery, GitHub Actions.
- Code, identifiers, commit messages in English; prose may be Chinese.

---

## File Structure

| Path | Responsibility | Task |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock` | runtime deps (via `uv add`), pytest marker and default filter | 1 |
| `src/text2sql/common/__init__.py`, `src/text2sql/common/config.py` | `Settings`, `get_settings()` | 1 |
| `tests/__init__.py`, `tests/unit/__init__.py` | make test dirs packages (unique module names for mypy/pytest) | 1 |
| `tests/conftest.py` | autouse settings/cache isolation | 1, 5, 6 |
| `tests/unit/test_config.py` | Settings tests | 1 |
| `src/text2sql/common/tracing.py`, `src/text2sql/common/logging.py` | trace context, JSON logging | 2 |
| `tests/unit/test_tracing.py`, `tests/unit/test_logging.py` | tests | 2 |
| `src/text2sql/common/errors.py` | `AppError`, error codes, `error_body()` | 3 |
| `src/text2sql/api/__init__.py`, `src/text2sql/api/routes/__init__.py` | package markers | 3 |
| `src/text2sql/api/middleware.py` | `TraceIdMiddleware` (pure ASGI) | 3 |
| `src/text2sql/api/app.py` | `create_app()`, exception handlers, `/api/v1` router | 3, 10 |
| `src/text2sql/api/routes/health.py` | `/healthz` (3), `/readyz` (7) | 3, 7 |
| `apps/api/main.py` | API entry point | 3 |
| `tests/unit/test_api_app.py` | middleware/error tests | 3 |
| `deployment/compose.yaml`, `deployment/postgres/init/01-test-db.sql`, `deployment/mysql/init/01-t2s-business.sql`, `.env.example` | local databases | 4 |
| `tests/integration/__init__.py`, `tests/integration/conftest.py` | env-driven fixtures | 4, 5 |
| `tests/integration/test_compose_databases.py` | databases reachable | 4 |
| `src/text2sql/db/__init__.py`, `src/text2sql/db/system.py` | PG engine/session/ping | 5 |
| `src/text2sql/db/models/__init__.py`, `base.py`, `jobs.py` | `Base`, `Job` | 5 |
| `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_create_jobs.py` | Alembic | 5 |
| `tests/unit/test_system_db.py`, `tests/integration/test_migrations.py` | tests | 5 |
| `src/text2sql/db/business.py` | read-only MySQL engine, `ping`, `server_version` | 6 |
| `tests/unit/test_business_db.py`, `tests/integration/test_business_db_readonly.py` | tests | 6 |
| `tests/unit/test_readyz.py`, `tests/integration/test_readyz_live.py` | tests | 7 |
| `src/text2sql/jobs/__init__.py`, `src/text2sql/jobs/queue.py` | queue | 8 |
| `tests/unit/test_queue_rules.py`, `tests/integration/test_queue.py` | tests | 8 |
| `src/text2sql/jobs/worker.py`, `apps/worker/main.py` | worker loop, registry, signals | 9 |
| `tests/unit/test_worker.py`, `tests/integration/test_worker.py` | tests | 9 |
| `src/text2sql/agent/__init__.py`, `src/text2sql/agent/graph.py`, `src/text2sql/api/routes/agent.py` | echo graph + temporary route | 10 |
| `tests/unit/test_agent_echo.py` | tests | 10 |
| `README.md`, `AGENTS.md` | local development docs | 11 |

---

### Task 1: Dependencies, pytest layout, and Settings (F1, F9 config)

**Files:**
- Modify: `pyproject.toml` (via `uv add`, plus `[tool.pytest.ini_options]`), `uv.lock`
- Create: `src/text2sql/common/__init__.py`, `src/text2sql/common/config.py`
- Create: `tests/__init__.py`, `tests/unit/__init__.py` (only if missing), `tests/conftest.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `text2sql.common.config.Settings` (fields in Global Constraints)
  - `text2sql.common.config.get_settings() -> Settings` (`functools.lru_cache(maxsize=1)`; tests call `get_settings.cache_clear()`)
  - `tests/conftest.py` autouse fixture `_isolated_settings`: chdir to `tmp_path` (so a developer `.env` is never read), removes `T2S_*` env vars except `T2S_TEST_*`, sets `T2S_SYSTEM_DB_URL=postgresql+psycopg://unit:unit@127.0.0.1:1/unit` and `T2S_BUSINESS_DB_URL=mysql+pymysql://unit:unit@127.0.0.1:1/unit` (port 1 refuses connections immediately), clears caches before and after each test. Later tasks extend `_clear_caches()`.

- [ ] **Step 1: Add runtime dependencies**

Run: `uv add "sqlalchemy>=2.0" alembic httpx`
Expected: output lists `+ alembic==…`, `+ httpx==…`, `+ sqlalchemy==…`; `[project].dependencies` in `pyproject.toml` now has the three; `uv.lock` updated.

- [ ] **Step 2: Configure pytest markers**

Add to `pyproject.toml` (merge into an existing `[tool.pytest.ini_options]` table and keep its other keys):

```toml
[tool.pytest.ini_options]
markers = [
    "integration: requires the Docker Compose databases (deployment/compose.yaml)",
]
addopts = "--strict-markers -m 'not integration'"
```

`uv run pytest -m integration` works because a later `-m` on the command line overrides the one in `addopts`.

- [ ] **Step 3: Make test directories packages**

Create empty `tests/__init__.py` and `tests/unit/__init__.py` if they do not exist. Reason: Task 4 adds `tests/integration/conftest.py`; without packages mypy reports `Duplicate module named "conftest"`.

Run: `uv run pytest`
Expected: the existing `tests/unit/test_smoke.py` still passes.

- [ ] **Step 4: Write the autouse isolation fixture**

Create `tests/conftest.py`:

```python
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from text2sql.common.config import get_settings

UNIT_SYSTEM_DB_URL = "postgresql+psycopg://unit:unit@127.0.0.1:1/unit"
UNIT_BUSINESS_DB_URL = "mysql+pymysql://unit:unit@127.0.0.1:1/unit"


def _clear_caches() -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("T2S_") and not key.startswith("T2S_TEST_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", UNIT_SYSTEM_DB_URL)
    monkeypatch.setenv("T2S_BUSINESS_DB_URL", UNIT_BUSINESS_DB_URL)
    _clear_caches()
    yield
    _clear_caches()
```

- [ ] **Step 5: Write the failing Settings tests**

Create `tests/unit/test_config.py`:

```python
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


def test_reads_dotenv_in_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'text2sql.common'`.

- [ ] **Step 7: Implement Settings**

Create empty `src/text2sql/common/__init__.py`. Create `src/text2sql/common/config.py`:

```python
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
```

The ignore covers mypy's "missing named argument" for required fields that pydantic-settings fills from the environment; `unused-ignore` keeps strict mode quiet if the pydantic mypy plugin is enabled.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: `7 passed`.

- [ ] **Step 9: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: `All checks passed!`, `Success: no issues found in N source files`, all tests pass.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock src/text2sql/common tests/__init__.py tests/unit/__init__.py tests/conftest.py tests/unit/test_config.py
git commit -m "feat(common): add settings, runtime deps and pytest integration marker"
```

---

### Task 2: Trace context and JSON logging (F2 core)

**Files:**
- Create: `src/text2sql/common/tracing.py`, `src/text2sql/common/logging.py`
- Test: `tests/unit/test_tracing.py`, `tests/unit/test_logging.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `text2sql.common.tracing.get_trace_id() -> str | None`
  - `text2sql.common.tracing.set_trace_id(trace_id: str | None) -> contextvars.Token[str | None]`
  - `text2sql.common.tracing.reset_trace_id(token: contextvars.Token[str | None]) -> None`
  - `text2sql.common.tracing.new_trace_id() -> str` (32 lowercase hex chars)
  - `text2sql.common.tracing.is_valid_trace_id(value: str) -> bool`
  - `text2sql.common.logging.JsonFormatter(logging.Formatter)`
  - `text2sql.common.logging.configure_logging(level: str) -> None` (root logger gets exactly one stdout handler with `JsonFormatter`)

- [ ] **Step 1: Write the failing tracing tests**

Create `tests/unit/test_tracing.py`:

```python
import re

from text2sql.common.tracing import (
    get_trace_id,
    is_valid_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)


def test_new_trace_id_is_32_hex() -> None:
    value = new_trace_id()
    assert re.fullmatch(r"[0-9a-f]{32}", value)
    assert new_trace_id() != value


def test_set_get_reset() -> None:
    assert get_trace_id() is None
    token = set_trace_id("trace-abc-123")
    assert get_trace_id() == "trace-abc-123"
    reset_trace_id(token)
    assert get_trace_id() is None


def test_validation_pattern() -> None:
    assert is_valid_trace_id("abcd-1234")
    assert is_valid_trace_id("a" * 64)
    assert not is_valid_trace_id("short")
    assert not is_valid_trace_id("a" * 65)
    assert not is_valid_trace_id("has space-123")
    assert not is_valid_trace_id("under_score_1")
```

- [ ] **Step 2: Write the failing logging tests**

Create `tests/unit/test_logging.py`:

```python
import io
import json
import logging

from text2sql.common.logging import JsonFormatter, configure_logging
from text2sql.common.tracing import reset_trace_id, set_trace_id


def _capture(name: str) -> tuple[logging.Logger, io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger, stream, handler


def test_json_fields_and_extra() -> None:
    logger, stream, handler = _capture("text2sql.test.json")
    token = set_trace_id("trace-log-0001")
    try:
        logger.info("hello %s", "世界", extra={"job_id": 7})
    finally:
        reset_trace_id(token)
        logger.removeHandler(handler)
    entry = json.loads(stream.getvalue())
    assert entry["level"] == "INFO"
    assert entry["logger"] == "text2sql.test.json"
    assert entry["msg"] == "hello 世界"
    assert entry["trace_id"] == "trace-log-0001"
    assert entry["job_id"] == 7
    assert entry["ts"].endswith("+00:00")
    assert "世界" in stream.getvalue()


def test_trace_id_null_outside_context() -> None:
    logger, stream, handler = _capture("text2sql.test.null")
    try:
        logger.info("no trace")
    finally:
        logger.removeHandler(handler)
    assert json.loads(stream.getvalue())["trace_id"] is None


def test_exception_is_formatted() -> None:
    logger, stream, handler = _capture("text2sql.test.exc")
    try:
        try:
            raise ValueError("bad value")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    entry = json.loads(stream.getvalue())
    assert entry["msg"] == "failed"
    assert "ValueError: bad value" in entry["exc"]


def test_configure_logging_installs_single_json_handler() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("debug")
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.DEBUG
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tracing.py tests/unit/test_logging.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'text2sql.common.tracing'`.

- [ ] **Step 4: Implement tracing**

Create `src/text2sql/common/tracing.py`:

```python
import re
import uuid
from contextvars import ContextVar, Token

TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(trace_id: str | None) -> Token[str | None]:
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def is_valid_trace_id(value: str) -> bool:
    return TRACE_ID_PATTERN.fullmatch(value) is not None
```

- [ ] **Step 5: Implement JSON logging**

Create `src/text2sql/common/logging.py`:

```python
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from text2sql.common.tracing import get_trace_id

_RESERVED_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None))
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, trace_id, extra keys."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": get_trace_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tracing.py tests/unit/test_logging.py -v`
Expected: `7 passed`.

- [ ] **Step 7: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/text2sql/common/tracing.py src/text2sql/common/logging.py tests/unit/test_tracing.py tests/unit/test_logging.py
git commit -m "feat(common): add trace id context and JSON log formatter"
```

---

### Task 3: API skeleton: errors, trace middleware, `/healthz` (F2 middleware, F3)

**Files:**
- Create: `src/text2sql/common/errors.py`
- Create: `src/text2sql/api/__init__.py`, `src/text2sql/api/routes/__init__.py`
- Create: `src/text2sql/api/middleware.py`, `src/text2sql/api/app.py`, `src/text2sql/api/routes/health.py`
- Create: `apps/api/main.py`
- Test: `tests/unit/test_api_app.py`

**Interfaces:**
- Consumes: `get_trace_id`, `set_trace_id`, `reset_trace_id`, `new_trace_id`, `is_valid_trace_id`, `configure_logging` (Task 2); `get_settings` (Task 1).
- Produces:
  - `text2sql.common.errors.AppError(code: str, message: str, status_code: int)` with attributes `code`, `message`, `status_code`
  - constants `INTERNAL_ERROR`, `VALIDATION_ERROR`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `HTTP_ERROR`, `NOT_READY` in `text2sql.common.errors`
  - `text2sql.common.errors.error_body(code: str, message: str) -> dict[str, dict[str, str]]`
  - `text2sql.api.middleware.TraceIdMiddleware` (pure ASGI; logger `text2sql.api.request`, message `http_request`, extras `method`, `path`, `status_code`, `duration_ms`)
  - `text2sql.api.app.API_V1_PREFIX = "/api/v1"`, `build_api_v1_router() -> APIRouter`, `create_app() -> FastAPI`
  - `text2sql.api.routes.health.router` (`APIRouter`, no prefix) with `GET /healthz`
  - `apps/api/main.py` attribute `app`

- [ ] **Step 1: Write the failing API tests**

Create `tests/unit/test_api_app.py`:

```python
import io
import json
import logging
import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from text2sql.api.app import create_app
from text2sql.common.errors import AppError
from text2sql.common.logging import JsonFormatter


class _Payload(BaseModel):
    n: int


def _build_app() -> FastAPI:
    app = create_app()

    @app.get("/api/v1/_test/app-error")
    def app_error() -> None:
        raise AppError("DEMO_CONFLICT", "Demo conflict", 409)

    @app.get("/api/v1/_test/crash")
    def crash() -> None:
        raise RuntimeError("postgresql+psycopg://u:leakedpw@db/x")

    @app.post("/api/v1/_test/validate")
    def validate(payload: _Payload) -> dict[str, int]:
        return {"n": payload.n}

    @app.get("/api/v1/_test/log")
    def log_inside() -> dict[str, str]:
        logging.getLogger("text2sql.test.route").info("inside_handler")
        return {"status": "ok"}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_build_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Trace-Id"])


def test_valid_incoming_trace_id_is_echoed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Trace-Id": "client-trace-01"})
    assert response.headers["X-Trace-Id"] == "client-trace-01"


def test_invalid_incoming_trace_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Trace-Id": "bad id!"})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Trace-Id"])


def test_app_error_uses_unified_body(client: TestClient) -> None:
    response = client.get("/api/v1/_test/app-error")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "DEMO_CONFLICT",
            "message": "Demo conflict",
            "trace_id": response.headers["X-Trace-Id"],
        }
    }


def test_validation_error(client: TestClient) -> None:
    response = client.post("/api/v1/_test/validate", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_route_uses_unified_body(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["trace_id"] == response.headers["X-Trace-Id"]


def test_uncaught_exception_hides_details(client: TestClient) -> None:
    response = client.get("/api/v1/_test/crash")
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Internal server error",
            "trace_id": response.headers["X-Trace-Id"],
        }
    }
    assert "leakedpw" not in response.text
    assert "Traceback" not in response.text


def test_all_log_lines_of_a_request_share_trace_id(client: TestClient) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("text2sql")
    saved_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        client.get("/api/v1/_test/log", headers={"X-Trace-Id": "trace-log-0001"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved_level)
    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert {"inside_handler", "http_request"} <= {e["msg"] for e in entries}
    assert {e["trace_id"] for e in entries} == {"trace-log-0001"}
    request_entry = next(e for e in entries if e["msg"] == "http_request")
    assert request_entry["method"] == "GET"
    assert request_entry["path"] == "/api/v1/_test/log"
    assert request_entry["status_code"] == 200
    assert isinstance(request_entry["duration_ms"], float)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_api_app.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'text2sql.api'`.

- [ ] **Step 3: Implement errors**

Create `src/text2sql/common/errors.py`:

```python
from text2sql.common.tracing import get_trace_id

INTERNAL_ERROR = "INTERNAL_ERROR"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
HTTP_ERROR = "HTTP_ERROR"
NOT_READY = "NOT_READY"


class AppError(Exception):
    """Base class for expected, client-visible errors."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    trace_id = get_trace_id() or ""
    return {"error": {"code": code, "message": message, "trace_id": trace_id}}
```

- [ ] **Step 4: Implement the middleware**

Create empty `src/text2sql/api/__init__.py` and `src/text2sql/api/routes/__init__.py`. Create `src/text2sql/api/middleware.py`:

```python
import logging
import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from text2sql.common.errors import INTERNAL_ERROR, error_body
from text2sql.common.tracing import (
    is_valid_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)

logger = logging.getLogger("text2sql.api.request")

TRACE_HEADER = "X-Trace-Id"


class TraceIdMiddleware:
    """Trace id per request, request log line, and last-resort 500 response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(TRACE_HEADER)
        if incoming and is_valid_trace_id(incoming):
            trace_id = incoming
        else:
            trace_id = new_trace_id()
        token = set_trace_id(trace_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_trace(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message).append(TRACE_HEADER, trace_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        except Exception:
            logger.exception("unhandled_exception")
            if response_started:
                raise
            response = JSONResponse(
                error_body(INTERNAL_ERROR, "Internal server error"), status_code=500
            )
            await response(scope, receive, send_with_trace)
        finally:
            logger.info(
                "http_request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            reset_trace_id(token)
```

Note: added via `app.add_middleware`, this middleware sits inside Starlette's `ServerErrorMiddleware`. Do **not** register an `Exception` handler on the app; that would build the 500 response outside the trace context, without the `X-Trace-Id` header.

- [ ] **Step 5: Implement the health router (healthz only) and the app factory**

Create `src/text2sql/api/routes/health.py`:

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

Create `src/text2sql/api/app.py`:

```python
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from text2sql import __version__
from text2sql.api.middleware import TraceIdMiddleware
from text2sql.api.routes import health
from text2sql.common.errors import (
    HTTP_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    VALIDATION_ERROR,
    AppError,
    error_body,
)

API_V1_PREFIX = "/api/v1"

_HTTP_ERROR_CODES = {404: NOT_FOUND, 405: METHOD_NOT_ALLOWED}


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code = _HTTP_ERROR_CODES.get(exc.status_code, HTTP_ERROR)
    return JSONResponse(
        error_body(code, str(exc.detail)),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        error_body(VALIDATION_ERROR, "Request validation failed"), status_code=422
    )


def build_api_v1_router() -> APIRouter:
    return APIRouter(prefix=API_V1_PREFIX)


def create_app() -> FastAPI:
    app = FastAPI(title="text2sql", version=__version__)
    app.add_middleware(TraceIdMiddleware)
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.include_router(health.router)
    app.include_router(build_api_v1_router())
    return app
```

Create `apps/api/main.py`:

```python
"""API entry point: `uv run uvicorn apps.api.main:app --no-access-log`."""

from text2sql.api.app import create_app
from text2sql.common.config import get_settings
from text2sql.common.logging import configure_logging

configure_logging(get_settings().log_level)
app = create_app()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_api_app.py -v`
Expected: `8 passed`.

- [ ] **Step 7: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/text2sql/common/errors.py src/text2sql/api apps/api/main.py tests/unit/test_api_app.py
git commit -m "feat(api): add app factory, trace id middleware, unified errors and healthz"
```

---

### Task 4: Local databases with Docker Compose and integration fixtures (F8, F9)

**Files:**
- Create: `deployment/compose.yaml`
- Create: `deployment/postgres/init/01-test-db.sql`, `deployment/mysql/init/01-t2s-business.sql`
- Create: `.env.example`
- Create: `tests/integration/__init__.py`, `tests/integration/conftest.py`
- Test: `tests/integration/test_compose_databases.py`

**Interfaces:**
- Consumes: `get_settings` (Task 1).
- Produces:
  - Compose services `pg` (5433; databases `t2s_system` and `t2s_test`; user `t2s`), `mysql57` (3307), `mysql80` (3308); MySQL database `t2s_business` with table `_t2s_probe(id, note)` holding one row `(1, 'probe')`; account `t2s_ro` with `SELECT ON t2s_business.*` only
  - `tests/integration/conftest.py`: `require_env(name: str) -> str` (skips with reason `"<NAME> not set"`); `MYSQL_TARGETS = {"mysql57": "T2S_TEST_MYSQL57_URL", "mysql80": "T2S_TEST_MYSQL80_URL"}`; fixtures `system_db_url() -> str` (copies `T2S_TEST_SYSTEM_DB_URL` into `T2S_SYSTEM_DB_URL`, clears settings cache), `mysql_target() -> str` (parametrized `mysql57`/`mysql80`), `mysql_url(mysql_target) -> str` (copies the matching URL into `T2S_BUSINESS_DB_URL`, clears settings cache)

- [ ] **Step 1: Write the Compose file**

Create `deployment/compose.yaml`:

```yaml
# Local development databases. Credentials are for this machine only.
# Profile convention (Spec 01 4.8): application services `api` and `worker`
# are added by Spec 09 under `profiles: [app]`; a plain
# `docker compose -f deployment/compose.yaml up -d` starts databases only.
name: text2sql

services:
  pg:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: t2s
      POSTGRES_PASSWORD: t2s_local_only
      POSTGRES_DB: t2s_system
    ports:
      - "5433:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./postgres/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U t2s -d t2s_system"]
      interval: 5s
      timeout: 3s
      retries: 20

  mysql57:
    image: mysql:5.7
    platform: linux/amd64  # no arm64 image exists; emulated on Apple Silicon
    environment:
      MYSQL_ROOT_PASSWORD: root_local_only
    command: ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_general_ci"]
    ports:
      - "3307:3306"
    volumes:
      - mysql57_data:/var/lib/mysql
      - ./mysql/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -uroot -p\"$$MYSQL_ROOT_PASSWORD\" --silent"]
      interval: 5s
      timeout: 5s
      retries: 60
      start_period: 30s

  mysql80:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: root_local_only
    command: ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_general_ci"]
    ports:
      - "3308:3306"
    volumes:
      - mysql80_data:/var/lib/mysql
      - ./mysql/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -uroot -p\"$$MYSQL_ROOT_PASSWORD\" --silent"]
      interval: 5s
      timeout: 5s
      retries: 60
      start_period: 30s

volumes:
  pg_data:
  mysql57_data:
  mysql80_data:
```

The MySQL healthcheck uses TCP (`-h 127.0.0.1`); during first-start initialization the temporary server has networking disabled, so the service only turns healthy after the init script has run.

- [ ] **Step 2: Write the init scripts**

Create `deployment/postgres/init/01-test-db.sql`:

```sql
-- Separate database for integration tests (T2S_TEST_SYSTEM_DB_URL).
CREATE DATABASE t2s_test OWNER t2s;
```

Create `deployment/mysql/init/01-t2s-business.sql`:

```sql
-- Runs once on first start of mysql57 and mysql80. Local credentials only.
CREATE DATABASE IF NOT EXISTS t2s_business
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS t2s_business._t2s_probe (
  id INT NOT NULL PRIMARY KEY,
  note VARCHAR(32) NOT NULL
) ENGINE=InnoDB;

INSERT INTO t2s_business._t2s_probe (id, note) VALUES (1, 'probe');

-- mysql_native_password lets PyMySQL authenticate on 8.0 without the
-- `cryptography` package (caching_sha2_password over plain TCP needs RSA).
CREATE USER IF NOT EXISTS 't2s_ro'@'%'
  IDENTIFIED WITH mysql_native_password BY 't2s_ro_local_only';
GRANT SELECT ON t2s_business.* TO 't2s_ro'@'%';
FLUSH PRIVILEGES;
```

- [ ] **Step 3: Write `.env.example`**

Create `.env.example`:

```dotenv
# Local development only. Never reuse these credentials anywhere else.
T2S_ENV=dev
T2S_LOG_LEVEL=INFO
T2S_SYSTEM_DB_URL=postgresql+psycopg://t2s:t2s_local_only@127.0.0.1:5433/t2s_system
T2S_BUSINESS_DB_URL=mysql+pymysql://t2s_ro:t2s_ro_local_only@127.0.0.1:3308/t2s_business
T2S_BUSINESS_DB_TIMEOUT_S=5
T2S_JOB_POLL_INTERVAL_S=1.0
T2S_JOB_LEASE_S=300

# Integration tests read these from the process environment, not from .env:
#   set -a && source .env && set +a && uv run pytest -m integration
T2S_TEST_SYSTEM_DB_URL=postgresql+psycopg://t2s:t2s_local_only@127.0.0.1:5433/t2s_test
T2S_TEST_MYSQL57_URL=mysql+pymysql://t2s_ro:t2s_ro_local_only@127.0.0.1:3307/t2s_business
T2S_TEST_MYSQL80_URL=mysql+pymysql://t2s_ro:t2s_ro_local_only@127.0.0.1:3308/t2s_business
```

Run: `git check-ignore .env`
Expected: prints `.env`. If it prints nothing, append `.env` to `.gitignore` and add `.gitignore` to this task's commit.

- [ ] **Step 4: Start the databases**

Run: `cp -n .env.example .env; docker compose -f deployment/compose.yaml up -d`
Then run: `docker compose -f deployment/compose.yaml ps`
Expected: within ~2 minutes (5.7 is slower under emulation) `pg`, `mysql57`, `mysql80` all show `Up … (healthy)`. The host port is 5433 (5432 is often taken by other local PostgreSQL instances); containers still listen on 5432 internally.

- [ ] **Step 5: Write the integration fixtures**

Create empty `tests/integration/__init__.py`. Create `tests/integration/conftest.py`:

```python
import os

import pytest

from text2sql.common.config import get_settings

MYSQL_TARGETS = {
    "mysql57": "T2S_TEST_MYSQL57_URL",
    "mysql80": "T2S_TEST_MYSQL80_URL",
}


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set")
    return value


@pytest.fixture
def system_db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = require_env("T2S_TEST_SYSTEM_DB_URL")
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", url)
    get_settings.cache_clear()
    return url


@pytest.fixture(params=list(MYSQL_TARGETS))
def mysql_target(request: pytest.FixtureRequest) -> str:
    target: str = request.param  # type: ignore[attr-defined, unused-ignore]
    return target


@pytest.fixture
def mysql_url(mysql_target: str, monkeypatch: pytest.MonkeyPatch) -> str:
    url = require_env(MYSQL_TARGETS[mysql_target])
    monkeypatch.setenv("T2S_BUSINESS_DB_URL", url)
    get_settings.cache_clear()
    return url
```

- [ ] **Step 6: Write the database smoke tests**

Create `tests/integration/test_compose_databases.py`:

```python
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_system_test_database_is_reachable(system_db_url: str) -> None:
    engine = create_engine(system_db_url)
    try:
        with engine.connect() as conn:
            name = conn.execute(text("SELECT current_database()")).scalar_one()
            assert name == "t2s_test"
    finally:
        engine.dispose()


def test_probe_table_is_readable_by_t2s_ro(mysql_url: str) -> None:
    engine = create_engine(mysql_url)
    try:
        with engine.connect() as conn:
            notes = conn.execute(text("SELECT note FROM _t2s_probe")).scalars().all()
            assert notes == ["probe"]
            database = conn.execute(text("SELECT DATABASE()")).scalar_one()
            assert database == "t2s_business"
    finally:
        engine.dispose()
```

- [ ] **Step 7: Verify skip behaviour, then run against Compose**

Run: `env -u T2S_TEST_SYSTEM_DB_URL -u T2S_TEST_MYSQL57_URL -u T2S_TEST_MYSQL80_URL uv run pytest -m integration -rs`
Expected: `3 skipped` with reasons `T2S_TEST_SYSTEM_DB_URL not set`, `T2S_TEST_MYSQL57_URL not set`, `T2S_TEST_MYSQL80_URL not set`; exit code 0.

Run: `set -a && source .env && set +a && uv run pytest -m integration -v`
Expected: `3 passed` (`test_system_test_database_is_reachable`, `test_probe_table_is_readable_by_t2s_ro[mysql57]`, `test_probe_table_is_readable_by_t2s_ro[mysql80]`).

Run: `uv run pytest`
Expected: unit tests pass; integration tests deselected.

- [ ] **Step 8: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy`
Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add deployment .env.example tests/integration
git commit -m "chore(deploy): add compose databases, init scripts and integration fixtures"
```

---

### Task 5: System database access, `Job` model, and Alembic (F4, F6 table)

**Files:**
- Create: `src/text2sql/db/__init__.py`, `src/text2sql/db/system.py`
- Create: `src/text2sql/db/models/__init__.py`, `src/text2sql/db/models/base.py`, `src/text2sql/db/models/jobs.py`
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_create_jobs.py`
- Modify: `tests/conftest.py` (full replacement below), `tests/integration/conftest.py` (full replacement below)
- Test: `tests/unit/test_system_db.py`, `tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: `get_settings` (Task 1), `configure_logging` (Task 2), `require_env`, `MYSQL_TARGETS`, fixtures `system_db_url`, `mysql_target`, `mysql_url` (Task 4).
- Produces:
  - `text2sql.db.system.get_engine() -> sqlalchemy.Engine` (`lru_cache(maxsize=1)`, `pool_pre_ping=True`)
  - `text2sql.db.system.get_sessionmaker() -> sqlalchemy.orm.sessionmaker[Session]` (`lru_cache(maxsize=1)`, `expire_on_commit=False`)
  - `text2sql.db.system.session_scope()` context manager yielding `Session` (commit on success; rollback and re-raise on exception; always close)
  - `text2sql.db.system.ping() -> bool`
  - `text2sql.db.models.Base`, `text2sql.db.models.Job` (table `jobs`; `id: int`, `kind: str`, `payload: dict[str, Any]`, `status: str`, `attempts: int`, `max_attempts: int`, `run_after: datetime`, `locked_until: datetime | None`, `last_error: str | None`, `trace_id: str | None`, `created_at: datetime`, `updated_at: datetime`)
  - `text2sql.db.models.jobs.JOB_STATUSES = ("queued", "running", "succeeded", "failed")`
  - Alembic revision `"0001"` (`down_revision = None`); `migrations/env.py` reads the URL from `get_settings().system_db_url` and calls `configure_logging` unless `config.attributes["configure_logging"]` is `False`
  - integration fixtures `alembic_cfg() -> alembic.config.Config` and `migrated_db(system_db_url, alembic_cfg) -> Iterator[Engine]` (upgrade to head; `TRUNCATE TABLE jobs RESTART IDENTITY` before and after)

- [ ] **Step 1: Extend the autouse cache clearing**

Replace `tests/conftest.py` with:

```python
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from text2sql.common.config import get_settings
from text2sql.db import system

UNIT_SYSTEM_DB_URL = "postgresql+psycopg://unit:unit@127.0.0.1:1/unit"
UNIT_BUSINESS_DB_URL = "mysql+pymysql://unit:unit@127.0.0.1:1/unit"


def _clear_caches() -> None:
    get_settings.cache_clear()
    system.get_engine.cache_clear()
    system.get_sessionmaker.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("T2S_") and not key.startswith("T2S_TEST_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", UNIT_SYSTEM_DB_URL)
    monkeypatch.setenv("T2S_BUSINESS_DB_URL", UNIT_BUSINESS_DB_URL)
    _clear_caches()
    yield
    _clear_caches()
```

- [ ] **Step 2: Write the failing unit tests**

Create `tests/unit/test_system_db.py`:

```python
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from text2sql.db import system
from text2sql.db.models import Base, Job
from text2sql.db.models.jobs import JOB_STATUSES


def _sqlite_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (v INTEGER)"))
    return sessionmaker(bind=engine, expire_on_commit=False)


def _count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.execute(text("SELECT count(*) FROM t")).scalar_one())


def test_session_scope_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _sqlite_factory()
    monkeypatch.setattr(system, "get_sessionmaker", lambda: factory)
    with system.session_scope() as session:
        session.execute(text("INSERT INTO t (v) VALUES (1)"))
    assert _count(factory) == 1


def test_session_scope_rolls_back_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _sqlite_factory()
    monkeypatch.setattr(system, "get_sessionmaker", lambda: factory)
    with pytest.raises(RuntimeError, match="boom"):
        with system.session_scope() as session:
            session.execute(text("INSERT INTO t (v) VALUES (1)"))
            raise RuntimeError("boom")
    assert _count(factory) == 0


def test_ping_returns_false_when_unreachable() -> None:
    assert system.ping() is False


def test_engine_uses_settings_url_and_is_cached() -> None:
    assert system.get_engine().url.port == 1
    assert system.get_engine() is system.get_engine()


def test_jobs_table_shape() -> None:
    table = Base.metadata.tables["jobs"]
    assert Job.__table__ is table
    assert set(table.columns.keys()) == {
        "id",
        "kind",
        "payload",
        "status",
        "attempts",
        "max_attempts",
        "run_after",
        "locked_until",
        "last_error",
        "trace_id",
        "created_at",
        "updated_at",
    }
    assert {index.name for index in table.indexes} == {"ix_jobs_status_run_after"}
    assert [column.name for column in table.primary_key.columns] == ["id"]
    assert JOB_STATUSES == ("queued", "running", "succeeded", "failed")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'text2sql.db'` (raised from `tests/conftest.py`).

- [ ] **Step 4: Implement the models**

Create empty `src/text2sql/db/__init__.py`.

Create `src/text2sql/db/models/base.py`:

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

Create `src/text2sql/db/models/jobs.py`:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from text2sql.db.models.base import Base

JOB_STATUSES = ("queued", "running", "succeeded", "failed")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
```

Create `src/text2sql/db/models/__init__.py`:

```python
from text2sql.db.models.base import Base
from text2sql.db.models.jobs import Job

__all__ = ["Base", "Job"]
```

- [ ] **Step 5: Implement the system DB module**

Create `src/text2sql/db/system.py`:

```python
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from text2sql.common.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = get_settings().system_db_url.get_secret_value()
    return create_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("system_db_ping_failed", exc_info=True)
        return False
    return True
```

- [ ] **Step 6: Run unit tests to verify they pass**

Run: `uv run pytest tests/unit -v`
Expected: all pass, including 5 tests in `test_system_db.py`.

- [ ] **Step 7: Add migration fixtures and the failing migration test**

Replace `tests/integration/conftest.py` with:

```python
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from text2sql.common.config import get_settings
from text2sql.db.system import get_engine

REPO_ROOT = Path(__file__).resolve().parents[2]

MYSQL_TARGETS = {
    "mysql57": "T2S_TEST_MYSQL57_URL",
    "mysql80": "T2S_TEST_MYSQL80_URL",
}


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set")
    return value


@pytest.fixture
def system_db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = require_env("T2S_TEST_SYSTEM_DB_URL")
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", url)
    get_settings.cache_clear()
    return url


@pytest.fixture(params=list(MYSQL_TARGETS))
def mysql_target(request: pytest.FixtureRequest) -> str:
    target: str = request.param  # type: ignore[attr-defined, unused-ignore]
    return target


@pytest.fixture
def mysql_url(mysql_target: str, monkeypatch: pytest.MonkeyPatch) -> str:
    url = require_env(MYSQL_TARGETS[mysql_target])
    monkeypatch.setenv("T2S_BUSINESS_DB_URL", url)
    get_settings.cache_clear()
    return url


@pytest.fixture
def alembic_cfg() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.attributes["configure_logging"] = False
    return cfg


def _truncate_jobs(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE jobs RESTART IDENTITY"))


@pytest.fixture
def migrated_db(system_db_url: str, alembic_cfg: Config) -> Iterator[Engine]:
    command.upgrade(alembic_cfg, "head")
    engine = get_engine()
    _truncate_jobs(engine)
    yield engine
    _truncate_jobs(engine)
```

Create `tests/integration/test_migrations.py`:

```python
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from text2sql.db.models import Base
from text2sql.db.system import get_engine

pytestmark = pytest.mark.integration


def test_upgrade_matches_models_and_downgrade_removes_table(
    system_db_url: str, alembic_cfg: Config
) -> None:
    engine = get_engine()
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    try:
        inspector = inspect(engine)
        assert "jobs" in inspector.get_table_names()
        index_names = {ix["name"] for ix in inspector.get_indexes("jobs")}
        assert "ix_jobs_status_run_after" in index_names
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == []

        command.downgrade(alembic_cfg, "base")
        assert "jobs" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(alembic_cfg, "head")
```

Run: `set -a && source .env && set +a && uv run pytest -m integration tests/integration/test_migrations.py -v`
Expected: FAIL with `alembic.util.exc.CommandError` (no `script_location` — `alembic.ini` does not exist yet).

- [ ] **Step 8: Add Alembic files**

Create `alembic.ini`:

```ini
[alembic]
script_location = %(here)s/migrations
file_template = %%(rev)s_%%(slug)s
path_separator = os
```

Create `migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create `migrations/env.py`:

```python
from alembic import context
from sqlalchemy import create_engine, pool

from text2sql.common.config import get_settings
from text2sql.common.logging import configure_logging
from text2sql.db.models import Base

config = context.config
target_metadata = Base.metadata

if config.attributes.get("configure_logging", True):
    configure_logging(get_settings().log_level)


def _database_url() -> str:
    return get_settings().system_db_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `migrations/versions/0001_create_jobs.py`:

```python
"""create jobs table

Revision ID: 0001
Revises:
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="queued"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_jobs_status",
        ),
    )
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status_run_after", table_name="jobs")
    op.drop_table("jobs")
```

- [ ] **Step 9: Run the migration test to verify it passes**

Run: `set -a && source .env && set +a && uv run pytest -m integration tests/integration/test_migrations.py -v`
Expected: `1 passed`.

- [ ] **Step 10: Verify the CLI (acceptance 2)**

Run: `uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head`
Expected: JSON log lines containing `Running upgrade  -> 0001, create jobs table` and `Running downgrade 0001 -> , create jobs table`; exit code 0. (Targets `t2s_system` via `.env`.)

- [ ] **Step 11: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, unit tests pass.

- [ ] **Step 12: Commit**

```bash
git add src/text2sql/db alembic.ini migrations tests/conftest.py tests/integration/conftest.py tests/unit/test_system_db.py tests/integration/test_migrations.py
git commit -m "feat(db): add system database session, jobs model and alembic migration"
```

---

### Task 6: Read-only business database access (F5)

**Files:**
- Create: `src/text2sql/db/business.py`
- Modify: `tests/conftest.py` (full replacement below)
- Test: `tests/unit/test_business_db.py`, `tests/integration/test_business_db_readonly.py`

**Interfaces:**
- Consumes: `get_settings` (Task 1); fixtures `mysql_url`, `mysql_target` (Task 4).
- Produces:
  - `text2sql.db.business.READ_ONLY_INIT_COMMAND = "SET SESSION TRANSACTION READ ONLY"`
  - `text2sql.db.business.business_connect_args(timeout_s: int) -> dict[str, Any]` → `{"connect_timeout": timeout_s, "read_timeout": timeout_s, "init_command": READ_ONLY_INIT_COMMAND}`
  - `text2sql.db.business.get_business_engine() -> sqlalchemy.Engine` (`lru_cache(maxsize=1)`, URL from `business_db_url`, `pool_pre_ping=True`, `connect_args=business_connect_args(settings.business_db_timeout_s)`)
  - `text2sql.db.business.ping() -> bool`
  - `text2sql.db.business.server_version() -> str`

- [ ] **Step 1: Extend the autouse cache clearing**

Replace `tests/conftest.py` with:

```python
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from text2sql.common.config import get_settings
from text2sql.db import business, system

UNIT_SYSTEM_DB_URL = "postgresql+psycopg://unit:unit@127.0.0.1:1/unit"
UNIT_BUSINESS_DB_URL = "mysql+pymysql://unit:unit@127.0.0.1:1/unit"


def _clear_caches() -> None:
    get_settings.cache_clear()
    system.get_engine.cache_clear()
    system.get_sessionmaker.cache_clear()
    business.get_business_engine.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("T2S_") and not key.startswith("T2S_TEST_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", UNIT_SYSTEM_DB_URL)
    monkeypatch.setenv("T2S_BUSINESS_DB_URL", UNIT_BUSINESS_DB_URL)
    _clear_caches()
    yield
    _clear_caches()
```

- [ ] **Step 2: Write the failing unit tests**

Create `tests/unit/test_business_db.py`:

```python
from typing import Any

import pytest

from text2sql.db import business


def test_connect_args_use_timeout_and_read_only_session() -> None:
    assert business.business_connect_args(7) == {
        "connect_timeout": 7,
        "read_timeout": 7,
        "init_command": "SET SESSION TRANSACTION READ ONLY",
    }


def test_engine_gets_business_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_BUSINESS_DB_TIMEOUT_S", "9")
    captured: dict[str, Any] = {}

    def fake_create_engine(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(business, "create_engine", fake_create_engine)
    business.get_business_engine()
    assert captured["url"] == "mysql+pymysql://unit:unit@127.0.0.1:1/unit"
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "connect_timeout": 9,
        "read_timeout": 9,
        "init_command": "SET SESSION TRANSACTION READ ONLY",
    }


def test_ping_returns_false_when_unreachable() -> None:
    assert business.ping() is False
```

- [ ] **Step 3: Write the failing integration tests**

Create `tests/integration/test_business_db_readonly.py`:

```python
import pymysql
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError

from text2sql.db import business

pytestmark = pytest.mark.integration


def test_ping_and_server_version(mysql_url: str, mysql_target: str) -> None:
    assert business.ping() is True
    expected_prefix = {"mysql57": "5.7.", "mysql80": "8.0."}[mysql_target]
    assert business.server_version().startswith(expected_prefix)


def test_session_is_read_only(mysql_url: str) -> None:
    with business.get_business_engine().connect() as conn:
        flag = conn.execute(text("SELECT @@SESSION.transaction_read_only")).scalar_one()
    assert int(flag) == 1


def test_write_through_engine_fails(mysql_url: str) -> None:
    with pytest.raises(DBAPIError):
        with business.get_business_engine().begin() as conn:
            conn.execute(text("INSERT INTO _t2s_probe (id, note) VALUES (2, 'x')"))


def test_write_without_read_only_session_is_denied_by_grants(mysql_url: str) -> None:
    url = make_url(mysql_url)
    conn = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "",
        password=url.password or "",
        database=url.database,
    )
    try:
        with pytest.raises(pymysql.err.MySQLError) as excinfo:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO _t2s_probe (id, note) VALUES (3, 'y')")
        assert excinfo.value.args[0] == 1142  # INSERT command denied
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_business_db.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'business' from 'text2sql.db'` (raised from `tests/conftest.py`).

- [ ] **Step 5: Implement the business DB module**

Create `src/text2sql/db/business.py`:

```python
"""Read-only MySQL access with the t2s_ro account.

Only for connectivity checks and metadata collection (Spec 02). User queries
never use this engine; they go through the gateway with tenant accounts
(Spec 07).
"""

import logging
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from text2sql.common.config import get_settings

logger = logging.getLogger(__name__)

READ_ONLY_INIT_COMMAND = "SET SESSION TRANSACTION READ ONLY"


def business_connect_args(timeout_s: int) -> dict[str, Any]:
    return {
        "connect_timeout": timeout_s,
        "read_timeout": timeout_s,
        "init_command": READ_ONLY_INIT_COMMAND,
    }


@lru_cache(maxsize=1)
def get_business_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.business_db_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args=business_connect_args(settings.business_db_timeout_s),
    )


def ping() -> bool:
    try:
        with get_business_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("business_db_ping_failed", exc_info=True)
        return False
    return True


def server_version() -> str:
    with get_business_engine().connect() as conn:
        return str(conn.execute(text("SELECT VERSION()")).scalar_one())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_business_db.py -v`
Expected: `3 passed`.

Run: `set -a && source .env && set +a && uv run pytest -m integration tests/integration/test_business_db_readonly.py -v`
Expected: `8 passed` (4 tests × `mysql57`, `mysql80`).

- [ ] **Step 7: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, unit tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/text2sql/db/business.py tests/conftest.py tests/unit/test_business_db.py tests/integration/test_business_db_readonly.py
git commit -m "feat(db): add read-only business database engine with t2s_ro timeouts"
```

---

### Task 7: `/readyz` (F3)

**Files:**
- Modify: `src/text2sql/api/routes/health.py` (full replacement below)
- Test: `tests/unit/test_readyz.py`, `tests/integration/test_readyz_live.py`

**Interfaces:**
- Consumes: `text2sql.db.system.ping`, `text2sql.db.business.ping` (Tasks 5, 6); `error_body`, `NOT_READY` (Task 3); `create_app` (Task 3); fixtures `system_db_url`, `mysql_url` (Task 4).
- Produces:
  - `text2sql.api.routes.health.ReadinessCheck = Callable[[], bool]`
  - `text2sql.api.routes.health.READY_TIMEOUT_S = 2.0`
  - `text2sql.api.routes.health.get_readiness_checks() -> dict[str, ReadinessCheck]` (FastAPI dependency; keys `system_db`, `business_db`)
  - `GET /readyz`: 200 `{"status": "ready", "checks": {...}}`; 503 `{"status": "not_ready", "checks": {...}, "error": {"code": "NOT_READY", "message": "Service not ready", "trace_id": "..."}}`

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_readyz.py`:

```python
import time

import pytest
from fastapi.testclient import TestClient

from text2sql.api.app import create_app
from text2sql.api.routes import health
from text2sql.api.routes.health import ReadinessCheck, get_readiness_checks


def _client(checks: dict[str, ReadinessCheck]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_readiness_checks] = lambda: checks
    return TestClient(app)


def _ok() -> bool:
    return True


def _down() -> bool:
    return False


def _boom() -> bool:
    raise RuntimeError("boom-secret-detail")


def test_ready_when_all_checks_pass() -> None:
    response = _client({"system_db": _ok, "business_db": _ok}).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"system_db": "ok", "business_db": "ok"},
    }


def test_not_ready_when_a_check_fails() -> None:
    response = _client({"system_db": _ok, "business_db": _down}).get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"system_db": "ok", "business_db": "error"}
    assert body["error"]["code"] == "NOT_READY"
    assert body["error"]["trace_id"] == response.headers["X-Trace-Id"]


def test_exception_details_are_not_returned() -> None:
    response = _client({"system_db": _boom, "business_db": _ok}).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["system_db"] == "error"
    assert "boom-secret-detail" not in response.text


def test_slow_check_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "READY_TIMEOUT_S", 0.1)

    def slow() -> bool:
        time.sleep(0.5)
        return True

    started = time.perf_counter()
    response = _client({"system_db": slow, "business_db": _ok}).get("/readyz")
    assert time.perf_counter() - started < 0.45
    assert response.status_code == 503
    assert response.json()["checks"]["system_db"] == "error"


def test_default_checks_use_both_databases() -> None:
    assert set(get_readiness_checks()) == {"system_db", "business_db"}
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"system_db": "error", "business_db": "error"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_readyz.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'ReadinessCheck'`.

- [ ] **Step 3: Implement `/readyz`**

Replace `src/text2sql/api/routes/health.py` with:

```python
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from text2sql.common.errors import NOT_READY, error_body
from text2sql.db import business, system

logger = logging.getLogger(__name__)

router = APIRouter()

ReadinessCheck = Callable[[], bool]

READY_TIMEOUT_S = 2.0

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="readyz")


def get_readiness_checks() -> dict[str, ReadinessCheck]:
    return {"system_db": system.ping, "business_db": business.ping}


def _run_check(name: str, check: ReadinessCheck) -> str:
    future = _executor.submit(check)
    try:
        passed = future.result(timeout=READY_TIMEOUT_S)
    except Exception:
        logger.warning("readiness_check_failed", extra={"check": name}, exc_info=True)
        return "error"
    return "ok" if passed else "error"


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(
    checks: Annotated[dict[str, ReadinessCheck], Depends(get_readiness_checks)],
) -> JSONResponse:
    results = {name: _run_check(name, check) for name, check in checks.items()}
    if all(result == "ok" for result in results.values()):
        return JSONResponse({"status": "ready", "checks": results})
    body = {
        "status": "not_ready",
        "checks": results,
        **error_body(NOT_READY, "Service not ready"),
    }
    return JSONResponse(body, status_code=503)
```

(A timed-out check keeps running in its executor thread until the driver's own timeout; the response is not delayed by it.)

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `uv run pytest tests/unit/test_readyz.py tests/unit/test_api_app.py -v`
Expected: `13 passed`.

- [ ] **Step 5: Write and run the live readiness tests (acceptance 3)**

Create `tests/integration/test_readyz_live.py`:

```python
import pytest
from fastapi.testclient import TestClient

from text2sql.api.app import create_app

pytestmark = pytest.mark.integration


def test_ready_with_live_databases(system_db_url: str, mysql_url: str) -> None:
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"system_db": "ok", "business_db": "ok"},
    }


def test_not_ready_when_mysql_is_down(system_db_url: str) -> None:
    # The autouse fixture points T2S_BUSINESS_DB_URL at 127.0.0.1:1 (nothing listens).
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"system_db": "ok", "business_db": "error"}
```

Run: `set -a && source .env && set +a && uv run pytest -m integration tests/integration/test_readyz_live.py -v`
Expected: `3 passed` (`test_ready_with_live_databases[mysql57]`, `[mysql80]`, `test_not_ready_when_mysql_is_down`).

- [ ] **Step 6: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, unit tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/text2sql/api/routes/health.py tests/unit/test_readyz.py tests/integration/test_readyz_live.py
git commit -m "feat(api): add readyz with per-check 2s timeout"
```

---

### Task 8: PostgreSQL job queue (F6 queue)

**Files:**
- Create: `src/text2sql/jobs/__init__.py`, `src/text2sql/jobs/queue.py`
- Test: `tests/unit/test_queue_rules.py`, `tests/integration/test_queue.py`

**Interfaces:**
- Consumes: `Job` (Task 5), `session_scope`, `get_sessionmaker` (Task 5), `get_trace_id`/`set_trace_id`/`reset_trace_id` (Task 2), fixture `migrated_db` (Task 5).
- Produces (`text2sql.jobs.queue`):
  - `MAX_ERROR_LENGTH = 2000`, `MAX_KIND_LENGTH = 64`
  - `class NonRetryableJobError(Exception)`
  - `class JobNotFoundError(LookupError)`
  - `backoff_seconds(attempts: int) -> int` (`2 ** attempts`)
  - `truncate_error(error: str) -> str` (first 2000 chars)
  - `enqueue(session: Session, kind: str, payload: dict[str, Any], *, max_attempts: int = 3, run_after: datetime | None = None) -> int`
  - `claim(session: Session, kinds: Sequence[str], lease_s: int) -> Job | None`
  - `complete(session: Session, job_id: int) -> None`
  - `fail(session: Session, job_id: int, error: str, *, retryable: bool = True) -> None`
  - All functions only use the given session; the caller commits.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_queue_rules.py`:

```python
import pytest
from sqlalchemy.orm import Session

from text2sql.jobs.queue import (
    MAX_ERROR_LENGTH,
    NonRetryableJobError,
    backoff_seconds,
    enqueue,
    truncate_error,
)


def test_backoff_is_two_to_the_attempts() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [2, 4, 8, 16]


def test_truncate_error() -> None:
    assert MAX_ERROR_LENGTH == 2000
    assert truncate_error("x" * 2500) == "x" * 2000
    assert truncate_error("short") == "short"


def test_non_retryable_is_an_exception() -> None:
    assert issubclass(NonRetryableJobError, Exception)


@pytest.mark.parametrize(
    ("kind", "max_attempts"),
    [("", 3), ("k" * 65, 3), ("noop", 0)],
)
def test_enqueue_rejects_invalid_arguments(kind: str, max_attempts: int) -> None:
    with pytest.raises(ValueError):
        enqueue(Session(), kind, {}, max_attempts=max_attempts)
```

(`Session()` without a bind is never used because validation happens first.)

- [ ] **Step 2: Write the failing integration tests**

Create `tests/integration/test_queue.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select, update

from text2sql.common.tracing import reset_trace_id, set_trace_id
from text2sql.db.models import Job
from text2sql.db.system import get_sessionmaker, session_scope
from text2sql.jobs.queue import (
    JobNotFoundError,
    claim,
    complete,
    enqueue,
    fail,
)

pytestmark = pytest.mark.integration


def _get(job_id: int) -> Job:
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        return job


def _make_due(job_id: int) -> None:
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(run_after=func.now() - timedelta(seconds=1))
        )


def _enqueue(max_attempts: int = 3) -> int:
    with session_scope() as session:
        return enqueue(session, "noop", {"n": 1}, max_attempts=max_attempts)


def test_enqueue_records_queued_job_and_trace_id(migrated_db: Engine) -> None:
    token = set_trace_id("trace-enqueue-01")
    try:
        job_id = _enqueue()
    finally:
        reset_trace_id(token)
    job = _get(job_id)
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.payload == {"n": 1}
    assert job.trace_id == "trace-enqueue-01"


def test_claim_marks_running_and_sets_lease(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        job = claim(session, ["noop"], lease_s=300)
        assert job is not None
        assert job.id == job_id
    job = _get(job_id)
    assert job.status == "running"
    assert job.attempts == 1
    assert job.locked_until is not None
    assert job.locked_until > datetime.now(UTC) + timedelta(seconds=250)


def test_claim_filters_kind_and_respects_run_after(migrated_db: Engine) -> None:
    with session_scope() as session:
        enqueue(
            session,
            "noop",
            {},
            run_after=datetime.now(UTC) + timedelta(hours=1),
        )
        enqueue(session, "other", {})
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is None
        assert claim(session, [], lease_s=300) is None


def test_claim_orders_by_run_after_then_id(migrated_db: Engine) -> None:
    first = _enqueue()
    second = _enqueue()
    with session_scope() as session:
        claimed = claim(session, ["noop"], lease_s=300)
        assert claimed is not None
        assert claimed.id == first
    with session_scope() as session:
        claimed = claim(session, ["noop"], lease_s=300)
        assert claimed is not None
        assert claimed.id == second


def test_concurrent_claims_skip_locked_rows(migrated_db: Engine) -> None:
    first = _enqueue()
    second = _enqueue()
    factory = get_sessionmaker()
    with factory() as session_a, factory() as session_b:
        job_a = claim(session_a, ["noop"], lease_s=300)
        job_b = claim(session_b, ["noop"], lease_s=300)
        assert job_a is not None
        assert job_b is not None
        assert {job_a.id, job_b.id} == {first, second}
        session_a.commit()
        session_b.commit()


def test_expired_lease_can_be_reclaimed(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is None
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(locked_until=func.now() - timedelta(seconds=1))
        )
    with session_scope() as session:
        job = claim(session, ["noop"], lease_s=300)
        assert job is not None
        assert job.id == job_id
    assert _get(job_id).attempts == 2


def test_complete(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        complete(session, job_id)
    job = _get(job_id)
    assert job.status == "succeeded"
    assert job.locked_until is None


def test_fail_retries_with_backoff_then_fails(migrated_db: Engine) -> None:
    job_id = _enqueue(max_attempts=2)
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    before = datetime.now(UTC)
    with session_scope() as session:
        fail(session, job_id, "RuntimeError: first")
    job = _get(job_id)
    assert job.status == "queued"
    assert job.locked_until is None
    assert job.last_error == "RuntimeError: first"
    assert job.run_after >= before + timedelta(seconds=1)  # 2^1 s backoff

    _make_due(job_id)
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        fail(session, job_id, "RuntimeError: second")
    job = _get(job_id)
    assert job.status == "failed"
    assert job.attempts == 2
    assert job.last_error == "RuntimeError: second"


def test_fail_non_retryable_fails_immediately(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        fail(session, job_id, "x" * 2500, retryable=False)
    job = _get(job_id)
    assert job.status == "failed"
    assert job.attempts == 1
    assert job.last_error == "x" * 2000


def test_unknown_job_raises(migrated_db: Engine) -> None:
    with session_scope() as session:
        with pytest.raises(JobNotFoundError):
            complete(session, 999_999)
        with pytest.raises(JobNotFoundError):
            fail(session, 999_999, "nope")


def test_job_count_is_isolated_per_test(migrated_db: Engine) -> None:
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(Job)).scalar_one() == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_queue_rules.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'text2sql.jobs'`.

- [ ] **Step 4: Implement the queue**

Create empty `src/text2sql/jobs/__init__.py`. Create `src/text2sql/jobs/queue.py`:

```python
"""PostgreSQL job queue (FOR UPDATE SKIP LOCKED). Callers own the transaction."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from text2sql.common.tracing import get_trace_id
from text2sql.db.models import Job

MAX_ERROR_LENGTH = 2000
MAX_KIND_LENGTH = 64


class NonRetryableJobError(Exception):
    """Raised by a handler when retrying is pointless; the job fails at once."""


class JobNotFoundError(LookupError):
    """The job id does not exist."""


def backoff_seconds(attempts: int) -> int:
    return int(2**attempts)


def truncate_error(error: str) -> str:
    return error[:MAX_ERROR_LENGTH]


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> int:
    if not kind or len(kind) > MAX_KIND_LENGTH:
        raise ValueError(f"kind must be 1..{MAX_KIND_LENGTH} characters")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    job = Job(
        kind=kind,
        payload=payload,
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        trace_id=get_trace_id(),
    )
    if run_after is not None:
        job.run_after = run_after
    session.add(job)
    session.flush()
    return job.id


def claim(session: Session, kinds: Sequence[str], lease_s: int) -> Job | None:
    if not kinds:
        return None
    candidate = aliased(Job, name="candidate")
    next_id = (
        select(candidate.id)
        .where(
            candidate.kind.in_(list(kinds)),
            or_(
                and_(candidate.status == "queued", candidate.run_after <= func.now()),
                and_(
                    candidate.status == "running",
                    candidate.locked_until < func.now(),
                ),
            ),
        )
        .order_by(candidate.run_after, candidate.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == next_id)
        .values(
            status="running",
            attempts=Job.attempts + 1,
            locked_until=func.now() + timedelta(seconds=lease_s),
            updated_at=func.now(),
        )
        .returning(Job)
    )
    return session.scalars(stmt).one_or_none()


def complete(session: Session, job_id: int) -> None:
    stmt = (
        update(Job)
        .where(Job.id == job_id)
        .values(status="succeeded", locked_until=None, updated_at=func.now())
        .returning(Job.id)
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        raise JobNotFoundError(job_id)


def fail(session: Session, job_id: int, error: str, *, retryable: bool = True) -> None:
    row = session.execute(
        select(Job.attempts, Job.max_attempts)
        .where(Job.id == job_id)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise JobNotFoundError(job_id)
    attempts, max_attempts = row
    values: dict[str, Any] = {
        "last_error": truncate_error(error),
        "locked_until": None,
        "updated_at": func.now(),
    }
    if retryable and attempts < max_attempts:
        values["status"] = "queued"
        values["run_after"] = func.now() + timedelta(seconds=backoff_seconds(attempts))
    else:
        values["status"] = "failed"
    session.execute(update(Job).where(Job.id == job_id).values(**values))
```

Notes: `candidate` is an alias so the subquery is not auto-correlated to the `UPDATE jobs` target; `now()` is the transaction start time, so each claim must run in its own short transaction (the worker in Task 9 does this).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_queue_rules.py -v`
Expected: `6 passed`.

Run: `set -a && source .env && set +a && uv run pytest -m integration tests/integration/test_queue.py -v`
Expected: `11 passed`.

- [ ] **Step 6: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, unit tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/text2sql/jobs tests/unit/test_queue_rules.py tests/integration/test_queue.py
git commit -m "feat(jobs): add postgres queue with skip-locked claim, backoff and non-retryable errors"
```

---

### Task 9: Worker loop, handler registry, and signals (F6 worker)

**Files:**
- Create: `src/text2sql/jobs/worker.py`, `apps/worker/main.py`
- Test: `tests/unit/test_worker.py`, `tests/integration/test_worker.py`

**Interfaces:**
- Consumes: `claim`, `complete`, `fail`, `NonRetryableJobError`, `enqueue` (Task 8); `session_scope` (Task 5); `get_settings` (Task 1); `configure_logging`, `set_trace_id`, `reset_trace_id`, `new_trace_id`, `get_trace_id` (Task 2); fixture `migrated_db` (Task 5).
- Produces (`text2sql.jobs.worker`):
  - `Handler = Callable[[dict[str, Any]], None]`
  - `register(kind: str) -> Callable[[Handler], Handler]` (raises `ValueError` on duplicate kind)
  - `registered_kinds() -> list[str]` (sorted)
  - built-in handler `noop` registered as kind `"noop"`
  - `run_once(*, lease_s: int) -> bool` (`False` when no job was claimed)
  - `run_worker(stop_event: threading.Event) -> None`
  - `install_signal_handlers(stop_event: threading.Event) -> None` (SIGINT, SIGTERM → `stop_event.set()`)
  - `main() -> None`; `python -m text2sql.jobs.worker` and `python apps/worker/main.py` both call it
  - Later specs register handlers with `@register("<kind>")` in their own `jobs.py` and add an import of that module to `main()` (out of scope here).

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_worker.py`:

```python
import re
import signal
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from text2sql.common.tracing import get_trace_id
from text2sql.jobs import worker
from text2sql.jobs.queue import NonRetryableJobError


@dataclass
class FakeJob:
    id: int
    kind: str
    payload: dict[str, Any]
    trace_id: str | None = None


@dataclass
class Calls:
    claimed_kinds: list[list[str]] = field(default_factory=list)
    completed: list[int] = field(default_factory=list)
    failed: list[tuple[int, str, bool]] = field(default_factory=list)


def _install(monkeypatch: pytest.MonkeyPatch, job: FakeJob | None) -> Calls:
    calls = Calls()

    @contextmanager
    def fake_scope() -> Iterator[object]:
        yield object()

    def fake_claim(
        session: object, kinds: Sequence[str], lease_s: int
    ) -> FakeJob | None:
        calls.claimed_kinds.append(list(kinds))
        return job

    def fake_complete(session: object, job_id: int) -> None:
        calls.completed.append(job_id)

    def fake_fail(
        session: object, job_id: int, error: str, *, retryable: bool = True
    ) -> None:
        calls.failed.append((job_id, error, retryable))

    monkeypatch.setattr(worker, "session_scope", fake_scope)
    monkeypatch.setattr(worker, "claim", fake_claim)
    monkeypatch.setattr(worker, "complete", fake_complete)
    monkeypatch.setattr(worker, "fail", fake_fail)
    return calls


def test_noop_is_registered_and_duplicates_rejected() -> None:
    assert "noop" in worker.registered_kinds()
    with pytest.raises(ValueError):
        worker.register("noop")(lambda payload: None)


def test_idle_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, None)
    assert worker.run_once(lease_s=30) is False
    assert calls.claimed_kinds == [worker.registered_kinds()]


def test_success_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setitem(worker._HANDLERS, "t_ok", seen.append)
    calls = _install(monkeypatch, FakeJob(1, "t_ok", {"a": 1}))
    assert worker.run_once(lease_s=30) is True
    assert seen == [{"a": 1}]
    assert calls.completed == [1]
    assert calls.failed == []


def test_non_retryable_error_fails_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(payload: dict[str, Any]) -> None:
        raise NonRetryableJobError("bad payload")

    monkeypatch.setitem(worker._HANDLERS, "t_bad", handler)
    calls = _install(monkeypatch, FakeJob(2, "t_bad", {}))
    worker.run_once(lease_s=30)
    assert calls.failed == [(2, "NonRetryableJobError: bad payload", False)]
    assert calls.completed == []


def test_other_error_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(payload: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(worker._HANDLERS, "t_boom", handler)
    calls = _install(monkeypatch, FakeJob(3, "t_boom", {}))
    worker.run_once(lease_s=30)
    assert calls.failed == [(3, "RuntimeError: boom", True)]


def test_missing_handler_fails_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, FakeJob(4, "ghost", {}))
    worker.run_once(lease_s=30)
    assert calls.failed == [(4, "no handler registered for kind 'ghost'", False)]


def test_restores_job_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setitem(
        worker._HANDLERS, "t_trace", lambda p: seen.append(get_trace_id())
    )
    _install(monkeypatch, FakeJob(5, "t_trace", {}, trace_id="trace-job-0001"))
    worker.run_once(lease_s=30)
    assert seen == ["trace-job-0001"]
    assert get_trace_id() is None


def test_generates_trace_id_when_job_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setitem(
        worker._HANDLERS, "t_trace", lambda p: seen.append(get_trace_id())
    )
    _install(monkeypatch, FakeJob(6, "t_trace", {}, trace_id=None))
    worker.run_once(lease_s=30)
    assert seen[0] is not None
    assert re.fullmatch(r"[0-9a-f]{32}", seen[0])


def test_run_worker_loops_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_JOB_POLL_INTERVAL_S", "0.01")
    stop = threading.Event()
    results = iter([True, True, False])
    count = 0

    def fake_run_once(*, lease_s: int) -> bool:
        nonlocal count
        count += 1
        result = next(results)
        if not result:
            stop.set()
        return result

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    worker.run_worker(stop)
    assert count == 3


def test_run_worker_survives_iteration_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_JOB_POLL_INTERVAL_S", "0.01")
    stop = threading.Event()
    count = 0

    def flaky_run_once(*, lease_s: int) -> bool:
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("database down")
        stop.set()
        return False

    monkeypatch.setattr(worker, "run_once", flaky_run_once)
    worker.run_worker(stop)
    assert count == 2


def test_signals_set_stop_event() -> None:
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    stop = threading.Event()
    try:
        worker.install_signal_handlers(stop)
        signal.raise_signal(signal.SIGTERM)
        assert stop.is_set()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_worker.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'worker' from 'text2sql.jobs'`.

- [ ] **Step 3: Implement the worker**

Create `src/text2sql/jobs/worker.py`:

```python
"""Job worker: `uv run python -m text2sql.jobs.worker`."""

import logging
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import Any

from text2sql.common.config import get_settings
from text2sql.common.logging import configure_logging
from text2sql.common.tracing import new_trace_id, reset_trace_id, set_trace_id
from text2sql.db.system import session_scope
from text2sql.jobs.queue import NonRetryableJobError, claim, complete, fail

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        if kind in _HANDLERS:
            raise ValueError(f"handler already registered for kind {kind!r}")
        _HANDLERS[kind] = handler
        return handler

    return decorator


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)


@register("noop")
def noop(payload: dict[str, Any]) -> None:
    logger.info("noop_job", extra={"payload_keys": sorted(payload)})


def _format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _record_failure(job_id: int, error: str, *, retryable: bool) -> None:
    with session_scope() as session:
        fail(session, job_id, error, retryable=retryable)


def run_once(*, lease_s: int) -> bool:
    with session_scope() as session:
        job = claim(session, registered_kinds(), lease_s)
        if job is None:
            return False
        job_id, kind, trace_id = job.id, job.kind, job.trace_id
        payload = dict(job.payload)

    token = set_trace_id(trace_id or new_trace_id())
    started = time.perf_counter()
    log_extra: dict[str, Any] = {"job_id": job_id, "kind": kind}
    try:
        handler = _HANDLERS.get(kind)
        if handler is None:
            logger.error("job_handler_missing", extra=log_extra)
            _record_failure(
                job_id, f"no handler registered for kind {kind!r}", retryable=False
            )
            return True
        try:
            handler(payload)
        except NonRetryableJobError as exc:
            logger.warning("job_failed_non_retryable", extra=log_extra, exc_info=True)
            _record_failure(job_id, _format_error(exc), retryable=False)
        except Exception as exc:
            logger.exception("job_failed", extra=log_extra)
            _record_failure(job_id, _format_error(exc), retryable=True)
        else:
            with session_scope() as session:
                complete(session, job_id)
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.info(
                "job_succeeded", extra={**log_extra, "duration_ms": duration_ms}
            )
    finally:
        reset_trace_id(token)
    return True


def run_worker(stop_event: threading.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            processed = run_once(lease_s=settings.job_lease_s)
        except Exception:
            logger.exception("worker_iteration_failed")
            processed = False
        if not processed:
            stop_event.wait(settings.job_poll_interval_s)


def install_signal_handlers(stop_event: threading.Event) -> None:
    def request_stop(signum: int, frame: FrameType | None) -> None:
        logger.info("worker_stop_requested", extra={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    logger.info("worker_started", extra={"kinds": registered_kinds()})
    run_worker(stop_event)
    logger.info("worker_stopped")


if __name__ == "__main__":
    # Re-import under the package name so handlers registered through
    # `text2sql.jobs.worker.register` and this entry point share one registry.
    from text2sql.jobs.worker import main as package_main

    package_main()
```

Create `apps/worker/main.py`:

```python
"""Worker entry point; equivalent to `uv run python -m text2sql.jobs.worker`."""

from text2sql.jobs.worker import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `uv run pytest tests/unit/test_worker.py -v`
Expected: `11 passed`.

- [ ] **Step 5: Write the integration tests (acceptance 7)**

Create `tests/integration/test_worker.py`:

```python
import threading
from collections import Counter
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, update

from text2sql.common.tracing import get_trace_id, reset_trace_id, set_trace_id
from text2sql.db.models import Job
from text2sql.db.system import session_scope
from text2sql.jobs import worker
from text2sql.jobs.queue import NonRetryableJobError, enqueue

pytestmark = pytest.mark.integration


def _make_due(job_id: int) -> None:
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(run_after=func.now() - timedelta(seconds=1))
        )


def _get(job_id: int) -> Job:
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        return job


def test_200_noop_jobs_4_threads_each_runs_exactly_once(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    executed: Counter[int] = Counter()
    lock = threading.Lock()

    def counting_noop(payload: dict[str, Any]) -> None:
        with lock:
            executed[int(payload["n"])] += 1

    monkeypatch.setitem(worker._HANDLERS, "noop", counting_noop)
    with session_scope() as session:
        for n in range(200):
            enqueue(session, "noop", {"n": n})

    def drain() -> None:
        while worker.run_once(lease_s=300):
            pass

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert executed == Counter({n: 1 for n in range(200)})
    with session_scope() as session:
        rows = session.execute(
            select(Job.status, Job.attempts, func.count()).group_by(
                Job.status, Job.attempts
            )
        ).all()
    assert [tuple(row) for row in rows] == [("succeeded", 1, 200)]


def test_failing_job_retries_then_fails(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_fails(payload: dict[str, Any]) -> None:
        raise RuntimeError("still broken")

    monkeypatch.setitem(worker._HANDLERS, "t_retry", always_fails)
    with session_scope() as session:
        job_id = enqueue(session, "t_retry", {}, max_attempts=3)

    for expected_attempts in (1, 2):
        assert worker.run_once(lease_s=300) is True
        job = _get(job_id)
        assert (job.status, job.attempts) == ("queued", expected_attempts)
        assert worker.run_once(lease_s=300) is False  # backoff not yet due
        _make_due(job_id)

    assert worker.run_once(lease_s=300) is True
    job = _get(job_id)
    assert (job.status, job.attempts) == ("failed", 3)
    assert job.last_error == "RuntimeError: still broken"


def test_non_retryable_job_fails_on_first_attempt(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rejects(payload: dict[str, Any]) -> None:
        raise NonRetryableJobError("datasource mismatch")

    monkeypatch.setitem(worker._HANDLERS, "t_reject", rejects)
    with session_scope() as session:
        job_id = enqueue(session, "t_reject", {}, max_attempts=3)
    assert worker.run_once(lease_s=300) is True
    job = _get(job_id)
    assert (job.status, job.attempts) == ("failed", 1)
    assert job.last_error == "NonRetryableJobError: datasource mismatch"


def test_worker_restores_enqueue_trace_id(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []
    monkeypatch.setitem(
        worker._HANDLERS, "t_trace", lambda p: seen.append(get_trace_id())
    )
    token = set_trace_id("trace-worker-001")
    try:
        with session_scope() as session:
            enqueue(session, "t_trace", {})
    finally:
        reset_trace_id(token)
    assert worker.run_once(lease_s=300) is True
    assert seen == ["trace-worker-001"]
```

Run: `set -a && source .env && set +a && uv run pytest -m integration tests/integration/test_worker.py -v`
Expected: `4 passed`.

- [ ] **Step 6: Manual worker smoke check**

Run: `uv run python -m text2sql.jobs.worker` (in a second terminal), then press Ctrl-C.
Expected: JSON lines `worker_started` (with `"kinds": ["noop"]`), then `worker_stop_requested` and `worker_stopped`; exit code 0.

- [ ] **Step 7: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, unit tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/text2sql/jobs/worker.py apps/worker/main.py tests/unit/test_worker.py tests/integration/test_worker.py
git commit -m "feat(jobs): add worker loop with handler registry, trace restore and graceful shutdown"
```

---

### Task 10: LangGraph mount point and temporary echo route (F7)

**Files:**
- Create: `src/text2sql/agent/__init__.py`, `src/text2sql/agent/graph.py`, `src/text2sql/api/routes/agent.py`
- Modify: `src/text2sql/api/app.py` (`build_api_v1_router` and imports, shown below)
- Test: `tests/unit/test_agent_echo.py`

**Interfaces:**
- Consumes: `create_app`, `build_api_v1_router`, `API_V1_PREFIX` (Task 3); `get_trace_id` (Task 2).
- Produces:
  - `text2sql.agent.graph.EchoState` (`TypedDict`: `question: str`, `answer: str`)
  - `text2sql.agent.graph.build_graph() -> CompiledStateGraph[EchoState, None, EchoState, EchoState]` (single node `"echo"`)
  - `text2sql.agent.graph.run_echo(question: str) -> str`
  - `text2sql.api.routes.agent.router` (`APIRouter(prefix="/agent")`), `EchoRequest`, `EchoResponse`; mounted at `POST /api/v1/agent/echo`
  - Spec 08 deletes `routes/agent.py`, the echo node and `tests/unit/test_agent_echo.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_agent_echo.py`:

```python
from fastapi.testclient import TestClient

from text2sql.agent.graph import build_graph, run_echo
from text2sql.api.app import create_app

QUESTION = "最近30天各行业工单数"


def test_graph_copies_question_to_answer() -> None:
    result = build_graph().invoke({"question": QUESTION, "answer": ""})
    assert result["answer"] == QUESTION
    assert run_echo(QUESTION) == QUESTION


def test_echo_endpoint() -> None:
    response = TestClient(create_app()).post(
        "/api/v1/agent/echo", json={"question": QUESTION}
    )
    assert response.status_code == 200
    assert response.json() == {
        "answer": QUESTION,
        "trace_id": response.headers["X-Trace-Id"],
    }


def test_unprefixed_path_is_not_found() -> None:
    response = TestClient(create_app()).post("/v1/agent/echo", json={"question": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_missing_question_is_validation_error() -> None:
    response = TestClient(create_app()).post("/api/v1/agent/echo", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_agent_echo.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'text2sql.agent'`.

- [ ] **Step 3: Implement the graph**

Create empty `src/text2sql/agent/__init__.py`. Create `src/text2sql/agent/graph.py`:

```python
"""Temporary LangGraph mount check (Spec 01 F7). Spec 08 replaces this graph."""

from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


class EchoState(TypedDict):
    question: str
    answer: str


EchoGraph = CompiledStateGraph[EchoState, None, EchoState, EchoState]


def echo_node(state: EchoState) -> dict[str, str]:
    return {"answer": state["question"]}


def build_graph() -> EchoGraph:
    graph: StateGraph[EchoState, None, EchoState, EchoState] = StateGraph(EchoState)
    graph.add_node("echo", echo_node)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph() -> EchoGraph:
    return build_graph()


def run_echo(question: str) -> str:
    result = _compiled_graph().invoke({"question": question, "answer": ""})
    answer = result["answer"]
    if not isinstance(answer, str):
        raise TypeError("echo graph returned a non-string answer")
    return answer
```

`StateGraph`/`CompiledStateGraph` take four type parameters (`StateT, ContextT, InputT, OutputT`) in the langgraph version resolved in Task 1 (checked against langgraph 1.2.x). If mypy reports a different arity, run `uv run python -c "from langgraph.graph.state import CompiledStateGraph as C; print(C.__parameters__)"` and adjust the `EchoGraph` alias and the `graph` annotation to match; do not use `Any`.

- [ ] **Step 4: Implement the route and mount it under `/api/v1`**

Create `src/text2sql/api/routes/agent.py`:

```python
"""Temporary mount check: POST /api/v1/agent/echo. Deleted by Spec 08."""

from fastapi import APIRouter
from pydantic import BaseModel

from text2sql.agent.graph import run_echo
from text2sql.common.tracing import get_trace_id

router = APIRouter(prefix="/agent", tags=["agent"])


class EchoRequest(BaseModel):
    question: str


class EchoResponse(BaseModel):
    answer: str
    trace_id: str


@router.post("/echo")
def echo(request: EchoRequest) -> EchoResponse:
    answer = run_echo(request.question)
    return EchoResponse(answer=answer, trace_id=get_trace_id() or "")
```

In `src/text2sql/api/app.py`, change the routes import line and `build_api_v1_router`:

```python
from text2sql.api.routes import agent, health
```

```python
def build_api_v1_router() -> APIRouter:
    router = APIRouter(prefix=API_V1_PREFIX)
    router.include_router(agent.router)
    return router
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_agent_echo.py tests/unit/test_api_app.py -v`
Expected: `12 passed`.

- [ ] **Step 6: Quality gates**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
Expected: all clean, unit tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/text2sql/agent src/text2sql/api/routes/agent.py src/text2sql/api/app.py tests/unit/test_agent_echo.py
git commit -m "feat(agent): add echo graph and temporary /api/v1/agent/echo mount check"
```

---

### Task 11: Developer documentation and full acceptance run (acceptance 1–10)

**Files:**
- Modify: `README.md` (append section), `AGENTS.md` (replace the "Build, lint, and test commands will be documented here…" bullet, or append if absent)

**Interfaces:**
- Consumes: everything above.
- Produces: documented commands that match this plan exactly.

- [ ] **Step 1: Add the README section**

Append to `README.md`:

````markdown
## 本地开发

> 下面所有账号密码只用于本机 Docker Compose，**不得用于任何其他环境**。

1. 准备配置：`cp .env.example .env`
2. 启动数据库（PostgreSQL 16、MySQL 5.7、MySQL 8.0）：
   ```bash
   docker compose -f deployment/compose.yaml up -d
   docker compose -f deployment/compose.yaml ps   # 三个服务都应为 (healthy)
   ```
   Apple Silicon 上 MySQL 5.7 以 amd64 模拟运行，首次启动较慢；日常开发可只起 `pg mysql80`。
   应用服务（`api`、`worker`）将由 Spec 09 放在 profile `app` 中，本期只起数据库。
3. 运行迁移：`uv run alembic upgrade head`（回滚：`uv run alembic downgrade base`）
4. 启动 API：`uv run uvicorn apps.api.main:app --no-access-log`，检查 `curl -i localhost:8000/healthz` 与 `curl -i localhost:8000/readyz`
5. 启动 worker：`uv run python -m text2sql.jobs.worker`（Ctrl-C 处理完当前任务后退出）
6. 测试：
   ```bash
   uv run pytest                                          # 单元测试（不需要数据库）
   set -a && source .env && set +a && uv run pytest -m integration   # 集成测试（需要 Compose）
   ```
   集成测试读取 `T2S_TEST_SYSTEM_DB_URL`、`T2S_TEST_MYSQL57_URL`、`T2S_TEST_MYSQL80_URL`，未设置时自动跳过。
````

- [ ] **Step 2: Add the AGENTS.md section**

In `AGENTS.md`, under "Testing and evaluation", replace the bullet that says build/lint/test commands will be documented later with:

```markdown
- Commands (Python 3.12 via uv; add dependencies only with `uv add`):
  - Lint/format/types: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`
  - Unit tests (no databases): `uv run pytest`
  - Local databases: `docker compose -f deployment/compose.yaml up -d` (app services live under `--profile app`, added by Spec 09)
  - Migrations: `uv run alembic upgrade head`
  - API: `uv run uvicorn apps.api.main:app --no-access-log`; worker: `uv run python -m text2sql.jobs.worker`
  - Integration tests: `set -a && source .env && set +a && uv run pytest -m integration` (tests skip when `T2S_TEST_*` URLs are unset)
- The business MySQL account `t2s_ro` (`T2S_BUSINESS_DB_URL`) is only for metadata collection and `/readyz`; never execute user SQL with it.
```

If the bullet does not exist, append the same text at the end of the "Testing and evaluation" section.

- [ ] **Step 3: Full acceptance run**

Run each and compare with the expected result (spec 01 §5 numbering):

1. `docker compose -f deployment/compose.yaml up -d && docker compose -f deployment/compose.yaml ps` → `pg`, `mysql57`, `mysql80` all `(healthy)`.
2. `uv run alembic downgrade base && uv run alembic upgrade head` → exit 0; `jobs` exists (also covered by `tests/integration/test_migrations.py`).
3. In one terminal `uv run uvicorn apps.api.main:app --no-access-log`; in another:
   - `curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/healthz` → `200`
   - `curl -s localhost:8000/readyz` → `{"status":"ready","checks":{"system_db":"ok","business_db":"ok"}}` (`.env` points at mysql80; to check 5.7, restart the API with `T2S_BUSINESS_DB_URL` set to the 3307 URL from `.env.example` → same result)
   - `docker compose -f deployment/compose.yaml stop mysql80` then `curl -s -w '\n%{http_code}\n' localhost:8000/readyz` → body with `"business_db":"error"` and `503`; then `docker compose -f deployment/compose.yaml start mysql80`.
4. `curl -si -H 'X-Trace-Id: manual-check-01' localhost:8000/healthz | grep -i x-trace-id` → `x-trace-id: manual-check-01`; the API terminal shows the `http_request` line with `"trace_id": "manual-check-01"` (automated in `test_all_log_lines_of_a_request_share_trace_id`).
5. Covered by `test_uncaught_exception_hides_details`.
6. Covered by `tests/integration/test_business_db_readonly.py` and `tests/unit/test_business_db.py`.
7. Covered by `tests/integration/test_worker.py` and `tests/integration/test_queue.py`.
8. `curl -s -X POST localhost:8000/api/v1/agent/echo -H 'Content-Type: application/json' -d '{"question":"你好"}'` → `{"answer":"你好","trace_id":"<32 hex>"}`; `curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/v1/agent/echo` → `404`.
9. `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest && set -a && source .env && set +a && uv run pytest -m integration` → all pass; integration summary shows no skips (all three URLs set).
10. README and AGENTS.md updated (Steps 1–2).

- [ ] **Step 4: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: document local compose, migrations, api, worker and test commands"
```

---

## Self-Review

### 1. Spec coverage (spec 01 sections → tasks)

| Spec item | Task(s) |
| --- | --- |
| F1 Settings fields, `T2S_` prefix, `.env`, `lru_cache`, `SecretStr`, `.env.example`, `.env` ignored | 1, 4 |
| F2 JSON logging fields, `trace_id` context var helpers, middleware rules, `http_request` log, job trace restore | 2, 3, 8 (`enqueue` stores trace), 9 (worker restores) |
| F3 `create_app`, `/healthz`, `/readyz` (2 s each, 200/503, no details), unified error body, 500 message, 422, `/api/v1` prefix rule | 3, 7 |
| F4 sync SQLAlchemy + psycopg, `get_engine`, `session_scope`, Alembic reading `system_db_url`, reversible migration, only `jobs` | 5 |
| F5 PyMySQL, read-only session, `connect_timeout`/`read_timeout` = `business_db_timeout_s`, only `ping`/`server_version`, `t2s_ro` SELECT only and not for user queries | 4, 6, 11 (AGENTS.md rule) |
| F6 `jobs` table + index, `enqueue`/`claim`/`complete`/`fail(retryable)`, `NonRetryableJobError`, backoff, 2000-char error, `register`, `run_worker`, signals, `noop` | 5, 8, 9 |
| F7 echo graph + `POST /api/v1/agent/echo` | 10 |
| F8 three services, ports, healthchecks, volumes, init scripts, `_t2s_probe`, amd64 platform, profile `app` convention, no Dockerfile, credentials note | 4, 11 |
| F9 `integration` marker, default unit-only run, env URLs with skip, separate test DB, cleanup | 1, 4, 5 |
| §3 `apps/api/main.py`, worker via `python -m text2sql.jobs.worker` | 3, 9 |
| §5 acceptance 1–10 | 11 (Step 3 maps each item) |
| §6 deps `sqlalchemy`, `alembic`, `httpx` runtime, no dev additions | 1 |

### 2. Placeholder scan

Searched the plan for "TBD", "TODO", "implement later", "similar to Task", "add appropriate": none. Every code step contains full file contents or the exact replaced function.

### 3. Type and name consistency

- `get_settings`, `Settings` (1) → used in 3, 5, 6, 9 with the same names.
- `set_trace_id` returns `Token[str | None]`; `reset_trace_id(token)` used in 3, 8 tests, 9.
- `error_body(code, message)` and `NOT_READY` (3) → used in 7; `INTERNAL_ERROR` → middleware (3).
- `system.get_engine` / `get_sessionmaker` / `session_scope` / `ping` (5) → conftest (5, 6), queue tests (8), worker (9), readyz (7).
- `business.get_business_engine` / `business_connect_args` / `ping` / `server_version` (6) → conftest (6), readyz (7).
- `Job` attributes (5) → queue (8) and worker tests (9).
- `claim(session, kinds, lease_s)`, `complete(session, job_id)`, `fail(session, job_id, error, *, retryable=True)` (8) → fakes in `tests/unit/test_worker.py` mirror these signatures (9).
- `build_api_v1_router` (3) is the single place where `/api/v1` routers are mounted (10).
- Fixtures `system_db_url`, `mysql_url`, `mysql_target` (4) and `alembic_cfg`, `migrated_db` (5) are used with these names in 5–9.
