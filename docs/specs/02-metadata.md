# Spec 02：Metadata 元数据目录

- 状态：待审阅
- 日期：2026-11-20
- 对应架构文档：`docs/architecture.md` 第四章（4.1～4.3）、5.1/5.3/5.5 的 MVP 修订、2.1（信任边界）、11.6（元数据清洗）、16.2（漂移）、第十九章（版本记录）、第二十三章（第 1 周）
- 依赖：Spec 01（Settings、`session_scope()`、`get_business_engine()`、`jobs` 队列、Alembic、`trace_id`）
- 被依赖：03（快照用于校验验收集）、04、05、06、07、08、09

## 1. 目标

把业务 MySQL 的结构采集成**不可变快照**存入 PostgreSQL，提供"发布指针"（Release Pointer）让查询绑定固定版本，并预留后续 Spec 需要的存储钩子：对象身份、注释注入标记与复核、STALE 标记。第 1 周交付"元数据快照可查询"。

## 2. 范围

### 2.1 本期做

| 编号 | 能力 | 说明 |
| --- | --- | --- |
| M1 | 数据源登记 | `metadata_datasource`，记录数据库精确版本、`sql_mode`、时区、字符集、`lower_case_table_names` |
| M2 | MySQL 采集器 | 表、视图、字段、索引、PK/UK、FK、注释、行数估计；兼容 5.7 与 8.0 |
| M3 | 不可变快照 | 规范化存储，按内容哈希去重，数据库触发器禁止修改 |
| M4 | 对象身份 | 自然键 + `object_uuid`，删除后重建同名对象分配新 UUID（4.3） |
| M5 | 注释注入检测 | 对表/字段注释做可疑指令检测并打标；人工复核记录表 |
| M6 | Release 与指针 | `metadata_release`（预留 Join Graph / 语义层 / Prompt / 模型版本列）、指针、事件日志；本期提供不带门禁的创建与激活 |
| M7 | STALE 标记存储 | `metadata_stale_mark` 表与读写接口（由 08、09 写入，06/08 读取） |
| M8 | 触发方式 | CLI 与队列任务 `metadata.sync`（HTTP 接口在 Spec 09） |

### 2.2 本期不做

- Migration Hook、定时 Snapshot、每日 Reconciliation（架构 5.3，阶段 2）
- Schema Diff、变更事件、影响分析、带门禁的发布与回滚（Spec 09）
- Join 边（Spec 04）、语义层（Spec 05）
- Data Profiling、采样、Top Values（架构 17.1，阶段 2）
- Embedding 与 `metadata_embedding`（架构 8.5，阶段 3）
- 分区、存储过程、触发器、CHECK 约束的采集（阶段 2；MVP 的合成库不使用这些对象）
- 一个数据源多个 database（MVP 一个数据源只采集连接串中的那一个库，见 4.1）
- HTTP 接口 `POST /api/v1/metadata/sync`（Spec 09）

## 3. 目录与文件

```
src/text2sql/
├── metadata/
│   ├── __main__.py            # M8 CLI 入口：python -m text2sql.metadata
│   ├── cli.py                 # M8 子命令
│   ├── jobs.py                # M8 队列 handler：metadata.sync
│   ├── snapshot.py            # M3 领域模型（pydantic，frozen）
│   ├── collector/
│   │   └── mysql.py           # M2 采集器
│   ├── catalog/
│   │   ├── models.py          # M1/M3/M4/M5/M6/M7 SQLAlchemy 模型
│   │   └── repository.py      # M3 快照写入与加载
│   ├── identity.py            # M4
│   ├── comment_review.py      # M5 复核记录
│   ├── release.py             # M6
│   └── stale.py               # M7
└── security/
    └── prompt_guard.py        # M5 注入检测规则（架构第十八章路径）
migrations/versions/0002_metadata.py
tests/unit/metadata/…
tests/integration/metadata/…   # 对 mysql57 与 mysql80 各跑一遍
```

## 4. 详细设计

### 4.1 配置

`Settings` 新增：

| 字段 | 环境变量 | 类型 | 默认值 |
| --- | --- | --- | --- |
| `datasource_id` | `T2S_DATASOURCE_ID` | `str`（`^[a-z0-9_]{1,64}$`） | `"local"` |

- 一个进程对应一个数据源。采集目标是 `Settings.business_db_url` 中的默认库（`SELECT DATABASE()`），为空时采集失败并报 `METADATA_NO_DATABASE`。
- 本地开发约定 `.env.example` 中 `T2S_DATASOURCE_ID=local_mysql80`；测 5.7 时切换为 `local_mysql57` 并改 `T2S_BUSINESS_DB_URL`。两个数据源的元数据可以共存于同一个 PG。
- 采集使用 Spec 01 的 `get_business_engine()`（只读会话、`t2s_ro` 账号）。采集只读 `information_schema` 和会话变量，不读业务数据。注意：网关（Spec 07）禁止用户 SQL 访问 `information_schema`，采集器不经过网关，两者不冲突。

### 4.2 M2 采集器

接口：

```python
def collect_snapshot(engine: Engine, datasource_id: str) -> SnapshotData: ...
```

采集步骤（全部在一个只读连接上执行，所有列显式写别名以抹平 5.7/8.0 返回列名大小写差异）：

1. 服务器信息：`SELECT VERSION(), DATABASE(), @@sql_mode, @@time_zone, @@system_time_zone, @@character_set_database, @@collation_database, @@lower_case_table_names`。版本解析为 `(major, minor, patch)`，后缀（如 `-log`）保留在 `server_version` 原文中。
2. 版本门槛：低于 5.7.8 时采集失败，错误码 `METADATA_UNSUPPORTED_VERSION`。原因：网关依赖 5.7.8 引入的 `max_execution_time`（架构 10.1）。
3. 表：`information_schema.TABLES`（`TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_COMMENT, TABLE_ROWS`），`TABLE_SCHEMA = DATABASE()`。
4. 字段：`information_schema.COLUMNS`（`COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA, COLUMN_COMMENT, CHARACTER_SET_NAME, COLLATION_NAME, GENERATION_EXPRESSION`）。
5. 索引：`information_schema.STATISTICS`（`INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART, INDEX_TYPE`），按 `SEQ_IN_INDEX` 组装有序列表。`PRIMARY` 为主键。
6. 外键：`KEY_COLUMN_USAGE` 关联 `REFERENTIAL_CONSTRAINTS`，只保留引用同库表的外键（跨库外键记录日志后忽略，MVP 不支持跨库）。
7. 视图：`information_schema.VIEWS`（`VIEW_DEFINITION, CHECK_OPTION, SECURITY_TYPE`）。账号无 `SHOW VIEW` 权限时定义为空串，记 `definition_available = false`，不报错。
8. 派生字段：`is_primary`（字段属于 PRIMARY）、`is_unique`（字段**单独**构成某个唯一索引）。复合唯一约束只记录在索引上，Spec 04 按列集合判定唯一性。
9. 每个表、字段的注释交给 `prompt_guard.scan_text()`，结果写入 `comment_flags`。

采集结果为 `SnapshotData`（4.4 的领域模型，不含数据库 ID）。表数超过 2000 或字段数超过 50000 时报错 `METADATA_TOO_LARGE`（MVP 防护，按 1.3 画像调整）。

### 4.3 M3 快照存储（PostgreSQL）

所有表在 Alembic 迁移 `0002_metadata` 中创建。类型均为 PostgreSQL 写法。

**`metadata_datasource`**

| 列 | 类型 | 约束 / 说明 |
| --- | --- | --- |
| `datasource_id` | `VARCHAR(64)` | 主键 |
| `dialect` | `VARCHAR(16)` | 非空，MVP 固定 `mysql` |
| `database_name` | `VARCHAR(128)` | 非空 |
| `server_version` | `VARCHAR(64)` | 最近一次采集值 |
| `sql_mode` / `time_zone` / `system_time_zone` | `TEXT` / `VARCHAR(64)` / `VARCHAR(64)` | 最近一次采集值 |
| `character_set` / `collation` | `VARCHAR(64)` | 最近一次采集值 |
| `lower_case_table_names` | `SMALLINT` | 最近一次采集值 |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

首次采集时自动插入，之后每次采集更新"最近一次"字段（这是唯一可变的元数据表）。

**`metadata_snapshot`**（不可变）

| 列 | 类型 | 约束 / 说明 |
| --- | --- | --- |
| `id` | `BIGINT` identity | 主键，即"schema 版本号" |
| `datasource_id` | `VARCHAR(64)` | FK → `metadata_datasource` |
| `database_name` | `VARCHAR(128)` | 非空 |
| `server_version` | `VARCHAR(64)` | 非空，原文 |
| `version_major` / `version_minor` / `version_patch` | `SMALLINT` | 非空 |
| `sql_mode` / `time_zone` / `system_time_zone` / `character_set` / `collation` | 同上 | 非空 |
| `lower_case_table_names` | `SMALLINT` | 非空 |
| `content_hash` | `CHAR(64)` | 非空，见下 |
| `table_count` / `column_count` | `INT` | 非空 |
| `collected_at` | `TIMESTAMPTZ` | 非空 |
| `trace_id` | `VARCHAR(64)` | 可空 |

唯一约束 `(datasource_id, content_hash)`。

**`metadata_table`**：`id` 主键；`snapshot_id` FK；`object_uuid UUID` 非空；`table_name VARCHAR(128)`；`table_type`（`BASE TABLE` / `VIEW`）；`engine`；`table_comment TEXT`；`comment_flags TEXT[]` 默认空；`row_estimate BIGINT` 可空；`content_hash CHAR(64)`。唯一 `(snapshot_id, table_name)`。

**`metadata_column`**：`id`；`snapshot_id`；`table_id` FK；`object_uuid`；`column_name`；`ordinal_position INT`；`data_type`；`column_type`；`is_nullable BOOLEAN`；`default_value TEXT`；`extra TEXT`；`generation_expression TEXT`；`character_set`；`collation`；`column_comment TEXT`；`comment_flags TEXT[]`；`is_primary BOOLEAN`；`is_unique BOOLEAN`；`content_hash`。唯一 `(table_id, column_name)`。

**`metadata_index`**：`id`；`snapshot_id`；`table_id`；`index_name`；`is_primary`；`is_unique`；`columns TEXT[]`（有序）；`sub_parts INT[]`（前缀长度，可空元素）；`index_type`。唯一 `(table_id, index_name)`。

**`metadata_foreign_key`**：`id`；`snapshot_id`；`table_id`；`constraint_name`；`columns TEXT[]`；`ref_table`；`ref_columns TEXT[]`；`on_update`；`on_delete`。

**`metadata_view`**：`id`；`snapshot_id`；`table_id`（唯一）；`view_definition TEXT`；`definition_available BOOLEAN`；`check_option`；`security_type`。

不可变性保证：

- 应用层只有"插入整份快照"的函数，没有更新函数。
- 迁移为上述 6 张快照表创建 `BEFORE UPDATE OR DELETE` 触发器，抛出异常 `snapshot rows are immutable`。保留期清理在阶段 2 实现，届时通过显式关闭触发器的维护脚本执行。

内容哈希与去重：

- `content_hash = sha256(canonical_json(SnapshotData 去掉 collected_at、row_estimate))`。canonical JSON：键排序、表按名排序、字段按 `ordinal_position`、索引按名、外键按约束名。`row_estimate` 不进哈希，原因是 `TABLE_ROWS` 每次都会波动，进哈希会导致每次采集都产生新快照。
- 若该数据源已存在相同 `content_hash` 的快照，`sync` 不写新快照，直接返回已有快照 ID，结果标记 `unchanged = true`。

仓库接口（`text2sql.metadata.catalog.repository`）：

```python
def save_snapshot(session: Session, data: SnapshotData) -> SaveResult: ...   # SaveResult(snapshot_id: int, unchanged: bool)
def load_snapshot(session: Session, snapshot_id: int) -> SchemaSnapshot: ...
def latest_snapshot_id(session: Session, datasource_id: str) -> int | None: ...
def list_snapshots(session: Session, datasource_id: str, limit: int = 20) -> list[SnapshotSummary]: ...
```

### 4.4 领域模型（`text2sql.metadata.snapshot`）

pydantic v2，`model_config = ConfigDict(frozen=True)`，供 04～09 统一使用：

```python
class ServerInfo(BaseModel):
    server_version: str
    version: tuple[int, int, int]
    sql_mode: str
    time_zone: str
    system_time_zone: str
    character_set: str
    collation: str
    lower_case_table_names: int

class ColumnInfo(BaseModel):
    name: str
    ordinal_position: int
    data_type: str            # 小写，如 "int"、"datetime"
    column_type: str          # 如 "tinyint(4)"
    is_nullable: bool
    default_value: str | None
    extra: str
    generation_expression: str | None
    comment: str
    comment_flags: tuple[str, ...]
    is_primary: bool
    is_unique: bool
    object_uuid: UUID

class IndexInfo(BaseModel):
    name: str
    is_primary: bool
    is_unique: bool
    columns: tuple[str, ...]
    sub_parts: tuple[int | None, ...]

class ForeignKeyInfo(BaseModel):
    name: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]

class ViewInfo(BaseModel):
    definition: str
    definition_available: bool

class TableInfo(BaseModel):
    name: str
    table_type: Literal["BASE TABLE", "VIEW"]
    comment: str
    comment_flags: tuple[str, ...]
    row_estimate: int | None
    columns: tuple[ColumnInfo, ...]
    indexes: tuple[IndexInfo, ...]
    foreign_keys: tuple[ForeignKeyInfo, ...]
    view: ViewInfo | None
    object_uuid: UUID

class SchemaSnapshot(BaseModel):
    snapshot_id: int
    datasource_id: str
    database_name: str
    server: ServerInfo
    content_hash: str
    collected_at: datetime
    tables: tuple[TableInfo, ...]

    def table(self, name: str) -> TableInfo | None: ...            # 按 lower_case_table_names 规则匹配
    def column(self, table: str, column: str) -> ColumnInfo | None: ...  # 字段名大小写不敏感
    def unique_column_sets(self, table: str) -> tuple[frozenset[str], ...]: ...  # PK 与所有唯一索引（不含前缀索引）
    def target_key(self) -> Literal["mysql57", "mysql80"]: ...      # 5.7.x → mysql57；8.0.x → mysql80；其他版本抛错
```

`SnapshotData` 结构相同，但没有 `snapshot_id` 和 `object_uuid`（UUID 在保存时由 M4 分配）。

**对象键（object key）约定**，04～09 共用：

| 对象类型 `object_type` | `object_key` 示例 |
| --- | --- |
| `TABLE` | `t_work_order` |
| `COLUMN` | `t_work_order.status` |
| `EDGE` | `work_order_customer`（Spec 04 的边 ID） |
| `METRIC` | `work_order_count`（Spec 05） |
| `DIMENSION` | `industry`（Spec 05） |
| `ENUM` | `t_work_order.status`（Spec 05） |

MVP 一个数据源只有一个库，所以键里不带库名。

### 4.5 M4 对象身份

**`metadata_object_identity`**：`object_uuid UUID` 主键；`datasource_id`；`object_type`（`TABLE` / `COLUMN`）；`object_key`；`first_snapshot_id`；`last_seen_snapshot_id`；`retired_at_snapshot_id` 可空。部分唯一索引 `(datasource_id, object_type, object_key) WHERE retired_at_snapshot_id IS NULL`。

规则（保存快照时在同一事务中执行）：

- 键在当前有效身份中存在：复用其 UUID，更新 `last_seen_snapshot_id`。
- 键不存在：新建 UUID。
- 上一份快照有、本份没有的键：写入 `retired_at_snapshot_id`。之后同名对象再出现时分配新 UUID（架构 4.3：同名重建不一定是同一业务字段）。
- 重命名不自动识别，由 Spec 09 的人工 Rename Mapping 处理。

### 4.6 M5 注释注入检测与复核

`text2sql.security.prompt_guard`：

```python
def scan_text(text: str) -> tuple[str, ...]: ...   # 返回命中的规则码，空元组表示干净
```

规则码（正则，大小写不敏感，中英文各一组）：

| 规则码 | 命中条件示例 |
| --- | --- |
| `INSTRUCTION_OVERRIDE` | ignore (all )?(previous\|above) / 忽略(之前\|以上\|上面)的?(所有)?(规则\|指令\|要求) |
| `ROLE_PLAY` | you are now / 你现在是 / act as / system prompt / 系统提示 |
| `PRIVILEGE_REQUEST` | 所有租户 / all tenants / bypass / 绕过 / 越权 |
| `CODE_OR_MARKUP` | 三个反引号、`<\|`、`</`、`{{` |
| `URL` | `https?://` |
| `TOO_LONG` | 长度超过 500 字符 |

**`metadata_comment_review`**：`id`；`datasource_id`；`object_type`；`object_key`；`comment_hash CHAR(64)`（sha256 原文）；`decision`（`ALLOW` / `REDACT`）；`reviewer VARCHAR(128)`；`reviewed_at TIMESTAMPTZ`；`note TEXT`。唯一 `(datasource_id, object_type, object_key, comment_hash)`。

```python
def record_review(session, *, datasource_id, object_type, object_key, comment: str,
                  decision: Literal["ALLOW", "REDACT"], reviewer: str, note: str = "") -> None: ...
def comment_visible(session, *, datasource_id, object_type, object_key, comment: str,
                    flags: Sequence[str]) -> bool: ...
```

`comment_visible` 规则：无标记 → `True`；有标记且存在同哈希 `ALLOW` 记录 → `True`；否则 `False`。Spec 08 构建模型上下文时只放 `comment_visible` 为真的注释；注释改动后哈希变化，需要重新复核。

### 4.7 M6 Release 与发布指针

**`metadata_release`**

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `BIGINT` identity | 主键，即 `metadata_release_id`（对外为整数） |
| `datasource_id` | `VARCHAR(64)` | FK |
| `snapshot_id` | `BIGINT` | FK，非空 |
| `join_graph_version_id` | `BIGINT` | 可空；FK 由 Spec 04 的迁移添加 |
| `semantic_version_id` | `BIGINT` | 可空；FK 由 Spec 05 的迁移添加 |
| `prompt_version` | `VARCHAR(80)` | 可空；由 Spec 08 填写 |
| `model_version` | `VARCHAR(160)` | 可空；由 Spec 08 填写 |
| `db_dialect` | `VARCHAR(16)` | 非空，取自快照 |
| `db_version` | `VARCHAR(64)` | 非空，取自快照 `server_version` |
| `status` | `VARCHAR(16)` | `CANDIDATE` / `PUBLISHED` / `REJECTED` |
| `evaluation_status` | `VARCHAR(16)` | `NOT_RUN` / `PASSED` / `FAILED`，默认 `NOT_RUN` |
| `eval_run_id` | `BIGINT` | 可空；FK 由 Spec 03 的迁移添加 |
| `created_by` | `VARCHAR(128)` | 非空 |
| `note` | `TEXT` | 默认空 |
| `created_at` | `TIMESTAMPTZ` | 非空 |
| `trace_id` | `VARCHAR(64)` | 可空 |

Release 一经创建，组件列（`snapshot_id`、`join_graph_version_id`、`semantic_version_id`、`prompt_version`、`model_version`、`db_*`）不可修改（触发器只允许改 `status`、`evaluation_status`、`eval_run_id`、`note`）。所有列一次建好，避免后续 Spec 反复改表，原因是这些组件在架构第十九章已经确定。

**`metadata_release_pointer`**：`datasource_id` 主键；`release_id` FK 非空；`previous_release_id` 可空；`updated_at`；`updated_by`。

**`metadata_release_event`**：`id`；`datasource_id`；`from_release_id` 可空；`to_release_id`；`action`（`ACTIVATE` / `PUBLISH` / `ROLLBACK`）；`actor`；`reason`；`created_at`；`trace_id`。

接口（`text2sql.metadata.release`）：

```python
@dataclass(frozen=True)
class ReleaseRecord:
    release_id: int
    datasource_id: str
    snapshot_id: int
    join_graph_version_id: int | None
    semantic_version_id: int | None
    prompt_version: str | None
    model_version: str | None
    db_dialect: str
    db_version: str
    status: str
    evaluation_status: str
    eval_run_id: int | None
    created_at: datetime

def create_release(session: Session, *, datasource_id: str, snapshot_id: int,
                   join_graph_version_id: int | None = None, semantic_version_id: int | None = None,
                   prompt_version: str | None = None, model_version: str | None = None,
                   created_by: str, note: str = "") -> int: ...
def get_release(session: Session, release_id: int) -> ReleaseRecord: ...
def get_active_release_id(session: Session, datasource_id: str) -> int | None: ...
def move_pointer(session: Session, *, datasource_id: str, release_id: int,
                 action: Literal["ACTIVATE", "PUBLISH", "ROLLBACK"], actor: str, reason: str) -> None: ...
def activate_release(session: Session, release_id: int, *, actor: str, reason: str) -> None: ...
```

- `move_pointer` 是唯一移动指针的函数：`SELECT … FOR UPDATE` 锁住指针行（不存在则插入），校验 release 属于同一数据源且状态不是 `REJECTED`，更新指针、写事件、把目标 release 置为 `PUBLISHED`，全部在调用方的同一事务内（架构 5.5 的"事务性切换"）。
- `activate_release` = `move_pointer(action="ACTIVATE")`，**不做门禁**，用于第 1～5 周开发和评测。`Settings.env == "prod"` 时抛出 `AppError("RELEASE_GATE_REQUIRED", …, 409)`，生产只能走 Spec 09 的带门禁发布与回滚。
- 每个查询在开始时读取一次 `get_active_release_id()` 并在整个查询中固定使用（架构 5.5），由 Spec 08 实现。

### 4.8 M7 STALE 标记存储

**`metadata_stale_mark`**

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `BIGINT` identity | 主键 |
| `datasource_id` | `VARCHAR(64)` | FK |
| `object_type` | `VARCHAR(16)` | `TABLE` / `COLUMN` / `EDGE` / `METRIC` / `DIMENSION` / `ENUM` |
| `object_key` | `VARCHAR(300)` | 4.4 的对象键 |
| `reason_code` | `VARCHAR(48)` | 例如 `COLUMN_REMOVED`、`TYPE_CHANGED`、`EXECUTION_UNKNOWN_COLUMN`、`DEPENDS_ON_STALE` |
| `detail` | `TEXT` | 不含业务数据 |
| `source` | `VARCHAR(16)` | `SCHEMA_DIFF` / `EXECUTION_DRIFT` / `MANUAL` |
| `change_event_id` | `BIGINT` | 可空；FK 由 Spec 09 添加 |
| `created_at` / `created_by` | | 非空 |
| `resolved_at` / `resolved_by` / `resolution_note` | | 可空 |

部分唯一索引 `(datasource_id, object_type, object_key) WHERE resolved_at IS NULL`：同一对象同时最多一条未解决标记，重复标记时追加 `detail` 不新建。

```python
ObjectType = Literal["TABLE", "COLUMN", "EDGE", "METRIC", "DIMENSION", "ENUM"]

@dataclass(frozen=True)
class StaleSet:
    keys: frozenset[tuple[ObjectType, str]]
    def is_stale(self, object_type: ObjectType, object_key: str) -> bool: ...
    def column_or_table_stale(self, column_key: str) -> bool: ...   # 字段或其所在表任一被标记即为真

def mark_stale(session: Session, *, datasource_id: str, object_type: ObjectType, object_key: str,
               reason_code: str, detail: str, source: Literal["SCHEMA_DIFF", "EXECUTION_DRIFT", "MANUAL"],
               created_by: str, change_event_id: int | None = None) -> int: ...
def resolve_stale(session: Session, mark_id: int, *, resolved_by: str, note: str) -> None: ...
def load_open_stale(session: Session, datasource_id: str) -> StaleSet: ...
```

`load_open_stale` 不缓存，每个查询开始时读一次（标记要立即生效，见架构 5.1 修订"STALE 对象直接退出检索和规划"）。

### 4.9 M8 CLI 与队列任务

`uv run python -m text2sql.metadata <子命令>`：

| 子命令 | 作用 |
| --- | --- |
| `sync` | 采集并保存快照，打印 `snapshot_id`、是否 `unchanged`、表数与字段数、被标记的注释数 |
| `snapshots` | 列出当前数据源最近 20 个快照 |
| `show [--snapshot ID\|latest] [--table NAME]` | 打印快照概要或某张表的字段、索引、外键、注释与标记 |
| `flagged [--snapshot ID\|latest]` | 列出带注入标记且未复核的注释 |
| `review-comment --type COLUMN --key t.c --decision ALLOW\|REDACT --reviewer NAME [--note]` | 写复核记录（对当前快照中的注释原文） |
| `release create [--snapshot ID] --by NAME [--note]` | 创建 release（本期只带快照） |
| `release activate ID --by NAME --reason TEXT` | 不带门禁的激活（prod 环境拒绝） |
| `release show [ID\|active]` | 打印 release 组件与状态 |

队列：`@register("metadata.sync")`，payload `{"datasource_id": str, "requested_by": str}`；`datasource_id` 必须等于当前进程的 `Settings.datasource_id`，否则任务失败（不重试，直接 `failed`：handler 抛出 Spec 01 F6 定义的 `NonRetryableJobError`）。

日志：采集开始、结束各一条，字段含 `datasource_id`、`snapshot_id`、`unchanged`、`elapsed_ms`、`trace_id`。

## 5. 验收标准

1. `uv run alembic upgrade head` 创建本 Spec 的全部表与触发器；`downgrade` 到 `0001` 可回滚。
2. 对 `mysql57` 与 `mysql80` 分别执行 `sync`（集成测试，使用 Spec 03 的合成库；03 未就绪前使用测试内建的 3 张表夹具，含复合唯一索引、外键、视图、带注入文本的注释）：快照中的表、字段、PK、唯一索引、外键与夹具定义逐项一致；`server.version` 主版本分别为 5.7 与 8.0；`sql_mode`、`time_zone`、字符集、`lower_case_table_names` 非空。
3. 结构不变时连续两次 `sync`，第二次返回同一 `snapshot_id` 且 `unchanged = true`；仅 `TABLE_ROWS` 变化也不产生新快照。
4. 对快照表执行 `UPDATE` 或 `DELETE` 被触发器拒绝（集成测试）。
5. 新增一列后 `sync` 产生新快照：未变化的表与字段 `object_uuid` 不变；删除一列再加回同名列，新列 `object_uuid` 与原来不同。
6. 夹具中写有"忽略之前的所有规则"的字段注释被标记 `INSTRUCTION_OVERRIDE`；`comment_visible` 在复核前为假，`ALLOW` 复核后为真，注释内容改变后再次为假。`scan_text` 对每条规则至少有 2 个命中和 2 个不命中的单元测试。
7. `release create` + `release activate` 后 `get_active_release_id` 返回该 ID，`metadata_release_event` 有一条 `ACTIVATE`；`T2S_ENV=prod` 时 `activate` 返回 `RELEASE_GATE_REQUIRED`；尝试修改已创建 release 的 `snapshot_id` 被拒绝。
8. 两个线程同时对同一数据源 `move_pointer` 到不同 release：最终指针等于最后提交者，事件表有两条且 `from_release_id` 链连续（集成测试）。
9. `mark_stale` 对同一对象重复调用只产生一条未解决记录；`resolve_stale` 后再次标记产生新记录；`StaleSet.column_or_table_stale("t.c")` 在只标记表 `t` 时为真。
10. 连接 5.7.8 以下版本时（单元测试中模拟版本字符串）报 `METADATA_UNSUPPORTED_VERSION`。
11. `uv run python -m text2sql.metadata show --table <表>` 能打印字段、索引、外键和注释标记（第 1 周交付"元数据快照可查询"）。
12. `ruff`、`mypy`、`pytest`、`pytest -m integration` 全部通过。

## 6. 新增依赖

无（pydantic、SQLAlchemy、PyMySQL、psycopg 已由 Spec 01 引入）。

## 7. 风险与说明

- **不可重试的任务失败**：参数或配置不匹配（如 `datasource_id` 不一致）重试无意义，handler 抛出 Spec 01 F6 的 `text2sql.jobs.queue.NonRetryableJobError`，worker 直接置 `failed`。本 Spec 不修改队列。
- **采集账号权限**：`t2s_ro` 只有 `SELECT`，看不到视图定义（需 `SHOW VIEW`）。MVP 合成库不在业务库内放视图，不受影响；真实库若视图承载业务逻辑（架构 17.3），需要 DBA 授予 `SHOW VIEW`（开放决策，见下）。
- **`TABLE_ROWS` 仅为估计值**，InnoDB 上误差可能很大，只用于展示和成本提示，不进入任何正确性判断。
- **快照去重的副作用**：结构不变时沿用旧快照，`row_estimate` 也停在旧值。阶段 2 引入 Data Profiling 时单独存储统计信息。
- **单库假设**：一个数据源只采集一个库。真实库是多 schema 时（1.3 待填），需要在对象键中加库名，属于后续 Spec 的变更。
- **开放决策（需用户确认）**：
  1. 真实目标库画像（架构 1.3）：版本、表/字段规模、是否有 `SHOW VIEW` 权限、`lower_case_table_names`。本 Spec 的规模上限（2000 表 / 50000 字段）是 MVP 防护值，按画像调整。
  2. 注释复核人（架构 1.4 未填）：`review-comment` 的 `--reviewer` 由谁担任。
