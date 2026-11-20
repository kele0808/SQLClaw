# Spec 01：Foundation 工程基础

- 状态：已确认（2026-11-20）
- 日期：2026-11-20
- 对应架构文档：`docs/architecture.md` 第三章（技术选型）、第十八章（目录）、第二十一章（部署）、第二十三章（第 1 周）
- 依赖：无（第一个子项目）
- 被依赖：02～09 全部

## 1. 目标

搭好后续所有子项目共用的运行底座：配置、日志与追踪、API 骨架、数据库访问与迁移、PostgreSQL 任务队列、LangGraph 挂载点、本地 Docker Compose 环境和测试分层。**本期不包含任何 Text2SQL 业务逻辑。**

## 2. 范围

### 2.1 本期做

| 编号 | 能力 | 说明 |
| --- | --- | --- |
| F1 | 配置 | pydantic-settings，环境变量 + `.env` |
| F2 | 日志与追踪 | JSON 结构化日志，请求级 `trace_id` 自动注入 |
| F3 | API 骨架 | FastAPI 应用、`/healthz`、`/readyz`、统一错误响应；业务路由统一前缀 `/api/v1`（架构第二十章） |
| F4 | 系统库访问 | PostgreSQL，SQLAlchemy 2.0 + Alembic |
| F5 | 业务库访问 | MySQL 只读连接（`t2s_ro`），只用于连通检查与元数据采集，不执行用户查询 |
| F6 | 任务队列 | 基于 PostgreSQL 的 `FOR UPDATE SKIP LOCKED` 队列 + worker 循环 |
| F7 | LangGraph 挂载点 | 一个空图，经临时接口 `POST /api/v1/agent/echo` 跑通输入到输出（Spec 08 删除） |
| F8 | 本地环境 | Docker Compose：默认只起 PG 16、MySQL 5.7、MySQL 8.0；约定应用服务放在可选 profile `app` 中（服务本身由 Spec 09 加入） |
| F9 | 测试分层 | 单元测试不依赖数据库；集成测试用 `integration` 标记 |

### 2.2 本期不做

- 元数据采集与快照（Spec 02）
- 合成工单库的表结构与数据（Spec 03）
- 鉴权、用户与租户（Spec 07）
- 前端（Spec 09）
- 用户查询的执行与租户账号（Spec 07 网关）
- 应用镜像（Dockerfile）与 Compose 中的 `api`、`worker` 服务（Spec 09）
- OpenTelemetry、Redis、Celery（架构文档 23.2 阶段 3）
- GitHub Actions CI（gh 凭证尚无 workflow 权限，补上后单独加）

## 3. 目录与文件

代码位于 `src/text2sql/`（架构文档第十八章中的 `src/<module>/` 对应 `src/text2sql/<module>/`）。

```
src/text2sql/
├── common/
│   ├── config.py          # F1 Settings
│   ├── logging.py         # F2 日志配置
│   ├── tracing.py         # F2 trace_id 上下文
│   └── errors.py          # F3 AppError 与错误码
├── api/
│   ├── app.py             # F3 create_app()
│   ├── middleware.py      # F2 TraceIdMiddleware
│   └── routes/
│       ├── health.py      # F3 /healthz /readyz
│       └── agent.py       # F7 /api/v1/agent/echo（临时，Spec 08 删除）
├── db/
│   ├── system.py          # F4 PG engine / session
│   ├── business.py        # F5 MySQL 只读 engine
│   └── models/
│       ├── base.py        # F4 DeclarativeBase
│       └── jobs.py        # F6 Job 模型
├── jobs/
│   ├── queue.py           # F6 enqueue / claim / complete / fail
│   └── worker.py          # F6 worker 循环与 handler 注册
└── agent/
    └── graph.py           # F7 空图
migrations/                # F4 Alembic
deployment/compose.yaml    # F8
.env.example               # F1
tests/unit/ …
tests/integration/ …
```

`apps/api/main.py` 与 `apps/worker/` 在本期只放启动入口：`apps/api/main.py` 调用 `create_app()`；worker 通过 `python -m text2sql.jobs.worker` 启动。

## 4. 详细设计

### 4.1 F1 配置

`text2sql.common.config.Settings`（`BaseSettings`，前缀 `T2S_`，读取 `.env`）：

| 字段 | 环境变量 | 类型 | 默认值 |
| --- | --- | --- | --- |
| `env` | `T2S_ENV` | `Literal["dev", "test", "prod"]` | `"dev"` |
| `log_level` | `T2S_LOG_LEVEL` | `str` | `"INFO"` |
| `system_db_url` | `T2S_SYSTEM_DB_URL` | `SecretStr` | 无，必填 |
| `business_db_url` | `T2S_BUSINESS_DB_URL` | `SecretStr` | 无，必填 |
| `business_db_timeout_s` | `T2S_BUSINESS_DB_TIMEOUT_S` | `int` | `5`（只作用于 `business_db_url` 连接，见 4.5） |
| `job_poll_interval_s` | `T2S_JOB_POLL_INTERVAL_S` | `float` | `1.0` |
| `job_lease_s` | `T2S_JOB_LEASE_S` | `int` | `300` |

- `get_settings()` 用 `functools.lru_cache` 返回单例；测试通过清缓存覆盖。
- 连接串用 `SecretStr`，日志与错误信息中永远不输出明文。
- `business_db_url` 指向只读账号 `t2s_ro`（能看到全部租户的数据），只供元数据采集（Spec 02）与 `/readyz` 使用；用户查询使用 Spec 07 定义的租户连接串 `tenant_db_urls`，不使用本字段。
- 仓库提供 `.env.example`，指向 Compose 中的本地库；`.env` 已在 `.gitignore`。

### 4.2 F2 日志与追踪

- 使用标准库 `logging` + 自定义 JSON Formatter（不引入额外日志库）。每条日志字段：`ts`（ISO8601，UTC）、`level`、`logger`、`msg`、`trace_id`、以及 `extra` 中的键。
- `trace_id` 存在 `contextvars.ContextVar` 中（`text2sql.common.tracing`），提供 `get_trace_id()`、`set_trace_id()`、`new_trace_id()`（32 位十六进制，`uuid4().hex`）。
- `TraceIdMiddleware`：读取请求头 `X-Trace-Id`；若存在且匹配 `^[A-Za-z0-9-]{8,64}$` 则沿用，否则生成新的；写入响应头 `X-Trace-Id`；请求结束后重置上下文。
- 请求日志：每个请求结束时输出一条 `http_request`，含方法、路径、状态码、耗时毫秒。
- 任务队列中的 Job 记录创建时的 `trace_id`，worker 执行时恢复该 `trace_id`，保证异步链路可追溯。

### 4.3 F3 API 骨架

- `create_app() -> FastAPI` 组装中间件、路由和异常处理器。
- `GET /healthz`：进程存活，始终返回 `200 {"status": "ok"}`，不访问数据库。
- `GET /readyz`：分别对 PG 和 MySQL（`business_db_url`，即 `t2s_ro`）执行 `SELECT 1`（各自超时 2 秒）。
  - 全部成功：`200 {"status": "ready", "checks": {"system_db": "ok", "business_db": "ok"}}`
  - 任一失败：`503`，失败项为 `"error"`，不返回异常详情。
- 统一错误响应（所有非 2xx）：

```json
{"error": {"code": "INTERNAL_ERROR", "message": "…", "trace_id": "…"}}
```

- `AppError(code: str, message: str, status_code: int)` 为业务异常基类；未捕获异常统一返回 `500 INTERNAL_ERROR`，消息固定为 `"Internal server error"`，堆栈只进日志。
- 请求校验失败返回 `422 VALIDATION_ERROR`。
- 路径约定：`/healthz`、`/readyz` 不带前缀；其余所有 JSON API 路由挂在 `APIRouter(prefix="/api/v1")` 下（架构第二十章），后续 Spec 新增的 API 都使用该前缀。唯一例外是 Spec 09 的静态查询页面，挂载在 `/ui/`，不属于 API。

### 4.4 F4 系统库（PostgreSQL）

- SQLAlchemy 2.0，同步 API（`create_engine` + `sessionmaker`）。驱动 `psycopg`（v3），URL 形如 `postgresql+psycopg://…`。
- `text2sql.db.system` 提供 `get_engine()` 与上下文管理器 `session_scope()`（成功提交、异常回滚）。
- Alembic 位于 `migrations/`，`env.py` 从 `Settings.system_db_url` 读取连接，`target_metadata` 指向 `Base.metadata`。
- 迁移文件必须可升级也可降级；本期唯一迁移为创建 `jobs` 表。
- 选择同步 API 的理由：MVP 并发低，同步代码更易测试；如后续需要异步，在独立 spec 中评估。

### 4.5 F5 业务库（MySQL，只读）

- 驱动 PyMySQL，URL 形如 `mysql+pymysql://…`。
- `text2sql.db.business.get_business_engine()` 基于 `business_db_url` 创建 engine，连接在建立时执行 `SET SESSION TRANSACTION READ ONLY`，并把 PyMySQL 的 `connect_timeout` 与 `read_timeout` 设为 `business_db_timeout_s`。
- `business_db_timeout_s` 的含义：`t2s_ro` 连接的建立与单条读取超时，用于连通检查和元数据采集（`information_schema` 查询）。它**不是**用户查询超时：网关的语句超时 `query_timeout_s`、EXPLAIN 超时 `explain_timeout_s` 以及租户连接的超时由 Spec 07 定义，与本字段相互独立。`/readyz` 另有固定 2 秒上限。
- 本期只暴露 `ping() -> bool`（执行 `SELECT 1`）和 `server_version() -> str`（`SELECT VERSION()`）。完整的执行网关在 Spec 07。
- 本地 Compose 中为 MySQL 创建只授予 `SELECT` 的账号 `t2s_ro`。该账号只用于元数据采集与 `/readyz`；用户查询一律经 Spec 07 网关、使用按租户隔离的账号（`t2s_t{T}`，由 Spec 03 创建），网关永远不用 `t2s_ro` 执行用户 SQL。

### 4.6 F6 任务队列

`jobs` 表：

| 列 | 类型 | 约束 / 默认 |
| --- | --- | --- |
| `id` | `BIGINT` | 主键，自增 |
| `kind` | `VARCHAR(64)` | 非空 |
| `payload` | `JSONB` | 非空，默认 `{}` |
| `status` | `VARCHAR(16)` | 非空，取值 `queued` / `running` / `succeeded` / `failed` |
| `attempts` | `INT` | 非空，默认 0 |
| `max_attempts` | `INT` | 非空，默认 3 |
| `run_after` | `TIMESTAMPTZ` | 非空，默认 `now()` |
| `locked_until` | `TIMESTAMPTZ` | 可空 |
| `last_error` | `TEXT` | 可空 |
| `trace_id` | `VARCHAR(64)` | 可空 |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

索引：`(status, run_after)`。

`text2sql.jobs.queue` 接口（均接收 `Session`，由调用方控制事务）：

```python
def enqueue(session: Session, kind: str, payload: dict[str, Any], *,
            max_attempts: int = 3, run_after: datetime | None = None) -> int: ...
def claim(session: Session, kinds: Sequence[str], lease_s: int) -> Job | None: ...
def complete(session: Session, job_id: int) -> None: ...
def fail(session: Session, job_id: int, error: str, *, retryable: bool = True) -> None: ...

class NonRetryableJobError(Exception):
    """handler 抛出表示重试无意义（如参数错误、配置不匹配），任务直接置为 failed。"""
```

语义：

- `enqueue`：写入 `queued`，记录当前 `trace_id`。
- `claim`：在一条语句中选出一条可执行任务并加锁，条件为 `kind` 匹配，且满足以下之一：`status = 'queued' AND run_after <= now()`，或 `status = 'running' AND locked_until < now()`（租约过期，视为 worker 崩溃）。按 `run_after, id` 排序，使用 `FOR UPDATE SKIP LOCKED LIMIT 1`；被选中的任务置为 `running`，`attempts += 1`，`locked_until = now() + lease_s`。
- `complete`：置为 `succeeded`，清空 `locked_until`。
- `fail`：若 `retryable` 且 `attempts < max_attempts`，置回 `queued`，`run_after = now() + 2^attempts 秒`（指数退避）；否则置为 `failed`。记录 `last_error`（截断到 2000 字符）。

`text2sql.jobs.worker`：

- `register(kind: str)` 装饰器注册 handler，签名 `handler(payload: dict[str, Any]) -> None`。
- `run_worker(stop_event: threading.Event)` 循环：领取任务，恢复 `trace_id`，调用 handler，成功则 `complete`；handler 抛出 `NonRetryableJobError` 时 `fail(..., retryable=False)`，直接置为 `failed`，不论剩余次数；其他异常 `fail(...)`，按退避重试；无任务时休眠 `job_poll_interval_s`。收到 SIGINT / SIGTERM 时处理完当前任务后退出。
- 本期注册一个内置 `kind = "noop"` 的 handler 用于测试。

### 4.7 F7 LangGraph 挂载点

- `text2sql.agent.graph.build_graph()` 返回编译后的 `StateGraph`，State 为 `TypedDict`：`{"question": str, "answer": str}`，只有一个节点 `echo`，将 `question` 原样写入 `answer`。
- `POST /api/v1/agent/echo`，请求 `{"question": "…"}`，响应 `{"answer": "…", "trace_id": "…"}`。
- 该节点与接口是临时的挂载检查，用于验证路由前缀、`trace_id` 与 LangGraph 调用方式；Spec 08 用真实工作流替换空图，并删除该接口及其测试（正式查询接口为 Spec 09 的 `POST /api/v1/query`）。

### 4.8 F8 本地环境

`deployment/compose.yaml`：

| 服务 | 镜像 | 宿主端口 | 用途 |
| --- | --- | --- | --- |
| `pg` | `pgvector/pgvector:pg16` | 5432 | 系统库（预装 pgvector，供后续 spec 使用） |
| `mysql57` | `mysql:5.7` | 3307 | 业务库，5.7 方言 |
| `mysql80` | `mysql:8.0` | 3308 | 业务库，8.0 方言 |

- 每个服务配置 healthcheck；数据用命名卷持久化。
- 本期 compose 只包含上表三个数据库服务，`docker compose -f deployment/compose.yaml up -d` 只起数据库，开发时 API 与 worker 在宿主机用 `uv run` 启动。
- Profile 约定：应用服务（`api`、`worker`）统一放在 `profiles: [app]` 中，默认不启动，`--profile app` 时与数据库一起启动（架构第二十一章的 MVP 部署）。这两个服务与其镜像由 Spec 09 加入；本期不建 Dockerfile。
- MySQL 初始化脚本放在 `deployment/mysql/init/`，创建库 `t2s_business` 和只读账号 `t2s_ro`（仅 `SELECT`）。本期库内只建一张 `_t2s_probe` 表用于连通测试，真实表结构由 Spec 03 提供。
- `mysql:5.7` 官方镜像没有 arm64 版本，在 Apple Silicon 上为该服务设置 `platform: linux/amd64`（通过模拟运行，速度较慢，但只用于方言兼容测试）。
- 账号密码只用于本地，写在 compose 与 `.env.example` 中，并在 README 中注明不得用于任何其他环境。

### 4.9 F9 测试

- pytest 标记 `integration`，在 `pyproject.toml` 中声明；默认运行 `uv run pytest` 只跑单元测试（`-m "not integration"`），`uv run pytest -m integration` 跑集成测试（需先 `docker compose -f deployment/compose.yaml up -d`）。
- 集成测试读取 `T2S_TEST_SYSTEM_DB_URL`、`T2S_TEST_MYSQL57_URL`、`T2S_TEST_MYSQL80_URL`；未设置时跳过并给出原因，不报错。
- 集成测试使用独立的测试 schema / 库，每个测试前后清理数据。

## 5. 验收标准

1. `docker compose -f deployment/compose.yaml up -d` 后三个服务均为 healthy。
2. `uv run alembic upgrade head` 成功创建 `jobs` 表，`uv run alembic downgrade base` 可回滚。
3. 启动 API 后：`/healthz` 返回 200；`/readyz` 在 PG 和 MySQL（5.7 与 8.0 分别配置时）均可用时返回 200，停掉 MySQL 后返回 503。
4. 任意请求的响应头都带 `X-Trace-Id`；传入合法 `X-Trace-Id` 时原样返回；该请求的所有日志行带同一 `trace_id`。
5. 未捕获异常返回统一错误结构，响应中不含堆栈和连接串。
6. 使用 `t2s_ro` 账号对业务库执行写操作会失败（权限与只读会话双重保证），有集成测试覆盖；`get_business_engine()` 建立的连接上 PyMySQL `connect_timeout`、`read_timeout` 等于 `business_db_timeout_s`（单元测试检查连接参数）。
7. 队列并发测试：入队 200 个 `noop` 任务，4 个线程并发领取，每个任务恰好执行一次；handler 抛异常的任务按退避重试，达到 `max_attempts` 后为 `failed`；handler 抛 `NonRetryableJobError` 的任务第一次失败即为 `failed`（`attempts = 1`）；租约过期的 `running` 任务可被重新领取。
8. `POST /api/v1/agent/echo` 返回与输入相同的 `answer`；`POST /v1/agent/echo`（无前缀）返回 404。
9. `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、`uv run pytest`、`uv run pytest -m integration` 全部通过。
10. `AGENTS.md` 与 README 补充：如何启动 Compose、运行迁移、启动 API 和 worker、运行集成测试。

## 6. 新增依赖

- 运行时：`sqlalchemy`、`alembic`、`httpx`（其余 fastapi、uvicorn、langgraph、psycopg、pymysql、pydantic-settings 已在 `pyproject.toml`）。`httpx` 本期供 FastAPI TestClient 使用，Spec 08 的 LLM 适配器在运行时使用它，因此直接列为运行时依赖，不放开发依赖组。
- 开发：无新增

## 7. 风险与说明

- MySQL 5.7 在 Apple Silicon 上通过 amd64 模拟运行，启动和查询较慢；如影响开发体验，可在日常开发中只起 `mysql80`，5.7 留给方言测试。
- 队列选择 PostgreSQL 而非 Redis / Celery，依据架构文档 23.2：MVP 吞吐量低，减少组件；队列接口独立封装，后续替换不影响调用方。

## 8. 修订记录

| 日期 | 修改 | 原因 |
| --- | --- | --- |
| 2026-11-20 | 业务路由统一前缀 `/api/v1`；echo 接口改为 `POST /api/v1/agent/echo`，明确为临时挂载检查、由 Spec 08 删除 | 与架构第二十章及 Spec 08/09 的接口路径一致 |
| 2026-11-20 | `t2s_ro` 只用于元数据采集与 `/readyz`，用户查询使用 Spec 07 的租户账号 | 原文"应用默认使用该账号"与 Spec 07 的租户隔离冲突（`t2s_ro` 能看全部租户） |
| 2026-11-20 | 队列增加 `NonRetryableJobError` 与 `fail(..., retryable=False)` | Spec 02 起的 handler 需要"不重试、直接失败"的语义，统一在本 Spec 定义 |
| 2026-11-20 | `httpx` 从开发依赖改为运行时依赖 | Spec 08 的 LLM 适配器在运行时使用 |
| 2026-11-20 | 明确 `business_db_timeout_s` 只是 `t2s_ro` 连接的建立/读取超时 | 与 Spec 07 的查询超时、EXPLAIN 超时区分，避免混用 |
| 2026-11-20 | Compose 默认只起数据库；约定 `api`、`worker` 放在可选 profile `app`，由 Spec 09 加入 | 与架构第二十一章 MVP 部署及 Spec 09 D6 一致 |
