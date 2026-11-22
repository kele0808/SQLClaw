# Spec 02：Metadata 元数据目录

- 状态：已确认（2026-11-20）；按 docs/plans/02-metadata.md「待确认」修订（2026-11-22）；大表值采集改为索引路径（2026-11-22，见 `docs/research/02-value-index-large-tables.md`）
- 日期：2026-11-20
- 修订记录：
  - 2026-11-22：采纳大 Schema 调研（`docs/research/01-text2sql-sota.md` 建议 3、5）。新增 M9「表族与公共列」（只派生、不存储）和 M10「值索引」（离线 distinct 值 + PG `pg_trgm` 模糊匹配，供 Spec 05/08 实体解析先查）；4.1 加值索引配置；4.9 加子命令与队列任务 `metadata.value_index`；验收追加 13–16。两项都属于 **MVP**（第 1 周随 Spec 02 实现），原因：成本低，且 Spec 03 的干扰表压测和 Spec 08 的实体解析依赖它们。
  - 2026-11-22（大表值采集，`docs/research/02-value-index-large-tables.md`）：4.11 去掉整表跳过的 `TOO_MANY_ROWS`，改为逐列的「大表索引路径」：超过 `value_index_max_rows` 的表，只有存在 `(租户列, 本列)` 前导整列 BTREE 索引的列才采（否则 `BIG_TABLE_NO_INDEX`），执行前用 `EXPLAIN` 确认松散索引扫描（否则 `NO_LOOSE_SCAN`）；采集 SQL 的 `DISTINCT` 只放裸列（去掉 `CAST(... AS CHAR)` 和 `NULL AS tenant_key`，二者会让松散扫描失效，本机实测）；`ColumnDecision` 加 `path`；验收 14、15 改写，追加 17、18。索引信息沿用已有的 `IndexInfo.columns` / `sub_parts` / `index_type`，模型和表结构不变。
  - 2026-11-22（构建时现读行数）：值索引构建前在 `t2s_ro` 上现读 `information_schema.TABLES.TABLE_ROWS`（8.0 先在本会话 `SET SESSION information_schema_stats_expiry = 0`），以现读值代替快照的 `row_estimate` 判断大表；快照里有、现读结果里没有的表记 `TABLE_GONE`；`plan_value_columns` 加 `row_estimates` 参数；验收追加 19。
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
| M9 | 表族与公共列（2026-11-22 新增） | 从快照派生：同名前缀 + 同列签名的分表/备份表归为一个"表族"，多数表都有的审计列记为"公共列"；只是计算，不存储（4.10） |
| M10 | 值索引（2026-11-22 新增） | 对非敏感、非键、低基数的文本列离线取 distinct 值，按租户存入 PG，用 `pg_trgm` 做精确 + 模糊匹配；本 Spec 唯一读业务行的部分（4.11） |

### 2.2 本期不做

- Migration Hook、定时 Snapshot、每日 Reconciliation（架构 5.3，阶段 2）
- Schema Diff、变更事件、影响分析、带门禁的发布与回滚（Spec 09）
- Join 边（Spec 04）、语义层（Spec 05）
- Data Profiling、采样、Top Values（架构 17.1，阶段 2）。例外：M10 值索引只取低基数文本列的 distinct 值，不算 Profiling（4.11）
- 值索引的 embedding / 同义词扩展、定时重建（阶段 2；MVP 由 sync 后手动或 Spec 09 入队触发）
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
│   ├── jobs.py                # M8 队列 handler：metadata.sync、metadata.value_index
│   ├── snapshot.py            # M3 领域模型（pydantic，frozen）
│   ├── structure.py           # M9 表族与公共列（纯函数）
│   ├── value_index.py         # M10 列筛选规则、值归一化、问题切词（纯函数）
│   ├── collector/
│   │   ├── mysql.py           # M2 采集器
│   │   └── values.py          # M10 distinct 值读取
│   ├── catalog/
│   │   ├── models.py          # M1/M3/M4/M5/M6/M7/M10 SQLAlchemy 模型
│   │   ├── repository.py      # M3 快照写入与加载
│   │   └── value_store.py     # M10 值索引写入与检索
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
| `value_index_max_distinct` | `T2S_VALUE_INDEX_MAX_DISTINCT` | `int`（≥ 1） | `5000`：单列 distinct 值超过它就不索引（`TOO_MANY_VALUES`） |
| `value_index_max_rows` | `T2S_VALUE_INDEX_MAX_ROWS` | `int`（≥ 1） | `2000000`：行数估计（构建时现读的 `TABLE_ROWS`，4.11）超过它的表是「大表」，只采有 `(租户列, 列)` 前导索引、且 `EXPLAIN` 确认松散索引扫描的列（`BIG_TABLE_NO_INDEX` / `NO_LOOSE_SCAN`，4.11） |
| `value_index_max_value_length` | `T2S_VALUE_INDEX_MAX_VALUE_LENGTH` | `int`（1–512） | `128`：更长的值不收 |
| `value_index_timeout_ms` | `T2S_VALUE_INDEX_TIMEOUT_MS` | `int`（≥ 100） | `3000`：单列查询的 `MAX_EXECUTION_TIME`，须小于 Spec 01 的 `business_db_timeout_s`（客户端读超时 5 s） |
| `value_index_tenant_column` | `T2S_VALUE_INDEX_TENANT_COLUMN` | `str` | `"tenant_id"`：租户列名；空串表示单租户库，关闭租户规则 |

- 一个进程对应一个数据源。采集目标是 `Settings.business_db_url` 中的默认库（`SELECT DATABASE()`），为空时采集失败并报 `METADATA_NO_DATABASE`。
- 本地开发约定 `.env.example` 中 `T2S_DATASOURCE_ID=local_mysql80`；测 5.7 时切换为 `local_mysql57` 并改 `T2S_BUSINESS_DB_URL`。两个数据源的元数据可以共存于同一个 PG。
- 采集使用 Spec 01 的 `get_business_engine()`（只读会话、`t2s_ro` 账号）。结构采集（M2）只读 `information_schema` 和会话变量，不读业务数据；唯一的例外是 M10 值索引，它用同一个只读引擎对筛选后的列执行有界的 `SELECT DISTINCT`（4.11），仍属于"元数据采集"，不执行任何用户 SQL。注意：网关（Spec 07）禁止用户 SQL 访问 `information_schema`，采集器不经过网关，两者不冲突。

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
6. 外键：`KEY_COLUMN_USAGE` 关联 `REFERENTIAL_CONSTRAINTS`，只保留引用同库表的外键（跨库外键记录日志后忽略，MVP 不支持跨库）。查询用 LEFT JOIN `REFERENTIAL_CONSTRAINTS`：账号看不到该视图时外键仍被采集，`ON UPDATE` / `ON DELETE` 记为空串。
7. 视图：`information_schema.VIEWS`（`VIEW_DEFINITION, CHECK_OPTION, SECURITY_TYPE`）。账号无 `SHOW VIEW` 权限时定义为空串，记 `definition_available = false`，不报错。`VIEWS` 中没有该视图的行时同样处理。视图在 `TABLES` 中的 `TABLE_COMMENT` 固定为字面量 `"VIEW"`，归一为空串；`GENERATION_EXPRESSION` 为空串时记为 null。
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
| `character_set` / `collation_name` | `VARCHAR(64)` | 最近一次采集值 |
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
| `sql_mode` / `time_zone` / `system_time_zone` / `character_set` / `collation_name` | 同上 | 非空 |
| `lower_case_table_names` | `SMALLINT` | 非空 |
| `content_hash` | `CHAR(64)` | 非空，见下 |
| `table_count` / `column_count` | `INT` | 非空 |
| `collected_at` | `TIMESTAMPTZ` | 非空 |
| `trace_id` | `VARCHAR(64)` | 可空 |

普通索引 `(datasource_id, content_hash)`，不设唯一约束（去重只和最新快照比较，见下）。

**`metadata_table`**：`id` 主键；`snapshot_id` FK；`object_uuid UUID` 非空；`table_name VARCHAR(128)`；`table_type`（`BASE TABLE` / `VIEW`）；`engine`；`table_comment TEXT`；`comment_flags TEXT[]` 默认空；`row_estimate BIGINT` 可空；`content_hash CHAR(64)`。唯一 `(snapshot_id, table_name)`。

**`metadata_column`**：`id`；`snapshot_id`；`table_id` FK；`object_uuid`；`column_name`；`ordinal_position INT`；`data_type`；`column_type`；`is_nullable BOOLEAN`；`default_value TEXT`；`extra TEXT`；`generation_expression TEXT`；`character_set`；`collation_name`；`column_comment TEXT`；`comment_flags TEXT[]`；`is_primary BOOLEAN`；`is_unique BOOLEAN`；`content_hash`。唯一 `(table_id, column_name)`。

**`metadata_index`**：`id`；`snapshot_id`；`table_id`；`index_name`；`is_primary`；`is_unique`；`columns TEXT[]`（有序）；`sub_parts INT[]`（前缀长度，可空元素）；`index_type`。唯一 `(table_id, index_name)`。

**`metadata_foreign_key`**：`id`；`snapshot_id`；`table_id`；`constraint_name`；`columns TEXT[]`；`ref_table`；`ref_columns TEXT[]`；`on_update`；`on_delete`。唯一 `(table_id, constraint_name)`。

**`metadata_view`**：`id`；`snapshot_id`；`table_id`（唯一）；`view_definition TEXT`；`definition_available BOOLEAN`；`check_option`；`security_type`。

`collation` 是 PostgreSQL 保留字，所以三张表的列名用 `collation_name`；领域模型字段仍叫 `collation`。

不可变性保证：

- 应用层只有"插入整份快照"的函数，没有更新函数。
- 迁移为上述 6 张快照表创建 `BEFORE UPDATE OR DELETE` 触发器，抛出异常 `snapshot rows are immutable`。保留期清理在阶段 2 实现，届时通过显式关闭触发器的维护脚本执行。

内容哈希与去重：

- `content_hash = sha256(canonical_json(SnapshotData 去掉 collected_at、row_estimate))`。canonical JSON：键排序、表按名排序、字段按 `ordinal_position`、索引按名、外键按约束名。`row_estimate` 不进哈希，原因是 `TABLE_ROWS` 每次都会波动，进哈希会导致每次采集都产生新快照。
- 若该数据源**最新一份**快照的 `content_hash` 相同，`sync` 不写新快照，返回该快照 ID，结果标记 `unchanged = true`。与更早的快照相同（结构 A→B→A）时照常写新快照：否则删除后重建的字段会落回旧快照、复用旧 UUID，与 4.5 和验收 5 矛盾。

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
    character_set: str | None
    collation: str | None
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
    index_type: str

class ForeignKeyInfo(BaseModel):
    name: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    on_update: str
    on_delete: str

class ViewInfo(BaseModel):
    definition: str
    definition_available: bool
    check_option: str
    security_type: str

class TableInfo(BaseModel):
    name: str
    table_type: Literal["BASE TABLE", "VIEW"]
    engine: str | None
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

采集结果用不带 UUID 的 `ColumnData` / `TableData`（`ColumnInfo`、`TableInfo` 分别继承它们并加 `object_uuid`）；`SnapshotData` 含 `datasource_id`、`database_name`、`server`、`collected_at`、`tables: tuple[TableData, ...]`，没有 `snapshot_id` 和 `content_hash`（UUID 在保存时由 M4 分配，哈希由 `snapshot_content_hash()` 计算）。`target_key()` 对 5.7/8.0 以外的版本抛 `METADATA_UNSUPPORTED_VERSION`。

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

`comment_visible` 规则：无标记 → `True`；有标记且存在同哈希 `ALLOW` 记录 → `True`；否则 `False`。Spec 08 构建模型上下文时只放 `comment_visible` 为真的注释；注释改动后哈希变化，需要重新复核。同一对象同一注释哈希再次复核时覆盖旧决定（upsert）。

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

- `move_pointer` 是唯一移动指针的函数：先 `SELECT … FOR UPDATE` 锁住 `metadata_datasource` 行（指针行首次插入前也能让并发调用排队），再读取并更新或插入指针行，校验 release 属于同一数据源且状态不是 `REJECTED`，更新指针、写事件、把目标 release 置为 `PUBLISHED`，全部在调用方的同一事务内（架构 5.5 的"事务性切换"）。
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
| `structure [--snapshot ID\|latest]` | 打印表族（代表表与成员）和公共列（M9） |
| `values build [--include t.c ...] [--exclude t.c ...]` | 基于最新快照、并现读业务库的 `TABLE_ROWS`，重建值索引（M10），打印索引列数、跳过列数、值数 |
| `values show` | 打印最新一次构建的每一列：已索引或跳过原因、值数 |
| `values search TEXT [--tenant KEY]` | 调试用：打印命中的列、值、分数、精确/模糊 |
| `snapshots` | 列出当前数据源最近 20 个快照 |
| `show [--snapshot ID\|latest] [--table NAME]` | 打印快照概要或某张表的字段、索引、外键、注释与标记 |
| `flagged [--snapshot ID\|latest]` | 列出带注入标记且未复核的注释 |
| `review-comment --type COLUMN --key t.c --decision ALLOW\|REDACT --reviewer NAME [--note]` | 写复核记录（对当前快照中的注释原文） |
| `release create [--snapshot ID] --by NAME [--note]` | 创建 release（本期只带快照） |
| `release activate ID --by NAME --reason TEXT` | 不带门禁的激活（prod 环境拒绝） |
| `release show [ID\|active]` | 打印 release 组件与状态 |

队列：`@register("metadata.sync")`，payload `{"datasource_id": str, "requested_by": str}`；`datasource_id` 必须等于当前进程的 `Settings.datasource_id`，否则任务失败（不重试，直接 `failed`：handler 抛出 Spec 01 F6 定义的 `NonRetryableJobError`）。

日志：采集开始、结束各一条，字段含 `datasource_id`、`snapshot_id`、`unchanged`、`elapsed_ms`、`trace_id`。

值索引队列：`@register("metadata.value_index")`，payload `{"datasource_id": str, "requested_by": str, "include"?: [str], "exclude"?: [str]}`，`datasource_id` 校验同上。MVP 由人在 `sync` 后执行 `values build`；Spec 09 的 `sync_and_diff` 在结构变化后自动入队。

worker 启动时导入 `text2sql.jobs.worker.HANDLER_MODULES` 中列出的模块完成 handler 注册（本 Spec 加入 `text2sql.metadata.jobs`，它同时注册 `metadata.sync` 和 `metadata.value_index`）。

### 4.10 M9 表族与公共列

> 参考：[ReFoRCE](https://arxiv.org/abs/2502.00675) 按表名模式把同构表分组压缩；[DBCC](https://arxiv.org/abs/2606.28601) 抽取重复审计列、同构表模板化，Spider 2.0-Snow 上下文从 2.6M 降到 34.7K tokens。真实业务库里 `t_order_202401`、`t_order_bak` 这类分表/备份表和 `created_by`/`updated_at` 审计列是检索噪声的主要来源。

`text2sql.metadata.structure` 中的纯函数，输入快照的表列表，输出不存储、不进 `content_hash`（规则调整不应产生新快照）：

```python
@dataclass(frozen=True)
class TableFamily:
    stem: str                    # t_order
    representative: str          # 代表表：有不带后缀的同名表时用它，否则用名字排序最后一张（通常是最新分表）
    members: tuple[str, ...]     # 按名排序，含代表表

@dataclass(frozen=True)
class StructureSummary:
    families: tuple[TableFamily, ...]
    common_columns: tuple[str, ...]
    def family_of(self, table: str) -> TableFamily | None: ...

def family_stem(table_name: str) -> str: ...
def table_families(tables: Sequence[TableData]) -> tuple[TableFamily, ...]: ...
def common_columns(tables: Sequence[TableData], *, min_tables: int = 5, ratio: float = 0.5) -> tuple[str, ...]: ...
def summarize_structure(tables: Sequence[TableData]) -> StructureSummary: ...
```

- **表族**：只看 `BASE TABLE`。表名去掉一个或多个尾部后缀（`_数字（1–8 位）`、`_bak`、`_backup`、`_old`、`_tmp`、`_temp`、`_copy`、`_his`、`_history`、`_archive`、`_arch`，大小写不敏感）得到 `stem`；`stem` 相同**且**列签名（按 `ordinal_position` 的 `(列名小写, data_type)` 序列）完全相同的表才归为一族，至少 2 张。列不同的同名前缀表不合并，避免把"看起来像"的两张表混为一谈。
- **公共列**：至少 `min_tables` 张、且至少 `ratio` 比例的基表都有的 `(列名, data_type)`；主键列名不算（`id` 不是审计列）。
- 用途（消费方）：Spec 08 检索的一级摘要按表族折叠、公共列只列一次（08 §4.2）；Spec 03 干扰表压测用表族生成"分表副本"（03 E10）；Spec 05 可对表族只建一个语义模型（阶段 2）。
- **MVP**：实现上述函数和 `structure` 子命令。**阶段 2**：表族的人工确认/排除清单、按时间后缀的路由（查哪个分表）。

### 4.11 M10 值索引

> 参考：[CHESS](https://arxiv.org/abs/2405.16755) 离线对文本列建 MinHash LSH 值索引，跳过 id/url/email/phone 等列；[supersonic](https://github.com/tencentmusic/supersonic) 把维度值加载成 HanLP 词典做映射；[LinkedIn SQL Bot](https://arxiv.org/abs/2507.14372) 对类别字段取 top-K 值放进上下文。我们用 PG 自带的 `pg_trgm` 代替 LSH，不新增服务和依赖。
>
> 大表（2026-11-22，详见 `docs/research/02-value-index-large-tables.md`）：CHESS、CodeS 面向 SQLite 小库，对整列直接 `SELECT DISTINCT`；工业界（supersonic、Databricks Genie、Snowflake Cortex Analyst）都不自动扫大表，靠用户选列、限定扫描范围或数仓算力。我们连的是 MySQL 只读库，所以只走 MySQL 的松散索引扫描（loose index scan）：成本和 distinct 值个数成正比，和行数无关。

**解决的问题**：用户说"华东区""华东精密"，要落到 `t_region.name = '华东区'`、`t_customer.name = '华东精密有限公司'`。只靠 `enums.yaml`（人工维护）和网关实时 `IN` 查询（Spec 08 A5）时，不知道该查哪一列，模糊名称也查不到。

**构建时现读行数**（`collector/values.py` 的 `live_row_estimates`）：结构不变时快照沿用旧快照（4.3），快照里的 `row_estimate` 可能停在很久以前。所以每次构建在规划列之前，用同一个 `t2s_ro` 引擎、一条连接执行：

```sql
-- 仅 8.0：只改本会话，不需要权限，不写任何数据；5.7 没有这个变量，跳过
SET SESSION information_schema_stats_expiry = 0;
SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES
WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE';
```

- `:db` 是快照的 `database_name`；是否执行 `SET` 按快照的 `server.version >= (8, 0, 0)` 判断。
- 结果作为 `row_estimates: Mapping[str, int | None]` 传给纯函数 `plan_value_columns`：
  - 现读值不为 `NULL` → 代替快照的 `row_estimate`；
  - 现读值为 `NULL` → 回退到快照的 `row_estimate`；
  - 快照里的基表不在现读结果中 → `TABLE_GONE`（视图不在这条查询里，先被 `VIEW` 跳过）。
  - 不传 `row_estimates`（`None`）时只用快照值，供单元测试和没有业务库连接的调用方使用。
- 为什么 8.0 要 `SET SESSION`：8.0 的 `TABLE_ROWS` 来自统计缓存，`information_schema_stats_expiry` 默认 86400 秒，最多旧一天。2026-11-22 在本机 8.0.46 上实测：用 root 往空表插入 10 万行（不执行 `ANALYZE TABLE`），`t2s_ro` 在默认设置下读到 `0`，同一会话 `SET SESSION information_schema_stats_expiry = 0` 后读到 `100464`，换新会话又是 `0`。`t2s_ro` 只有 `SELECT` 权限也能设置这个会话变量。
- **不执行 `ANALYZE TABLE`**：它要写统计表，`t2s_ro` 是只读账号，而且对大表本身就有成本。
- 设置留在这条连接的会话里，连接回到连接池后仍然有效。这只影响之后在这条连接上读 `information_schema` 的结果，让结构采集读到的 `TABLE_ROWS` 也更新，没有副作用。
- `TABLE_ROWS` 仍是 InnoDB 的估计值：持久化统计在表变化超过约 10% 后才在后台重算，可能有偏差。所以行数估计只用来分流，真正兜底的还是 `MAX_EXECUTION_TIME` 和 `LIMIT`：即使估计偏小、大表被当成普通表，单条查询也会被截断并记 `TIMEOUT`。

**列筛选**（`value_index.plan_value_columns`，纯函数，每个文本列一条 `ColumnDecision(table, column, tenant_column, skip_reason, path)`，`path ∈ {"normal", "big_table"}`）。只考虑 `char`/`varchar`/`enum`/`*text` 类型；按下表顺序判断，第一条命中即为跳过原因：

| 跳过原因 | 规则 |
| --- | --- |
| `EXCLUDED` | 在 exclude 清单中（总是优先） |
| `VIEW` | 视图 |
| `TABLE_GONE` | 快照里有、构建时现读的 `TABLE_ROWS` 结果里没有的基表（快照之后被删除或改名） |
| `NO_TENANT_KEY` | 开启租户规则时，表没有 `value_index_tenant_column` 列 |
| `KEY_COLUMN` | 主键、单列唯一、外键列、租户列 |
| `TYPE_TOO_LONG` | `*text` 类型，或声明长度 > 255（`enum` 除外） |
| `NAME_PATTERN` | 列名以 `_id`/`_no`/`_code`/`_key`/`token`/`password`/`hash`/`url`/`email`/`phone`/`mobile`/`address`/`ip`/`card`/`bank`/`account` 等结尾 |
| `SENSITIVE_HINT` | 注释含 手机、电话、邮箱、地址、身份证、证件、姓名、密码、银行卡 |
| `BIG_TABLE_NO_INDEX` | 大表（行数估计 > `value_index_max_rows`）上没有「大表索引路径」：需要一个 `index_type = BTREE` 的索引，前导列依次是 `(租户列, 本列)`（关闭租户规则时本列是第一列），且这几列都是整列索引（`IndexInfo.sub_parts` 对应位置为 `None`；前缀索引不能做松散扫描） |

- **`path`**：行数估计不超过 `value_index_max_rows`（或为 `NULL`）的表，列的 `path = "normal"`；超过的表 `path = "big_table"`，采集器据此先做 `EXPLAIN`。判断所需的索引信息都在快照里（4.4 `IndexInfo.columns` 有序、`sub_parts`、`index_type`），`plan_value_columns` 仍是纯函数。
- **顺序**：原来整表跳过的 `TOO_MANY_ROWS` 排在第三；改成逐列判断后放在最后，因为它是唯一能靠「加索引」解决的原因，排最后保证报出它时其他规则都已通过。例如大表上的 `remark TEXT` 报 `TYPE_TOO_LONG`，不会误导人去建索引。
- **include / exclude**：include 清单只放宽 `TYPE_TOO_LONG` / `NAME_PATTERN` / `SENSITIVE_HINT` 三条软规则；`VIEW`、`TABLE_GONE`、`NO_TENANT_KEY`、`KEY_COLUMN`、`BIG_TABLE_NO_INDEX`（以及采集期的 `NO_LOOSE_SCAN`）不可放宽，被 include 放宽的列在大表上仍要有索引。Spec 05 `columns.yaml` 的 `value_index: true|false` 和 `sensitivity` 生成 include/exclude（05 §4.1），在 Spec 05 之前用 CLI 参数传入。

**采集**（`collector/values.py`，`t2s_ro` 只读引擎）：每个合格列一条查询，普通表和大表用同一条 SQL：

```sql
-- 开启租户规则
SELECT /*+ MAX_EXECUTION_TIME(:timeout_ms) */ DISTINCT `tenant_id` AS tenant_key, `col` AS value
FROM `t` WHERE `col` IS NOT NULL AND `col` <> '' AND CHAR_LENGTH(`col`) <= :max_length
LIMIT :max_distinct + 1

-- 关闭租户规则：不选租户列，tenant_key 记 NULL
SELECT /*+ MAX_EXECUTION_TIME(:timeout_ms) */ DISTINCT `col` AS value
FROM `t` WHERE `col` IS NOT NULL AND `col` <> '' AND CHAR_LENGTH(`col`) <= :max_length
LIMIT :max_distinct + 1
```

- **`DISTINCT` 只放裸列。** 租户键在 Python 里转成字符串（`str()`，`bytes` 按 UTF-8 解码），不在 SQL 里 `CAST(... AS CHAR)`；关闭租户规则时也不写 `NULL AS tenant_key`。原因：松散索引扫描要求 `DISTINCT` 的每一项都是索引最左前缀里的列（MySQL 手册 [GROUP BY Optimization](https://dev.mysql.com/doc/refman/5.7/en/group-by-optimization.html)：「all distinct attributes refer to columns that form a leftmost prefix of the index」），`CAST` 和常量都会让它退化成全索引扫描（2026-11-22 本机 5.7.44 / 8.0.46、10 万行表实测，见 research 02）。
- **大表先 `EXPLAIN`。** `path = "big_table"` 的列先在同一个 `t2s_ro` 连接上执行 `EXPLAIN FORMAT=TRADITIONAL <同一条 SQL>`（同样的绑定参数）。任一行的 `Extra` 含 `Using index for group-by` 才执行查询；否则这一列记 `NO_LOOSE_SCAN`，不执行。`EXPLAIN` 不读数据行；写 `FORMAT=TRADITIONAL` 是为了不受 8.0.32+ 的 `explain_format` 变量影响，5.7 也支持这个写法。
- **小表不做 `EXPLAIN`。** 小表即使有合适的索引，优化器也常选全索引扫描（成本更低），普通表照旧直接执行。
- **`WHERE` 过滤与松散扫描。** 本机实测 `IS NOT NULL` / `<> ''` / `CHAR_LENGTH(...) <= n` 不影响松散扫描（`Extra` 为 `Using where; Using index for group-by`），所以大表也用同一条 SQL。**[待 Task 15 在 5.7 / 8.0 夹具上再次 EXPLAIN 确认]** 如果某个版本上它让松散扫描失效，`EXPLAIN` 门槛会把列记为 `NO_LOOSE_SCAN`，不会退化成全表扫描；届时再把大表 SQL 改为不带 `WHERE`、在 Python 里过滤空值和超长值。
- 返回超过 `max_distinct` 行 → 该列记 `TOO_MANY_VALUES`，不收值（不是低基数列）。
- 服务端超时（MySQL 3024 / MariaDB 1969）→ 该列记 `TIMEOUT`，构建继续；其他错误使构建失败。
- 标识符来自快照并用反引号转义，值全部绑定参数。
- **行数估计只是成本分流，不是安全边界**：见上文「构建时现读行数」；估计偏差时由 `MAX_EXECUTION_TIME` 截断并记 `TIMEOUT`。

**不采纳**（理由见 research 02）：
- **抽样**：5.7 没有 `TABLESAMPLE`；`ORDER BY RAND()` 本身要全表扫描；`LIMIT N` 偏向聚簇索引靠前的旧数据，会漏掉新出现的值。
- **依赖 8.0 直方图**（`information_schema.COLUMN_STATISTICS`）：建直方图要 `ANALYZE TABLE ... UPDATE HISTOGRAM`，需要 `SELECT` 以外的权限，`t2s_ro` 做不了，只能读 DBA 已建的；5.7 没有直方图。阶段 2 可作为只读补充。
- 大表上没有合适索引的枚举列，MVP 记 `BIG_TABLE_NO_INDEX`；Spec 05 落地后，可在 `columns.yaml` 用 `values` 直接声明取值（05 §4.1），不扫业务库。

**存储**（迁移 `0002_metadata` 内，与其他元数据表一起建；迁移开头 `CREATE EXTENSION IF NOT EXISTS pg_trgm`，回滚不删扩展）：

| 表 | 列 | 约束 / 说明 |
| --- | --- | --- |
| `metadata_value_build` | `id` identity；`datasource_id` FK；`snapshot_id` FK；`indexed_column_count`、`skipped_column_count`、`value_count` INT；`created_at`；`created_by`；`trace_id` | 索引 `(datasource_id, id)`；一次构建一行 |
| `metadata_value_column` | `id`；`build_id` FK `ON DELETE CASCADE`；`column_key VARCHAR(300)`（`t.c`）；`skip_reason VARCHAR(32)` 可空；`value_count` | 唯一 `(build_id, column_key)`；跳过的列也记录，便于审计 |
| `metadata_value` | `id`；`build_id`、`column_id` FK `ON DELETE CASCADE`；`tenant_key VARCHAR(64)` 可空；`value TEXT`；`value_norm TEXT` | 索引 `(build_id, value_norm)`；GIN `value_norm gin_trgm_ops` |

- 值索引表**可变**（不是快照表）：一次构建在一个事务里写完，读方看到的要么是旧构建、要么是完整的新构建；每个数据源只保留最近 2 次构建，更早的整批删除（CASCADE）。
- `value_norm = casefold(NFKC(value))` 并压缩空白。

**检索**（`catalog/value_store.py`）：

```python
@dataclass(frozen=True)
class ValueHit:
    column_key: str      # "t.c"
    value: str           # 原值，交给 IR filter 使用
    score: float         # 精确 = 1.0；模糊 = word_similarity
    exact: bool

def save_value_build(session, *, datasource_id, snapshot_id, decisions, collected, created_by) -> int: ...
def latest_value_build(session, datasource_id) -> ValueBuildSummary | None: ...
def search_values(session, *, datasource_id: str, text_value: str, tenant_key: str | None,
                  limit: int = 10, min_score: float = 0.6, columns: Collection[str] | None = None) -> list[ValueHit]: ...
```

- **精确**：问题归一化后的所有 2–16 字子串里，等于某个 `value_norm` 的（"华东区上月工单" → `华东区`）。
- **模糊**：问题按 `\w+` 切出的片段（≤ 32 字），`span <% value_norm`（`word_similarity ≥ min_score`，走 GIN 索引）（"华东精密" → `华东精密有限公司`，0.8）。
- **租户隔离**：`tenant_key` 必填语义——传租户只返回该租户的值；传 `None` 只返回无租户列表的值。值索引是业务库之外的副本，不受数据库层租户视图保护，所以租户过滤在存储层强制，Spec 08 必须传 `principal.tenant_id`。
- 数据源还没有构建时返回空列表（检索方回退到原有路径）；CLI 报 `VALUE_INDEX_NOT_FOUND`。
- 命中结果是**不可信数据**（与注释同级，见 AGENTS.md 规则 6）：进入 LLM 上下文时放在数据区、只作候选，最终值仍由 Spec 08 A5 经网关确认。

**Spec 05 声明值**（05 §4.1 `values`）：声明的取值不查业务库，作为「声明列」进入构建，存为 `tenant_key = NULL`、对所有租户可见（只允许写跨租户通用的枚举值）。这要求 `save_value_build` 接收声明值、`search_values` 在租户过滤之外返回声明值，在 Spec 05 实现时修改本节，本 Spec MVP 不实现。

**MVP / 阶段 2**：MVP 做上述规则、构建、精确 + 模糊检索、CLI 和队列任务。阶段 2：同义词/拼音、embedding 召回、增量构建、按列的刷新频率、值频次排序。

### 4.12 错误码

| 错误码 | HTTP 状态（Spec 09 暴露接口时使用） | 场景 |
| --- | --- | --- |
| `METADATA_NO_DATABASE` | 422 | `business_db_url` 没有默认库 |
| `METADATA_UNSUPPORTED_VERSION` | 422 | 版本低于 5.7.8、无法解析，或 `target_key()` 遇到 5.7/8.0 以外的版本 |
| `METADATA_TOO_LARGE` | 422 | 超过 2000 表或 50000 字段 |
| `METADATA_SNAPSHOT_NOT_FOUND` | 404 | 快照 ID 不存在，或数据源还没有快照 |
| `METADATA_OBJECT_NOT_FOUND` | 404 | CLI 指定的表/字段不在快照中 |
| `RELEASE_NOT_FOUND` | 404 | release 不存在，或数据源没有活跃 release |
| `RELEASE_INVALID` | 409 | release/快照不属于该数据源，或 release 已 `REJECTED` |
| `RELEASE_GATE_REQUIRED` | 409 | `T2S_ENV=prod` 时调用不带门禁的激活 |
| `STALE_MARK_NOT_FOUND` | 404 | 解决一个不存在或已解决的 STALE 标记 |
| `VALUE_INDEX_NOT_FOUND` | 404 | 数据源还没有值索引构建（CLI `values show` / `values search`） |

4.11 里 `run_value_index` 在数据源没有快照时抛 `METADATA_SNAPSHOT_NOT_FOUND`（不另设错误码）；值采集超时不是错误，记为该列的 `TIMEOUT` 跳过原因；大表没有松散索引扫描同样不是错误，记为 `NO_LOOSE_SCAN`。

## 5. 验收标准

1. `uv run alembic upgrade head` 创建本 Spec 的全部表与触发器；`downgrade` 到 `0001` 可回滚。
2. 对 `mysql57` 与 `mysql80` 分别执行 `sync`（集成测试，使用 Spec 03 的合成库；03 未就绪前使用测试内建的 3 张表夹具，含复合唯一索引、外键、视图、带注入文本的注释；夹具由 root 账号（`T2S_TEST_MYSQL57_ADMIN_URL` / `T2S_TEST_MYSQL80_ADMIN_URL`）在独立库 `t2s_meta_fixture` 中创建，并授予 `t2s_ro` SELECT；不改动 `t2s_business`）：快照中的表、字段、PK、唯一索引、外键与夹具定义逐项一致；`server.version` 主版本分别为 5.7 与 8.0；`sql_mode`、`time_zone`、字符集、`lower_case_table_names` 非空。
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
13. （M9）`t_order`、`t_order_2023`、`t_order_bak` 列相同 → 一个表族，代表表 `t_order`；多一列的同前缀表或视图不入族；没有不带后缀的表时名字最大的分表做代表；`created_at` 出现在 ≥ 5 张且过半基表时列为公共列，主键 `id` 不算（单元测试）。
14. （M10 规则）键列、外键列、租户列、`*_code`/`*_phone` 等列名、注释含"手机"的列、视图、无租户列的表都被跳过并给出对应原因；exclude 优先于 include；include 不能放宽键列（单元测试）。
15. （M10 采集）夹具库 `t_customer` 插入两个租户的数据后 `values build`：`t_customer.name` 被索引且每个值带租户键（租户键由 Python 转成字符串）；`max_distinct` 小于实际值数时该列记 `TOO_MANY_VALUES`（集成测试，5.7 / 8.0）。
16. （M10 检索）`search_values` 对"华东精密"返回租户 1001 的 `华东精密有限公司`（模糊），对租户 1002 返回空；精确子串命中排在前面；只保留最近 2 次构建；迁移后 `pg_extension` 中有 `pg_trgm`（集成测试）。
17. （M10 大表规则，2026-11-22）`row_estimate` 超过 `max_rows` 的表：有 `(tenant_id, status)` 整列 BTREE 索引的 `status` 通过且 `path = "big_table"`；没有索引、索引里本列不在租户列之后、或是前缀索引的列记 `BIG_TABLE_NO_INDEX`；关闭租户规则时 `(status)` 单列索引即可；include 不能放宽 `BIG_TABLE_NO_INDEX`；小表的列 `path = "normal"`（单元测试）。
18. （M10 大表采集，2026-11-22）`path = "big_table"` 的列：伪造的 `EXPLAIN` 结果不含 `Using index for group-by` 时记 `NO_LOOSE_SCAN` 且不执行取值查询，含时才执行（单元测试）；夹具库建 10 万行、带 `(tenant_id, status)` 索引的表并 `ANALYZE TABLE` 后，`status` 走索引路径被收集、按租户分开，同表无索引的列记 `BIG_TABLE_NO_INDEX`（集成测试，5.7 / 8.0）。
19. （M10 现读行数，2026-11-22）`plan_value_columns` 的 `row_estimates`：快照 1000 行、现读 500 万行的表按大表处理（`path = "big_table"`，无索引的列记 `BIG_TABLE_NO_INDEX`）；快照里有、现读结果里没有的表记 `TABLE_GONE`；现读值为 `None` 时回退到快照值（单元测试）。`live_row_estimates` 在 8.0 上先执行 `SET SESSION information_schema_stats_expiry = 0`、5.7 上不执行，查询带绑定参数 `:db`（单元测试）；夹具大表的现读值 > 1000（集成测试，5.7 / 8.0）。

## 6. 新增依赖

无（pydantic、SQLAlchemy、PyMySQL、psycopg 已由 Spec 01 引入）。

## 7. 风险与说明

- **不可重试的任务失败**：参数或配置不匹配（如 `datasource_id` 不一致）重试无意义，handler 抛出 Spec 01 F6 的 `text2sql.jobs.queue.NonRetryableJobError`，worker 直接置 `failed`。本 Spec 不修改队列。
- **采集账号权限**：`t2s_ro` 只有 `SELECT`，看不到视图定义（需 `SHOW VIEW`）。MVP 合成库不在业务库内放视图，不受影响；真实库若视图承载业务逻辑（架构 17.3），需要 DBA 授予 `SHOW VIEW`（开放决策，见下）。
- **`TABLE_ROWS` 仅为估计值**，InnoDB 上误差可能很大，只用于展示和成本提示，不进入任何正确性判断。
- **快照去重的副作用**：结构不变时沿用旧快照，`row_estimate` 也停在旧值。值索引不受影响，它在构建时现读 `TABLE_ROWS`（4.11）。阶段 2 引入 Data Profiling 时单独存储统计信息。
- **值索引的合规**（架构 11.6）：值索引把业务数据的一部分复制到 PG。筛选规则跳过键列、敏感名称和注释，但规则是启发式的；上线真实库前需要数据负责人过一遍 `values show` 的结果，必要时在 Spec 05 `columns.yaml` 中标 `value_index: false`。
- **值索引的租户隔离**：只索引带租户列的表（或显式关闭租户规则的单租户库），检索时强制按租户过滤。若真实库的租户列名不统一，需要扩展为按表配置（开放决策 3）。
- **`pg_trgm` 与中文**：trigram 对 CJK 的支持取决于数据库的 locale/ctype。已实测 PG 16（`en_US.utf8`）和 PG 17（`C.UTF-8`）对中文可用（`word_similarity('华东精密','华东精密有限公司') = 0.8`）。`pg_trgm` 从 PG 13 起是 trusted 扩展，库属主即可创建；生产库若禁止，需要 DBA 预先创建。
- **扫描成本**：每列一条带 `MAX_EXECUTION_TIME` 和 `LIMIT` 的 `DISTINCT` 查询，只在副本上、离线执行。超过 `value_index_max_rows` 的大表只走索引路径（整列 BTREE 索引最左前缀 + `EXPLAIN` 确认松散索引扫描），成本与 distinct 值个数成正比、与行数无关；没有合适索引的大表列不采（4.11）。
- **单库假设**：一个数据源只采集一个库。真实库是多 schema 时（1.3 待填），需要在对象键中加库名，属于后续 Spec 的变更。
- **开放决策（需用户确认）**：
  1. 真实目标库画像（架构 1.3）：版本、表/字段规模、是否有 `SHOW VIEW` 权限、`lower_case_table_names`。本 Spec 的规模上限（2000 表 / 50000 字段）是 MVP 防护值，按画像调整。
  2. 注释复核人（架构 1.4 未填）：`review-comment` 的 `--reviewer` 由谁担任。
  3. 值索引（2026-11-22 新增）：真实库的租户列名是否统一为一个；值索引命中的值能否作为候选放进 LLM 上下文（本 Spec 默认：非敏感列可以，敏感列从不入索引）。
