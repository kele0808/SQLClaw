# Spec 05：Semantic Layer 语义层

- 状态：已确认（2026-11-20）
- 日期：2026-11-20
- 对应架构文档：第七章（7.1～7.4 及修订）、9.6（默认口径）、11.5（敏感字段清单）、1.4（指标定义人与审核人）、5.5（Release 组成）、第二十三章（第 3 周）
- 依赖：Spec 01、Spec 02（快照、release、`StaleSet`）、Spec 04（边 ID、`tree_from_edges`、`JoinGraph`、`semantic_dir`）
- 被依赖：06（指标、维度、枚举、默认值）、07（字段敏感级别与脱敏规则、租户键）、08（术语与同义词、实体查找）、09（影响分析）

## 1. 目标

用版本库中的 YAML 定义指标、维度、枚举、术语、字段注解和默认口径，加载时对照快照与 Join Graph 严格校验，存为不可变的语义版本；并提供把"快照 + Join Graph + 语义层"组装成一个 release 的入口与运行时的 `ReleaseBundle`。指标只能来自这里（AGENTS.md 硬规则），LLM 不能自行定义。

## 2. 范围

### 2.1 本期做

| 编号 | 能力 | 说明 |
| --- | --- | --- |
| S1 | 定义文件格式 | `metrics/`、`dimensions/`、`enums.yaml`、`terms.yaml`、`columns.yaml`、`defaults.yaml` |
| S2 | 加载与校验 | pydantic 结构校验 + 对照快照/Join Graph 的引用校验 + 状态规则 |
| S3 | 状态机 | `DRAFT → REVIEWING → VERIFIED → DEPRECATED`，VERIFIED 需定义人 ≠ 审核人 |
| S4 | 语义版本存储 | `semantic_version` 不可变记录 |
| S5 | 合成库语义模型 | `ticket_demo` 的 11 个指标、10 个维度及枚举、术语、注解 |
| S6 | Release 组装 | `build_candidate_release()`：快照 + Join Graph 版本 + 语义版本 |
| S7 | ReleaseBundle | 运行时按 release 加载并缓存（架构 17.6 的 Schema Cache） |
| S8 | CLI | 校验、查看、PR 检查、构建 release |

### 2.2 本期不做

- 指标审核页面与审批流（MVP 用 Pull Request，架构 7.4）
- 从历史报表 SQL 抽取候选指标（阶段 2）
- Semantic Evolution 自动检测（阶段 2）；MVP 的语义变更即"改 YAML + 提 PR + 构建新 release"
- 指标表达式中的任意 SQL 片段（只允许列 + 聚合 + 过滤，原因见 7）
- 语义层监控指标（VERIFIED 数、DRAFT 积压、审核时长；阶段 2 的看板，MVP 用 `semantic stats` 命令打印）

## 3. 目录与文件

```
semantic_definitions/ticket_demo/
├── model.yaml              # 模型名、对应库名、说明
├── defaults.yaml           # 默认时间范围、默认 LIMIT、默认过滤（软删除）
├── joins.yaml              # Spec 04
├── columns.yaml            # 字段注解：说明、同义词、废弃、敏感级别、租户键
├── enums.yaml              # 枚举值与标签
├── terms.yaml              # 业务术语 → 候选对象
├── metrics/*.yaml          # 每个指标一个文件
└── dimensions/*.yaml       # 每个维度一个文件
src/text2sql/semantic/
├── __main__.py / cli.py    # S8
├── models.py               # S1 pydantic 模型
├── loader.py               # S2
├── validation.py           # S2/S3
├── catalog.py              # SemanticModel 查询接口
└── store.py                # S4
src/text2sql/release/
├── __main__.py / cli.py    # S8 release 子命令
├── builder.py              # S6
└── bundle.py               # S7
migrations/versions/0005_semantic.py
tests/unit/semantic/…
tests/integration/semantic/…
```

## 4. 详细设计

### 4.1 S1 定义文件

所有对象共有字段：`name`（`^[a-z][a-z0-9_]{1,63}$`，同类唯一）、`display_name`、`description`、`synonyms: list[str]`、`owner`、`reviewer`、`reviewed_at: date | null`、`status`。

**指标**（`metrics/<name>.yaml`，顶层键沿用架构 7.1 的 `metric:`）：

```yaml
metric:
  name: avg_process_duration
  display_name: 平均处理时长（每张工单）
  description: 每张工单所有处理记录 duration 之和，再对工单求平均；无处理记录的工单不参与
  type: simple                    # simple | ratio
  expression:
    aggregation: avg              # count | count_distinct | sum | avg | min | max
    table: t_work_process
    column: duration
    per:                          # 可选：先按 per.table 的每一行预聚合（架构 6.5）
      table: t_work_order
      via_edge: process_work_order
      aggregation: sum
  filters: []                     # 见下
  time_dimension:
    table: t_work_order           # 可选，默认为指标的锚表（见下）
    column: create_time
  unit: 秒
  value_range: [0, null]          # 可选，供结果校验（Spec 08）
  synonyms: [处理时长, 平均处理时间]
  owner: <指标定义人>
  reviewer: <指标审核人>
  reviewed_at: 2026-12-04
  status: VERIFIED
```

- **锚表**：没有 `per` 时为 `expression.table`；有 `per` 时为 `per.table`。维度路径、时间字段、默认过滤都相对锚表解释。
- `per` 语义：先 `SELECT <via_edge 的 from 列>, <per.aggregation>(column) FROM expression.table GROUP BY <from 列>`，得到与锚表一对一的预聚合结果，再在锚表粒度上做外层 `aggregation`。`via_edge` 必须是从 `expression.table` 出发的 FORWARD 边（不扇出），到达 `per.table`。
- `filters`：`[{table?, column, operator, value}]`，`operator ∈ {=, !=, >, >=, <, <=, in, not_in, is_null, is_not_null}`；`table` 默认 `expression.table`，只能是 `expression.table` 或锚表；`in`/`not_in` 的 `value` 为列表。
- `time_dimension` 必填（每个指标都必须有默认时间字段，架构 9.6"时间字段"一行据此不需要澄清）；其表必须是锚表，或从锚表沿 FORWARD 边可达的表。
- `count`/`count_distinct` 的 `column` 可以是主键；`sum`/`avg` 的列必须是数值类型。

**比率指标**（架构 7.1 新增示例）：

```yaml
metric:
  name: work_order_completion_rate
  display_name: 工单完成率
  type: ratio
  numerator: created_completed_work_order_count
  denominator: work_order_count
  time_dimension: {column: create_time}
  value_range: [0, 1]
  owner: <指标定义人>
  reviewer: <指标审核人>
  status: VERIFIED
```

比率的分子、分母必须是 `simple` 指标，二者的锚表、`time_dimension`（表与列）与比率声明的 `time_dimension` 完全相同，否则校验失败 `RATIO_TIME_MISMATCH`。这是架构 7.1 修订指出的口径冲突在加载期的硬校验：不能靠运行期默默选一个。

**维度**（`dimensions/<name>.yaml`）：

```yaml
dimension:
  name: department
  display_name: 部门
  source: {table: t_department, column: department_name, key_column: id}
  join_paths:
    - role: HANDLER_DEPARTMENT
      from_table: t_work_order
      edges: [work_order_handler, employee_department]
      default: true
      label: 按工单处理人所在部门
    - role: CREATOR_DEPARTMENT
      from_table: t_work_order
      edges: [work_order_creator, employee_department]
      label: 按工单创建人所在部门
    - role: OPERATOR_DEPARTMENT
      from_table: t_work_process
      edges: [process_operator, employee_department]
      default: true
      label: 按处理记录操作人所在部门
  allows_fanout: false
  entity_lookup: null
  synonyms: [科室, 团队]
  owner: <指标定义人>
  reviewer: <指标审核人>
  status: VERIFIED
```

- `source.key_column` 可选；有则按键分组并输出标签（同名不同实体不会被合并），无则按 `source.column` 分组。
- `join_paths` 按 `from_table`（指标锚表）分组，每组最多一个 `default: true`。`source.table` 等于锚表时不需要路径（例如工单状态）。
- `edges` 必须能用 `tree_from_edges(graph, from_table, edges)` 从 `from_table` 连到 `source.table`。全部为 FORWARD 步时是普通维度；含扇出步时（例如经桥接表的标签）必须声明 `allows_fanout: true`，Spec 06 对这类维度限制可用的聚合。
- 架构 7.1 的 `join_path: [t_work_order.industry_id, t_industry.id]` 写法等价于一条 `default: true`、只含 `work_order_industry` 边的路径；本格式用边 ID 代替列名，原因是同一对列只对应一条边，用 ID 更短、且边被标记 STALE 时可以直接查依赖。
- `entity_lookup`：`{match: exact | contains, max_candidates: 10}`，声明该维度的值可以被问题中的名称引用（如客户名），Spec 08 据此查业务库解析实体并处理重名。

**枚举**（`enums.yaml`）：

```yaml
enums:
  - column: t_work_order.status
    values:
      - {code: 1, label: 新建, synonyms: [待处理]}
      - {code: 2, label: 处理中}
      - {code: 3, label: 已完成, synonyms: [完成, 已解决]}
      - {code: 4, label: 已取消}
      - {code: 5, label: 已关闭}
    owner: <指标定义人>
    reviewer: <指标审核人>
    status: VERIFIED
```

**术语**（`terms.yaml`）：

```yaml
terms:
  - term: 处理效率
    candidates:
      - {kind: metric, ref: avg_process_duration}
      - {kind: metric, ref: work_order_completion_rate}
    default: null                  # 无默认 → Spec 08 触发 METRIC_DEFINITION 澄清
  - term: 工单量
    candidates: [{kind: metric, ref: work_order_count}]
    default: work_order_count
```

`kind ∈ {metric, dimension, enum_value, table}`；`enum_value` 的 `ref` 形如 `t_work_order.status=3`。

**字段注解**（`columns.yaml`）：

```yaml
columns:
  t_work_order.tenant_id: {role: tenant_key}
  t_employee.phone: {sensitivity: PII, masking: PHONE}
  t_employee.email: {sensitivity: PII, masking: EMAIL}
  t_customer.contact_phone: {sensitivity: PII, masking: PHONE}
  t_work_order.legacy_status: {deprecated: true, note: 历史字段，以 status 为准}
  t_customer.industry_name_old: {deprecated: true, note: 已由 industry_id 取代}
```

```python
class ColumnAnnotation(BaseModel, frozen=True):
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    deprecated: bool = False
    note: str = ""
    sensitivity: Literal["NONE", "PII", "SECRET"] = "NONE"
    masking: Literal["PHONE", "EMAIL", "FULL"] | None = None    # PII 必填；SECRET 不出结果，不需要
    role: Literal["tenant_key"] | None = None
```

- 每张带 `tenant_id` 的表都必须把 `tenant_id` 标为 `role: tenant_key`（校验 `TENANT_KEY_MISSING`，按快照中名为 `tenant_id` 的列检查）。Spec 06 禁止 IR 在租户键上过滤或分组，Spec 07 禁止 SQL 引用租户键以外的租户库。
- 敏感字段清单是架构 1.3 / 11.5 的输入。合成库按上表标注；真实库由安全/合规负责人提供（开放决策）。

**默认口径**（`defaults.yaml`）：

```yaml
default_time_range: {type: RELATIVE, value: 30, unit: DAY}
default_limit: 100
default_filters:
  t_work_order: [{column: is_deleted, operator: "=", value: 0}]
  t_customer:   [{column: is_deleted, operator: "=", value: 0}]
  t_employee:   [{column: is_deleted, operator: "=", value: 0}]
  t_department: [{column: is_deleted, operator: "=", value: 0}]
```

默认过滤由 Spec 06 对查询涉及的每张表自动施加，并记为 `DEFAULT_FILTER` 口径。

### 4.2 S2/S3 加载、校验与状态规则

```python
@dataclass(frozen=True)
class SemanticIssue:
    object_type: Literal["METRIC", "DIMENSION", "ENUM", "TERM", "COLUMN", "DEFAULTS"]
    name: str
    code: str
    message: str
    severity: Literal["ERROR", "WARNING"]

class SemanticLoadError(AppError): issues: tuple[SemanticIssue, ...]

def load_definitions(semantic_dir: Path) -> SemanticDefinitions: ...           # 结构校验
def build_semantic_model(defs: SemanticDefinitions, snapshot: SchemaSnapshot,
                         graph: JoinGraph) -> SemanticModel: ...               # 引用与规则校验，有 ERROR 即抛错
```

校验码（ERROR，除特别注明）：

| 校验码 | 规则 |
| --- | --- |
| `UNKNOWN_TABLE` / `UNKNOWN_COLUMN` | 引用的表与列在快照中存在 |
| `DEPRECATED_COLUMN_USED` | 指标、维度、枚举不得引用 `deprecated` 字段 |
| `SENSITIVE_COLUMN_USED` | 指标与维度不得引用 `PII`/`SECRET` 字段（`count`/`count_distinct` 除外） |
| `TENANT_KEY_USED` | 指标过滤、维度不得引用租户键 |
| `TENANT_KEY_MISSING` | 见 4.1 |
| `UNKNOWN_EDGE` / `PATH_BROKEN` | 维度路径与 `per.via_edge` 的边存在且连续 |
| `PER_EDGE_FANS_OUT` | `per.via_edge` 必须是 FORWARD 步 |
| `FANOUT_NOT_DECLARED` | 路径含扇出步但未声明 `allows_fanout` |
| `MULTIPLE_DEFAULT_PATHS` | 同一 `from_table` 多于一个默认路径 |
| `TIME_FIELD_UNREACHABLE` | 时间字段表不是锚表且不能从锚表经 FORWARD 边到达；或类型不是 `date`/`datetime`/`timestamp` |
| `AGGREGATION_TYPE` | `sum`/`avg` 用于非数值列 |
| `RATIO_COMPONENT` | 比率的分子分母不存在或不是 `simple` |
| `RATIO_TIME_MISMATCH` | 见 4.1 |
| `VERIFIED_DEPENDS_ON_UNVERIFIED` | VERIFIED 比率不能引用非 VERIFIED 的分子分母 |
| `REVIEW_INCOMPLETE` | VERIFIED 对象必须有 `owner`、`reviewer`、`reviewed_at`，且 `owner != reviewer`（架构 1.4：审核人不能是定义人） |
| `ENUM_CODE_TYPE` | 枚举 code 的类型与字段类型族一致 |
| `TERM_REF` | 术语候选引用的对象存在；`default` 必须是候选之一 |
| `SYNONYM_CONFLICT`（WARNING） | 同一同义词指向多个对象，且没有对应的 `terms.yaml` 条目说明 |

状态规则（架构 7.2 修订）：

| 状态 | 可否用于查询 | 结果标注 |
| --- | --- | --- |
| `VERIFIED` | 可以，可作默认口径 | 无额外标注 |
| `REVIEWING` / `DRAFT` | 可以 | Spec 06 写入 `METRIC_STATUS`（指标）或 `DEFINITION_STATUS`（维度/枚举）口径，文案"口径未经业务确认" |
| `DEPRECATED` | 不可以 | — |

PR 检查（`semantic check-pr --base origin/main`）：对比基线与当前工作区，凡是新变为 VERIFIED 或 VERIFIED 对象内容有变化的，`reviewed_at` 必须晚于基线中的值，`owner != reviewer`。PR 的审批本身由代码托管平台保证；本检查只防止"改了口径却沿用旧审核记录"。

### 4.3 SemanticModel 查询接口（`text2sql.semantic.catalog`）

```python
class SemanticModel:
    name: str
    content_hash: str
    defaults: Defaults
    def metric(self, name: str) -> MetricDef: ...                # 不存在抛 KeyError
    def metrics(self, *, include_deprecated: bool = False) -> tuple[MetricDef, ...]: ...
    def dimension(self, name: str) -> DimensionDef: ...
    def dimensions(self, *, include_deprecated: bool = False) -> tuple[DimensionDef, ...]: ...
    def anchor_table(self, metric: str) -> str: ...
    def join_paths(self, dimension: str, from_table: str) -> tuple[JoinPathDef, ...]: ...
    def default_join_path(self, dimension: str, from_table: str) -> JoinPathDef | None: ...
    def enum(self, column_key: str) -> EnumDef | None: ...
    def terms(self) -> tuple[TermDef, ...]: ...
    def column(self, column_key: str) -> ColumnAnnotation: ...   # 未注解返回默认值
    def tenant_key_columns(self) -> frozenset[str]: ...
    def dependencies(self) -> Mapping[tuple[ObjectType, str], frozenset[tuple[ObjectType, str]]]: ...
```

`dependencies()` 返回"语义对象 → 它依赖的表、字段、边、指标"，Spec 06 用它判断对象是否因 STALE 不可用，Spec 09 用它做影响分析。

### 4.4 S4 语义版本存储

**`semantic_version`**（不可变，触发器禁止更新删除）：`id` BIGINT identity；`datasource_id`；`snapshot_id` FK；`join_graph_version_id` FK；`model_name`；`content_hash CHAR(64)`（规范化定义的 sha256）；`git_commit VARCHAR(40)` 可空（构建时读取 `git rev-parse HEAD`，非 Git 环境为空）；`definitions JSONB`（规范化后的全部定义）；`created_by`；`created_at`；`trace_id`。唯一 `(datasource_id, snapshot_id, join_graph_version_id, content_hash)`。迁移同时给 `metadata_release.semantic_version_id` 加外键。

### 4.5 S5 合成库语义模型 `ticket_demo`

指标（11 个，均为 VERIFIED，`owner`/`reviewer` 为合成模型的演示角色 `demo-definer` / `demo-reviewer`，不代表真实负责人）：

| 指标 | 定义 | 时间字段 |
| --- | --- | --- |
| `work_order_count` | `count_distinct(t_work_order.id)` | `t_work_order.create_time` |
| `created_completed_work_order_count` | 同上，过滤 `status = 3` | `t_work_order.create_time` |
| `completed_work_order_count` | 同上，过滤 `status = 3` | `t_work_order.complete_time` |
| `cancelled_work_order_count` | 同上，过滤 `status = 4` | `t_work_order.create_time` |
| `work_order_completion_rate` | 比率：`created_completed_work_order_count / work_order_count`，`value_range [0,1]` | `t_work_order.create_time` |
| `avg_process_duration` | 每工单 `sum(duration)` 后 `avg`（4.1 示例） | `t_work_order.create_time` |
| `total_process_duration` | `sum(t_work_process.duration)` | `t_work_order.create_time`（经 `process_work_order`） |
| `process_record_count` | `count_distinct(t_work_process.id)` | `t_work_process.process_time` |
| `customer_count` | `count_distinct(t_customer.id)` | `t_customer.created_at` |
| `avg_satisfaction_score` | `avg(t_satisfaction.score)`，`value_range [1,5]` | `t_satisfaction.created_at` |
| `satisfaction_count` | `count_distinct(t_satisfaction.id)` | `t_satisfaction.created_at` |

"已完成工单数"拆成按创建时间（用于完成率）和按完成时间两个指标，正是对架构 7.1 修订所指冲突的演示解法：两种口径都显式命名，由术语表决定默认（`已完成工单数` 默认 `completed_work_order_count`，`完成率` 只有一个比率指标）。真实业务的取舍需要业务方决定（第 7 节）。

维度（10 个）：

| 维度 | 来源 | 路径（`from_table` → 角色，`*` 为默认） |
| --- | --- | --- |
| `industry` | `t_industry.industry_name`（键 `id`） | `t_work_order`：`WORK_ORDER_INDUSTRY*`（边 `work_order_industry`）、`CUSTOMER_INDUSTRY`（边 `work_order_customer`、`customer_industry`）；`t_customer`：`CUSTOMER_INDUSTRY*`（边 `customer_industry`） |
| `customer` | `t_customer.customer_name`（键 `id`，`entity_lookup: exact`） | `t_work_order`：`WORK_ORDER_CUSTOMER*`（边 `work_order_customer`） |
| `customer_level` | `t_customer.customer_level`（枚举） | `t_work_order`：`WORK_ORDER_CUSTOMER*`（边 `work_order_customer`） |
| `department` | `t_department.department_name`（键 `id`） | 见 4.1 示例：工单默认处理人部门；处理记录默认操作人部门 |
| `employee` | `t_employee.employee_name`（键 `id`，`entity_lookup: exact`） | `t_work_order`：`WORK_ORDER_CREATOR`、`WORK_ORDER_HANDLER`（**无默认**）；`t_work_process`：`PROCESS_OPERATOR*` |
| `work_order_status` | `t_work_order.status`（枚举） | 锚表本身 |
| `priority` | `t_work_order.priority`（枚举） | 锚表本身 |
| `channel` | `t_work_order.channel`（枚举） | 锚表本身 |
| `tag` | `t_tag.tag_name`（键 `id`，`allows_fanout: true`） | `t_work_order`：`WORK_ORDER_TAG*`（边 `work_order_tag_work_order`、`work_order_tag_tag`） |
| `process_action` | `t_work_process.action`（枚举） | 锚表本身 |

`employee` 从工单出发没有默认路径，是"应该澄清"类题目的来源之一（架构 22.1）。

### 4.6 S6 Release 组装

```python
def build_candidate_release(session: Session, *, datasource_id: str, semantic_dir: Path,
                            created_by: str, snapshot_id: int | None = None,
                            note: str = "") -> int: ...
```

步骤：取 `snapshot_id`（默认该数据源最新快照）→ `load_join_definitions` + `build_join_graph` → `save_join_graph_version` → `load_definitions` + `build_semantic_model` → 保存 `semantic_version` → `create_release(...)`（Spec 02）返回 release ID，状态 `CANDIDATE`。任何校验错误都不产生 release。`prompt_version`、`model_version` 由 Spec 08 扩展本函数后填写。

### 4.7 S7 ReleaseBundle（`text2sql.release.bundle`）

```python
@dataclass(frozen=True)
class ReleaseBundle:
    release_id: int
    datasource_id: str
    snapshot: SchemaSnapshot
    join_graph: JoinGraph
    semantic: SemanticModel
    db_dialect: Literal["mysql"]
    db_version: tuple[int, int, int]

def load_bundle(session: Session, release_id: int) -> ReleaseBundle: ...
def load_active_bundle(session: Session, datasource_id: str) -> ReleaseBundle: ...   # 无活跃 release 抛 AppError("NO_ACTIVE_RELEASE", …, 503)
```

- 按 `release_id` 进程内 LRU 缓存（容量 8）。release 不可变，所以缓存永不需要失效（架构 17.6：Schema Cache 按 `datasource + release` 失效，等价于按 release ID 缓存）。
- release 缺少 `join_graph_version_id` 或 `semantic_version_id`（第 1～2 周只含快照的 release）时抛 `AppError("RELEASE_INCOMPLETE", …, 409)`。
- `StaleSet` 不在 bundle 内（要实时生效），由调用方另行 `load_open_stale()`。

### 4.8 S8 CLI

`uv run python -m text2sql.semantic <子命令>`：`validate`（对当前快照与 Join Graph 校验，错误时退出码 1）、`show metric|dimension NAME`、`stats`（各状态对象数）、`check-pr --base REF`。

`uv run python -m text2sql.release <子命令>`：`build --by NAME [--snapshot ID] [--note]`、`activate ID --by NAME --reason TEXT`（调用 Spec 02 的 `activate_release`，prod 拒绝）、`show [ID|active]`。

## 5. 验收标准

1. `semantic validate` 对 `ticket_demo` 在 `local_mysql57` 与 `local_mysql80` 上都无 ERROR。
2. 4.2 表中每个 ERROR 校验码至少一个失败用例（单元测试，用内存快照与 Join Graph 构造）；特别地，把架构 7.1 原示例（分子时间字段 `complete_time`、比率 `create_time`）写入夹具时报 `RATIO_TIME_MISMATCH`。
3. `owner == reviewer` 的 VERIFIED 指标被拒绝；`check-pr` 在"修改 VERIFIED 指标的 filters 但未更新 `reviewed_at`"时失败。
4. `default_join_path("department", "t_work_order")` 返回 `HANDLER_DEPARTMENT`；`default_join_path("employee", "t_work_order")` 返回 `None`；`anchor_table("avg_process_duration") == "t_work_order"`。
5. `dependencies()` 中 `METRIC:avg_process_duration` 依赖 `COLUMN:t_work_process.duration`、`EDGE:process_work_order`、`COLUMN:t_work_order.create_time`；`DIMENSION:department` 依赖 `EDGE:work_order_handler` 等其全部路径边。
6. `release build` 产生 `CANDIDATE` release，三个组件 ID 齐全；内容不变时再次构建复用相同的 Join Graph 版本与语义版本 ID（但产生新的 release 记录）。
7. `load_bundle` 第二次调用命中缓存（不访问数据库，测试中计数）；只含快照的 release 抛 `RELEASE_INCOMPLETE`。
8. `ruff`、`mypy`、`pytest`、`pytest -m integration` 全部通过。

## 6. 新增依赖

无（PyYAML、pydantic 已有）。

## 7. 风险与说明

- **表达式能力有限**：指标只能是"列 + 聚合 + 过滤（+ 一层预聚合）"，表达不了"完成时间 − 创建时间"这类计算列（例如平均解决时长）。这是刻意的：任意 SQL 片段会绕过 Spec 07 的对象与字段校验。需要计算列的指标暂走兜底通道；若此类需求频繁，在阶段 2 增加受限的列表达式（只允许白名单函数）。
- **合成模型的 owner/reviewer 是演示角色**：`demo-definer`/`demo-reviewer` 只用于合成库，真实语义模型必须填 1.4 中的具体人员。
- **默认路径是业务决策**：`department` 默认按处理人、`industry` 默认按工单行业，都是合成模型的演示选择，并会在结果中标注（架构 6.6、9.6）。
- **开放决策（需用户确认）**：
  1. 真实业务的 10～20 个核心指标清单、定义人和审核人、审核时限（架构 7.4、1.4）。
  2. "已完成工单数"与"完成率"的时间口径（架构 7.1 修订指出的冲突）在真实业务中如何取舍。
  3. 敏感字段清单与脱敏方式（架构 1.3、11.5），以及 `SECRET` 级别字段的范围。
  4. 默认时间范围是否采用"最近 30 天"（架构 9.6 的例子），默认 LIMIT 是否为 100。
