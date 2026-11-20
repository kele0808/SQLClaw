# Spec 06：Query IR v2、规划器与版本感知 SQL 编译器

- 状态：待审阅
- 日期：2026-11-20
- 对应架构文档：第九章（9.1、9.1.1、9.2、9.3、9.5、9.6 的默认口径部分）、10.1（版本能力矩阵）、10.3（扇出 / 比率 / 边校验在编译期的落实）、6.5（预聚合）、14.3（结果血缘）、17.5（IR 增量修改）、17.6（编译缓存）、第二十三章（第 3 周、第 5 周）
- 依赖：Spec 02（`StaleSet`、对象键）、Spec 03（验收集 `expected_ir`、标注约定、`Predictor` 协议）、Spec 04（`JoinGraph`、`JoinStep`、`tree_from_edges`、`connect_tables`）、Spec 05（`ReleaseBundle`、`SemanticModel`）
- 被依赖：07（编译产物要通过网关校验；能力矩阵复用）、08（LLM 产出 IR，规划结果决定澄清/兜底/修复）、09（多轮 IR 增量修改）

## 1. 目标

定义 Query IR v2（覆盖架构 9.1.1 的比率、时间偏移、派生、窗口、Top-N、嵌套、`assumptions`），实现**确定性**的规划器（IR → 逻辑计划，含默认口径、Join 路径、扇出处理）和按 MySQL 5.7 / 8.0 版本输出的 SQL 编译器。本 Spec 不调用 LLM。

## 2. 范围

### 2.1 本期做

| 编号 | 能力 | 时间 |
| --- | --- | --- |
| Q1 | IR v2 的完整 pydantic 模型与 JSON Schema（全部字段一次定义） | 第 3 周 |
| Q2 | 规划：指标/维度解析、默认口径与 `assumptions`、Join 路径、扇出处理、过滤与枚举解析、软删除默认过滤 | 第 3 周 |
| Q3 | 编译：聚合、过滤、时间范围、时间分桶、比率、全局 Top-N、HAVING、排序、LIMIT | 第 3 周 |
| Q4 | 能力矩阵与输出自检（5.7 不出现 CTE / 窗口函数） | 第 3 周 |
| Q5 | 结果血缘与输出列描述 | 第 3 周 |
| Q6 | `GoldIrPredictor`（评测用）与编译缓存 | 第 3 周 |
| Q7 | 时间偏移（同比/环比）、派生指标、窗口、组内 Top-N、嵌套 | 第 5 周 |
| Q8 | IR 增量修改操作（多轮与澄清共用） | 第 5 周 |

### 2.2 本期不做

- 自然语言 → IR（Spec 08 的 LLM 节点）
- 兜底通道的 SQL 生成（Spec 08）；本 Spec 只负责判定 `Unsupported` 并给出原因
- SQL 安全校验与执行（Spec 07；编译产物同样要过网关全部校验）
- PostgreSQL 方言（架构 23.2 阶段 4）
- 时间偏移与时间分桶维度同时使用（返回 `Unsupported`，原因见 7）
- 递归、会话切分等 IR 之外的算子（走兜底）

## 3. 目录与文件

```
src/text2sql/planner/
├── query_ir.py             # Q1 IR 模型
├── ir_edit.py              # Q8 增量修改
├── context.py              # CompileContext、结果类型、错误码
├── metric_resolver.py      # Q2
├── dimension_resolver.py   # Q2 路径选择与提升
├── join_planner.py         # Q2 每个块的 Join 集合
├── aggregation_planner.py  # Q2 分块、预聚合、扇出处理
├── filter_planner.py       # Q2 过滤与枚举
├── time_planner.py         # Q2 时间窗口
├── plan_validator.py       # Q2 汇总校验，产出 Planned / Unsupported / PlanFailed
├── logical_plan.py         # 逻辑计划数据结构
└── planner.py              # plan_query() 入口
src/text2sql/sql/
├── dialect/capabilities.py # Q4
└── generator/
    ├── compiler.py         # compile_plan() / compile_ir()
    ├── blocks.py           # 指标块 SQL
    ├── composition.py      # spine、比率、派生、偏移
    ├── ranking.py          # 窗口与 Top-N（8.0 窗口函数 / 5.7 改写）
    └── lineage.py          # Q5
src/text2sql/evaluation/predictors/gold_ir.py   # Q6
tests/fixtures/ir/*.json                        # IR 夹具
tests/unit/sql/snapshots/<case>.<mysql57|mysql80>.sql
```

## 4. 详细设计

### 4.1 配置

`Settings` 新增：

| 字段 | 环境变量 | 类型 | 默认值 |
| --- | --- | --- | --- |
| `business_timezone` | `T2S_BUSINESS_TIMEZONE` | `str`（IANA 时区名） | `"Asia/Shanghai"` |

用于在线查询计算"今天"（`as_of = 当前时间在该时区的日期`）。评测使用题目的 `as_of`。业务库 `DATETIME` 被视为该时区的本地时间。

### 4.2 Q1 Query IR v2

`text2sql.planner.query_ir`（pydantic v2；所有模型 `extra="forbid"`，防止 LLM 塞入未定义字段）：

```python
TimeUnit = Literal["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"]
Order = Literal["ASC", "DESC"]
Scalar = str | int | float | bool | None

class TimeRange(BaseModel):
    type: Literal["RELATIVE", "CALENDAR_TO_DATE", "PREVIOUS_PERIOD", "ABSOLUTE", "ALL"]
    value: int | None = None          # RELATIVE：N（≥1）；PREVIOUS_PERIOD：往前第几个完整期间，默认 1
    unit: TimeUnit | None = None      # RELATIVE / CALENDAR_TO_DATE / PREVIOUS_PERIOD 必填
    start: date | None = None         # ABSOLUTE：包含
    end: date | None = None           # ABSOLUTE：不包含
    time_field: str | None = None     # "table.column"，覆盖指标默认时间字段

class RatioMetricRef(BaseModel):
    id: str                           # 必须是语义层中 type=ratio 的指标
    type: Literal["RATIO"]
    numerator: str
    denominator: str

MetricRef = str | RatioMetricRef

class DimensionRef(BaseModel):
    id: str                           # 语义层维度名，或保留名 "time"
    join_role: str | None = None      # 维度路径的 role（Spec 05）
    grain: TimeUnit | None = None     # 仅 id == "time" 时必填

class Filter(BaseModel):
    dimension: str | None = None      # 与 column 二选一
    column: str | None = None         # "table.column"
    join_role: str | None = None      # dimension 过滤时可指定路径
    op: Literal["=", "!=", ">", ">=", "<", "<=", "IN", "NOT_IN", "BETWEEN", "IS_NULL", "IS_NOT_NULL", "CONTAINS"]
    values: list[Scalar] = []
    match_on: Literal["key", "label"] = "key"   # 维度有 key_column 时，比较键还是标签列
    labels: list[str] = []            # 仅用于展示（例如实体解析后的名称）

class TimeOffset(BaseModel):
    name: str                         # ^[a-z][a-z0-9_]{0,15}$，如 yoy、mom
    unit: TimeUnit
    value: int                        # 负数，-1 表示上一期
    align: Literal["SAME_PERIOD"] = "SAME_PERIOD"

class Derived(BaseModel):
    name: str
    expr: str                         # 文法见 4.8

class OrderItem(BaseModel):
    by: str                           # 指标 / 派生 / 窗口名 / 偏移列 "metric@offset" / 维度 id
    order: Order = "DESC"

class WindowSpec(BaseModel):
    name: str
    func: Literal["RANK", "DENSE_RANK", "ROW_NUMBER", "CUMULATIVE_SUM"]
    by: str | None = None             # CUMULATIVE_SUM 的被累计值
    partition_by: list[str] = []      # 维度 id
    order_by: list[OrderItem]

class TopN(BaseModel):
    n: int                            # 1～100
    by: str
    order: Order = "DESC"
    scope: Literal["GLOBAL", "PER_GROUP"] = "GLOBAL"
    partition_by: list[str] = []      # PER_GROUP 必填

class Having(BaseModel):
    by: str
    op: Literal["=", "!=", ">", ">=", "<", "<="]
    value: float

class NestedSource(BaseModel):
    ir: "QueryIR"
    link_dimension: str               # 内层 IR 必须按此维度分组；外层只保留该维度取值在内层结果中的行

class Assumption(BaseModel):
    type: Literal["DEFAULT_TIME_FIELD", "DEFAULT_TIME_RANGE", "DEFAULT_JOIN_ROLE", "METRIC_STATUS",
                  "DEFINITION_STATUS", "DEFAULT_FILTER", "FANOUT_DISTINCT", "TOP_N_TIES",
                  "LIMIT_CLAMPED", "FALLBACK_SQL"]
    value: str                        # 面向用户的中文说明
    source: str                       # 如 "metric:work_order_completion_rate"、"dimension:industry"、"defaults"

class FallbackInfo(BaseModel):
    reason: Literal["UNSUPPORTED_OPERATOR", "METRIC_NOT_DEFINED", "NESTING_TOO_DEEP", "OFFSET_WITH_TIME_DIMENSION"]
    detail: str
    allowed_tables: list[str] = []
    allowed_join_edges: list[str] = []   # 边 ID（Spec 04）

class QueryIR(BaseModel):
    ir_version: Literal[2] = 2
    plan_mode: Literal["IR", "FALLBACK_SQL"] = "IR"
    intent: Literal["ANALYTICAL_QUERY"] = "ANALYTICAL_QUERY"
    metrics: list[MetricRef]
    dimensions: list[str | DimensionRef] = []
    time_range: TimeRange | None = None
    time_offsets: list[TimeOffset] = []
    derived: list[Derived] = []
    window: list[WindowSpec] = []
    top_n: TopN | None = None
    filters: list[Filter] = []
    having: list[Having] = []
    sort: list[OrderItem] = []
    limit: int | None = None
    source: NestedSource | None = None
    assumptions: list[Assumption] = []
    clarification: dict[str, Any] | None = None   # 由 Spec 08 填写；非空时不得进入规划
    fallback: FallbackInfo | None = None
```

- 字段名与架构 9.1.1 示例一致；该示例 JSON 能被本模型解析（验收标准 1）。`dimensions` 中的字符串在解析后统一转为 `DimensionRef(id=…)`。
- `assumptions` 以规划器输出为准：规划时清空输入中的 `assumptions` 再重新生成，LLM 不能自行"声明"口径。
- `QueryIR.model_json_schema()` 供 Spec 08 放进提示词，作为结构化输出约束。

### 4.3 Q8 IR 增量修改（`text2sql.planner.ir_edit`）

```python
class SetTimeRange(BaseModel):   op: Literal["set_time_range"];  time_range: TimeRange
class SetTimeField(BaseModel):   op: Literal["set_time_field"];  time_field: str
class AddFilter(BaseModel):      op: Literal["add_filter"];      filter: Filter
class RemoveFilter(BaseModel):   op: Literal["remove_filter"];   target: str      # 维度 id 或 "table.column"
class AddDimension(BaseModel):   op: Literal["add_dimension"];   dimension: DimensionRef
class RemoveDimension(BaseModel):op: Literal["remove_dimension"]; id: str
class SetJoinRole(BaseModel):    op: Literal["set_join_role"];   dimension: str; join_role: str
class SetMetrics(BaseModel):     op: Literal["set_metrics"];     metrics: list[MetricRef]
class SetSort(BaseModel):        op: Literal["set_sort"];        sort: list[OrderItem]
class SetLimit(BaseModel):       op: Literal["set_limit"];       limit: int
class SetTopN(BaseModel):        op: Literal["set_top_n"];       top_n: TopN | None

IREditOp = Annotated[SetTimeRange | SetTimeField | AddFilter | RemoveFilter | AddDimension | RemoveDimension
                     | SetJoinRole | SetMetrics | SetSort | SetLimit | SetTopN, Field(discriminator="op")]

def apply_edits(ir: QueryIR, ops: Sequence[IREditOp]) -> QueryIR: ...
```

- 纯函数，返回新 IR；`assumptions` 清空（下次规划重新生成），`clarification` 置空。
- `SetTimeField` 写入 `time_range.time_field`（`time_range` 为空时先用默认时间范围补齐）。`SetJoinRole` 同时作用于该维度的 `dimensions` 项与 `filters` 项。
- 目标不存在（如移除不存在的维度）抛 `IREditError(code="EDIT_TARGET_NOT_FOUND")`。

### 4.4 编译上下文、结果类型与错误码（`text2sql.planner.context`）

```python
@dataclass(frozen=True)
class CompileContext:
    bundle: ReleaseBundle          # Spec 05
    stale: StaleSet                # Spec 02
    as_of: date
    max_limit: int                 # = Settings.max_rows（Spec 07）

PlanErrorCode = Literal[
    "CLARIFICATION_PENDING", "UNKNOWN_METRIC", "UNKNOWN_DIMENSION", "UNKNOWN_REFERENCE",
    "OBJECT_DEPRECATED", "OBJECT_STALE", "AMBIGUOUS_JOIN_ROLE", "UNKNOWN_JOIN_ROLE", "NO_JOIN_PATH",
    "FANOUT_UNSAFE", "UNKNOWN_ENUM_VALUE", "INVALID_FILTER", "TENANT_KEY_FORBIDDEN",
    "SENSITIVE_COLUMN_FORBIDDEN", "INVALID_TIME_RANGE", "INVALID_TIME_FIELD", "INVALID_DERIVED_EXPR",
    "INVALID_WINDOW", "INVALID_TOP_N", "INVALID_RATIO_REF", "INVALID_NESTED_SOURCE",
    "UNSUPPORTED_ON_VERSION",
]

@dataclass(frozen=True)
class PlanOption:
    value: str                     # 如 join_role "WORK_ORDER_CREATOR"、枚举 code
    label: str                     # 如 "按工单创建人"

@dataclass(frozen=True)
class PlanError:
    code: PlanErrorCode
    message: str                   # 不含业务数据
    path: str                      # IR 中的 JSON Pointer，如 "/dimensions/0"
    options: tuple[PlanOption, ...] = ()
    repairable: bool = True        # 是否值得交给 LLM 修 IR（见下表）

@dataclass(frozen=True)
class Planned:
    ir: QueryIR                    # 规范化后（默认值已补齐，assumptions 已生成）
    plan: LogicalPlan

@dataclass(frozen=True)
class Unsupported:
    reason: Literal["UNSUPPORTED_OPERATOR", "METRIC_NOT_DEFINED", "NESTING_TOO_DEEP", "OFFSET_WITH_TIME_DIMENSION"]
    detail: str

@dataclass(frozen=True)
class PlanFailed:
    errors: tuple[PlanError, ...]

PlanOutcome = Planned | Unsupported | PlanFailed

@dataclass(frozen=True)
class OutputColumn:
    name: str
    kind: Literal["DIMENSION_KEY", "DIMENSION_LABEL", "METRIC", "OFFSET_METRIC", "DERIVED", "WINDOW"]
    ref: str                       # 维度名 / 指标名 / "metric@offset" / 派生名 / 窗口名

@dataclass(frozen=True)
class MetricLineage:               # 架构 14.3
    metric: str
    source_tables: tuple[str, ...]
    source_columns: tuple[str, ...]
    aggregation: str               # 如 "COUNT(DISTINCT t_work_order.id)"
    filters: tuple[str, ...]       # 规范化文本，含指标过滤、IR 过滤、默认过滤
    time_field: str | None
    time_window: tuple[datetime, datetime] | None
    metadata_release_id: int

@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    dialect: Literal["mysql"]
    target_version: tuple[int, int, int]
    tables: frozenset[str]
    edges_used: tuple[str, ...]
    output_columns: tuple[OutputColumn, ...]
    lineage: tuple[MetricLineage, ...]
    assumptions: tuple[Assumption, ...]

@dataclass(frozen=True)
class Compiled:
    ir: QueryIR
    query: CompiledQuery

CompileOutcome = Compiled | Unsupported | PlanFailed

def plan_query(ir: QueryIR, ctx: CompileContext) -> PlanOutcome: ...
def compile_plan(plan: LogicalPlan, ctx: CompileContext) -> CompiledQuery: ...
def compile_ir(ir: QueryIR, ctx: CompileContext) -> CompileOutcome: ...
```

`repairable = False` 的错误码：`CLARIFICATION_PENDING`、`AMBIGUOUS_JOIN_ROLE`（交给澄清）、`OBJECT_STALE`（元数据待更新）、`TENANT_KEY_FORBIDDEN`、`SENSITIVE_COLUMN_FORBIDDEN`（交给拒绝）、`UNSUPPORTED_ON_VERSION`。其余为 `True`。Spec 08 按此分流。

`compile_plan` 若发现自身输出违反能力矩阵（4.9）则抛 `CompilerInvariantError`（编译器缺陷，不是用户错误）。

### 4.5 Q2 规划步骤

`plan_query` 依次执行，收集所有错误后一次返回（不在第一个错误处停止，便于 LLM 一次修完）：

1. **前置**：`clarification` 非空 → `CLARIFICATION_PENDING`。`plan_mode == "FALLBACK_SQL"` 且 `fallback` 非空 → 直接返回 `Unsupported(fallback.reason, fallback.detail)`（LLM 判断 IR 表达不了，例如需要会话切分）。`source.ir.source` 非空 → `Unsupported("NESTING_TOO_DEEP")`。
2. **指标**：存在、非 `DEPRECATED`；依赖未被 STALE（用 `SemanticModel.dependencies()` 与 `StaleSet` 判断）；内联 `RatioMetricRef` 必须与语义层同名比率的分子分母一致（`INVALID_RATIO_REF`）。比率展开为分子、分母两个度量。非 VERIFIED → `METRIC_STATUS` 口径。
3. **时间**：
   - `time_range` 为空且至少一个指标有时间字段 → 用 `defaults.default_time_range`，记 `DEFAULT_TIME_RANGE`（如"时间范围默认为最近 30 天"）。
   - 每个指标的有效时间字段 = `time_range.time_field`（若给出；必须是该指标锚表或其 FORWARD 可达表的日期列，否则 `INVALID_TIME_FIELD`）或指标的 `time_dimension`；后者记 `DEFAULT_TIME_FIELD`（如"按工单创建时间统计"），同一字段只记一次。
   - 按 4.7 解析为半开区间 `[start, end)`。
4. **维度路径**：对每个"指标锚表 × 维度"：
   - 维度来源表就是锚表 → 无需路径。
   - 指定了 `join_role` → 用该路径；不存在 → `UNKNOWN_JOIN_ROLE`（`options` 列出可用角色）。
   - 未指定：锚表上只有一条路径 → 直接用；有默认路径 → 用默认，并在锚表有多条路径时记 `DEFAULT_JOIN_ROLE`（文案取路径的 `label`，如"按工单所属行业统计"）；多条且无默认 → `AMBIGUOUS_JOIN_ROLE`，`options` 为各路径的 role 与 label。
   - **路径提升**：锚表上没有该维度的路径时，用 `connect_tables` 在只含 FORWARD 步的子图上找从锚表到"该维度有路径定义的 from_table"的最短链；恰好一个 → 拼接（如 `t_work_process --process_work_order--> t_work_order` 再接工单上的行业路径）；多个 → `AMBIGUOUS_JOIN_ROLE`；没有 → `NO_JOIN_PATH`。
   - 路径中任何边或字段被 STALE → `OBJECT_STALE`。
5. **扇出处理**（架构 6.5、10.3 的编译期落实）：
   - 维度路径全部为 FORWARD 步 → 无扇出。
   - 含扇出步（只可能来自 `allows_fanout` 维度，如标签）→ 只允许 `count_distinct` 且列为锚表主键的指标，记 `FANOUT_DISTINCT`（如"一张工单可有多个标签，各标签工单数之和可能大于工单总数"）；其他指标 → `FANOUT_UNSAFE`。
   - 有 `per` 的指标，预聚合子查询按 `via_edge` 的 from 列分组，与锚表一对一，不扇出。
   - 不同锚表的指标放在不同的块里分别聚合，再按维度键合并（4.6），所以多个 1:N 分支（chasm）在 IR 路径上不会出现。
6. **过滤**：
   - `dimension` 过滤：按第 4 步同样规则选路径；枚举维度且 `values` 为字符串时，按标签和同义词映射为 code，映射失败 → `UNKNOWN_ENUM_VALUE`（`options` 列出合法标签，用于架构 13.2 修订"先检查过滤值是否命中枚举"）。
   - `column` 过滤：列必须在锚表或其 FORWARD 可达表上；不得为废弃字段（`INVALID_FILTER`）、敏感字段（`SENSITIVE_COLUMN_FORBIDDEN`）、租户键（`TENANT_KEY_FORBIDDEN`）。
   - 维度或过滤引用租户键 → `TENANT_KEY_FORBIDDEN`（例如"查租户 1002 的工单"）。
   - `CONTAINS` 编译为 `LIKE '%…%'`，值中的 `%`、`_`、`\` 转义。
7. **默认过滤**：块中涉及的每张表施加 `defaults.default_filters`，记一次 `DEFAULT_FILTER`（"已排除软删除数据"）。
8. **排序与限制**：`sort`/`having`/`top_n`/`window` 引用的名字必须是输出列（`UNKNOWN_REFERENCE`）。`limit` 为空用 `defaults.default_limit`；超过 `ctx.max_limit` 时截为 `max_limit` 并记 `LIMIT_CLAMPED`。`top_n` 存在时记 `TOP_N_TIES`（"并列名次全部保留"）。
9. **版本**：目标为 5.7 且使用 `ROW_NUMBER` → `UNSUPPORTED_ON_VERSION`（5.7 上没有确定性的等价改写，见 4.8）。时间偏移与 `time` 维度同时出现 → `Unsupported("OFFSET_WITH_TIME_DIMENSION")`。

### 4.6 逻辑计划与 SQL 形态

```python
@dataclass(frozen=True)
class DimensionBinding:
    dimension: str
    role: str | None
    steps: tuple[JoinStep, ...]        # 从块的锚表出发
    key_column: str | None             # "table.column"
    label_column: str                  # "table.column"；time 维度为时间字段
    grain: TimeUnit | None

@dataclass(frozen=True)
class Measure:
    output_name: str                   # 指标名
    aggregation: str                   # count / count_distinct / sum / avg / min / max
    column: str                        # "table.column"；有 per 时为预聚合输出
    conditions: tuple[ResolvedFilter, ...]   # 指标自身过滤，编译为 CASE WHEN

@dataclass(frozen=True)
class MetricBlock:
    block_id: str                      # b1, b2, …
    anchor: str
    pre_aggregate: PreAggregate | None # 来自指标 per
    inner_joins: tuple[JoinStep, ...]  # 过滤与时间字段需要的表
    left_joins: tuple[JoinStep, ...]   # 只为维度需要的表
    dimensions: tuple[DimensionBinding, ...]
    measures: tuple[Measure, ...]
    time_column: str | None
    window: tuple[datetime, datetime] | None
    offset_name: str | None            # None 表示当前期
    filters: tuple[ResolvedFilter, ...]

@dataclass(frozen=True)
class LogicalPlan:
    blocks: tuple[MetricBlock, ...]
    ratios: tuple[tuple[str, str, str], ...]     # (输出名, 分子, 分母)
    derived: tuple[Derived, ...]
    windows: tuple[WindowSpec, ...]
    top_n: TopN | None
    having: tuple[Having, ...]
    sort: tuple[OrderItem, ...]
    limit: int
    nested: "LogicalPlan | None"
    link_dimension: str | None
    output_columns: tuple[OutputColumn, ...]
    assumptions: tuple[Assumption, ...]
```

**分块规则**：度量按 `(anchor, pre_aggregate, time_column, window, offset_name)` 分组为块；同一块内不同指标的过滤差异用条件聚合表达（`COUNT(DISTINCT CASE WHEN w.status = 3 THEN w.id END)`），避免为每个指标单独扫描。

**块 SQL**：

```sql
SELECT <维度键> AS industry_key, <维度标签> AS industry, <度量…>
FROM t_work_order AS t0
LEFT JOIN t_industry AS t1 ON t0.industry_id = t1.id            -- 维度表：LEFT JOIN，其默认过滤写在 ON
LEFT JOIN (SELECT work_id AS k, SUM(duration) AS v
           FROM t_work_process GROUP BY work_id) AS p0 ON p0.k = t0.id   -- 预聚合（per）
WHERE t0.is_deleted = 0                                         -- 锚表默认过滤
  AND t0.create_time >= '2026-06-01 00:00:00' AND t0.create_time < '2026-07-01 00:00:00'
  AND <IR 过滤>
GROUP BY <维度键>, <维度标签>
```

- 过滤或时间字段所在的非锚表用 INNER JOIN，只服务于维度的表用 LEFT JOIN（关联不到的行归入 NULL 组，与 Spec 03 标注约定一致）。
- 表别名按出现顺序 `t0, t1, …`，预聚合 `p0, …`，块 `b1, …`，确保快照测试稳定。

**合并**：只有一个块时，外层直接 `SELECT … FROM (<b1>) AS b1`。多个块时：

```sql
SELECT s.industry_key, s.industry, b1.work_order_count, b2.customer_count
FROM (SELECT industry_key, industry FROM (<b1>) AS u1
      UNION SELECT industry_key, industry FROM (<b2>) AS u2) AS s
LEFT JOIN (<b1>) AS b1 ON s.industry_key <=> b1.industry_key
LEFT JOIN (<b2>) AS b2 ON s.industry_key <=> b2.industry_key
```

两个版本统一使用派生表，不使用 CTE：一条代码路径、两个版本语义相同；代价是块 SQL 在合并时重复出现（MySQL 会分别物化），对 MVP 规模可接受。块之间按维度键（`<=>` 空值安全）连接，Spec 07 将这种"两侧都已按连接键分组"的连接识别为粒度对齐连接。

**比率**：`b.num / NULLIF(b.den, 0) AS work_order_completion_rate`；分子分母在同一块（同锚表同时间字段，Spec 05 已保证）。输出列中保留分子、分母列（`kind = METRIC`），便于结果校验和血缘。

**排序**：`ORDER BY` 先按 `sort`，再按所有维度键升序作为确定性的并列裁决；`NULL` 值用 `x IS NULL, x` 的形式统一排在最后（两版本一致）。

### 4.7 时间语义（`time_planner`）

以 `as_of`（日期）为"今天"，区间均为 `[start 00:00:00, end 00:00:00)`：

| 类型 | 区间 |
| --- | --- |
| `RELATIVE N DAY` | `[as_of − (N−1) 天, as_of + 1 天)` |
| `RELATIVE N WEEK/MONTH/QUARTER/YEAR` | `[as_of + 1 天 − N 个单位, as_of + 1 天)`（月份运算按日历，月末截断） |
| `CALENDAR_TO_DATE unit` | `[as_of 所在期的第一天, as_of + 1 天)`；周以周一为第一天 |
| `PREVIOUS_PERIOD unit, value=k` | 往前第 k 个完整自然期间 |
| `ABSOLUTE` | `[start, end)`；`start >= end` → `INVALID_TIME_RANGE` |
| `ALL` | 不加时间条件 |

时间偏移 `TimeOffset(unit, value)`：把当前区间的 `start` 和 `end` 同时平移 `value` 个 `unit`（日历运算，月末截断）。例如今年至今 `[2026-01-01, 2026-07-01)` 的 `yoy` 为 `[2025-01-01, 2025-07-01)`，即"同期"。

时间分桶（`time` 维度，两版本相同，输出字符串）：

| grain | 表达式 | 示例 |
| --- | --- | --- |
| DAY | `DATE_FORMAT(c, '%Y-%m-%d')` | `2026-06-30` |
| WEEK | `CONCAT(LEFT(YEARWEEK(c, 3), 4), '-W', RIGHT(YEARWEEK(c, 3), 2))` | `2026-W26` |
| MONTH | `DATE_FORMAT(c, '%Y-%m')` | `2026-06` |
| QUARTER | `CONCAT(YEAR(c), '-Q', QUARTER(c))` | `2026-Q2` |
| YEAR | `DATE_FORMAT(c, '%Y')` | `2026` |

### 4.8 Q7 偏移、派生、窗口、Top-N、嵌套

**时间偏移**：每个偏移为参与的指标生成一组偏移块（`offset_name` 非空，窗口为平移后的区间），与当前期块一起参与合并。输出列名 `<metric>__<offset>`（如 `work_order_count__yoy`），在 IR 引用中写作 `work_order_count@yoy`。

**派生**：表达式文法

```
expr   := term (("+" | "-") term)*
term   := factor (("*" | "/") factor)*
factor := NUMBER | REF | "(" expr ")" | "-" factor
REF    := METRIC_NAME ("@" OFFSET_NAME)?
```

`REF` 只能引用本 IR 的指标（含比率）或其偏移结果；除法编译为 `a / NULLIF(b, 0)`；不允许函数调用。解析失败或引用不存在 → `INVALID_DERIVED_EXPR`。

**窗口与 Top-N**：在合并结果外再包一层计算排名，再包一层过滤。

| 能力 | MySQL 8.0 | MySQL 5.7 改写 |
| --- | --- | --- |
| `RANK` | `RANK() OVER (PARTITION BY … ORDER BY …)` | `1 + (SELECT COUNT(*) FROM (<合并SQL>) r2 WHERE r2.p <=> r.p AND r2.v > r.v)` |
| `DENSE_RANK` | `DENSE_RANK() OVER (…)` | `1 + (SELECT COUNT(DISTINCT r2.v) FROM … WHERE … AND r2.v > r.v)` |
| `CUMULATIVE_SUM` | `SUM(v) OVER (PARTITION BY … ORDER BY o ROWS UNBOUNDED PRECEDING)` | `(SELECT SUM(r2.v) FROM … WHERE r2.p <=> r.p AND r2.o <= r.o)`；要求 `order_by` 的键在分区内唯一（维度键满足），否则 `INVALID_WINDOW` |
| `ROW_NUMBER` | `ROW_NUMBER() OVER (…)` | 不支持（`UNSUPPORTED_ON_VERSION`） |

（ASC 排序时比较方向相反。）

- `top_n` 一律按 RANK 语义（并列全部保留），`GLOBAL` 不分区、`PER_GROUP` 按 `partition_by` 分区；排序值为 NULL 的行不参与排名并被排除。这样 8.0 与 5.7 结果一致，与 Spec 03 标注约定一致。
- 5.7 的改写会让合并 SQL 在关联子查询中再次出现；MySQL 对不相关的派生表只物化一次，聚合后的行数通常很小（按维度分组），可接受。

**嵌套**（架构 9.1.1 `source.ir`）：先编译内层 IR（必须按 `link_dimension` 分组，否则 `INVALID_NESTED_SOURCE`），外层每个块在 WHERE 中加：

```sql
<外层 link_dimension 键> IN (SELECT k FROM (SELECT industry_key AS k FROM (<内层SQL>) AS n0) AS n1)
```

多包一层派生表是为了绕开 MySQL"`IN` 子查询中不支持 `LIMIT`"的限制（5.7 与 8.0 均有此限制，内层可能带 Top-N 与 LIMIT）。例："创建工单最多的员工及其部门和客户"＝内层按 `employee`（`join_role: WORK_ORDER_CREATOR`）取 Top-1 工单数，外层按 `employee`（`WORK_ORDER_CREATOR`）、`department`（`CREATOR_DEPARTMENT`）、`customer` 统计。

### 4.9 Q4 能力矩阵与自检（`text2sql.sql.dialect.capabilities`）

```python
@dataclass(frozen=True)
class Capabilities:
    cte: bool
    window_functions: bool
    max_execution_time: bool     # 会话变量 max_execution_time（5.7.8+）
    explain_analyze: bool        # 存在与否；网关一律禁止

def capabilities_for(version: tuple[int, int, int]) -> Capabilities: ...
def capability_violations(tree: exp.Expression, caps: Capabilities) -> list[str]: ...   # "CTE" / "WINDOW_FUNCTION"
```

5.7.x：`cte=False, window_functions=False, max_execution_time=True, explain_analyze=False`；8.0.x：`cte=True, window_functions=True, max_execution_time=True, explain_analyze=(version >= (8,0,18))`。`compile_plan` 输出后用 SQLGlot 解析自身结果并调用 `capability_violations`，有违规即 `CompilerInvariantError`。Spec 07 对所有 SQL（含兜底）复用同一函数。

### 4.10 Q5 血缘与口径

- 每个指标一条 `MetricLineage`：`source_tables`/`source_columns` 包含度量列、过滤列、时间字段及路径上的连接列；`aggregation` 为规范化的聚合文本（预聚合写成 `AVG(SUM(t_work_process.duration) PER t_work_order)`）；`filters` 包括默认过滤；`metadata_release_id` 取 `ctx.bundle.release_id`。
- `CompiledQuery.assumptions` 与 `Planned.ir.assumptions` 相同；面向用户的口径文案由 Spec 08 拼装。

### 4.11 Q6 `GoldIrPredictor` 与编译缓存

- `GoldIrPredictor`（`name = "gold_ir"`）：对有 `expected_ir` 的题，用评测上下文（`as_of`、release、`StaleSet` 为空）编译，返回 `Prediction(behavior="ANSWER", sql=…, ir=…, plan_mode="IR", assumptions=…)`；`PlanFailed` → `behavior="ERROR"`；`Unsupported` 或无 `expected_ir` → `SKIPPED`。用于第 3 周"IR → SQL 端到端可用，评测有第一版分数"，衡量编译器本身的正确率。
- 编译缓存：进程内 LRU（256 项），键为 `(release_id, IR 规范化 JSON 的 sha256, as_of, StaleSet 的哈希)`，值为 `CompileOutcome`（与用户无关、不含数据，符合架构 17.6 修订）。

### 4.12 测试

- IR 夹具 `tests/fixtures/ir/*.json` 覆盖每一类算子；每个夹具在 `mysql57`、`mysql80` 下各有一个期望 SQL 快照（`--update-snapshots` 重新生成，变更需在 PR 中审阅）。
- 集成测试：对合成库执行编译结果，与手写 SQL 结果用 Spec 03 的 `compare_results` 比对；同一 IR 在 5.7 与 8.0 上的结果必须一致。

## 5. 验收标准

第 3 周：

1. 架构 9.1 与 9.1.1 的两个 IR 示例 JSON 能被 `QueryIR` 解析；包含未定义字段的 IR 被拒绝。
2. 规划单元测试：4.5 中每个 `PlanErrorCode`（除第 5 周算子相关的）至少一个用例；`department` 从工单出发未指定角色时使用默认 `HANDLER_DEPARTMENT` 并产生 `DEFAULT_JOIN_ROLE`；`employee` 从工单出发未指定角色时返回 `AMBIGUOUS_JOIN_ROLE` 且 `options` 含创建人与处理人；"标签维度 + `avg_process_duration`"返回 `FANOUT_UNSAFE`；"标签维度 + `work_order_count`"成功并产生 `FANOUT_DISTINCT`；过滤 `work_order_status = "已解决"` 映射为 code 3，`"已作废"` 返回 `UNKNOWN_ENUM_VALUE`；按 `tenant_id` 过滤返回 `TENANT_KEY_FORBIDDEN`。
3. 时间单元测试：4.7 表中每种类型至少 2 个用例（含月末、闰年、跨年）。
4. 快照测试：每个第 3 周算子的夹具在两个版本下的 SQL 与快照一致；5.7 的输出不含 `WITH` 与窗口函数（`capability_violations` 为空）。
5. 集成测试：`avg_process_duration` 按部门（架构 6.1 的扇出场景）编译执行的结果，与 Spec 03 4.4 示例的手写真值一致，且工单数等于不关联处理记录时的工单数（无计数放大）。
6. `run --predictor gold_ir` 在两个版本上对所有带 `expected_ir` 的第 3 周类别题（`single_table`、`many_to_one_join`、`fanout_risk`、`ratio`、`join_role`、全局 Top-N）`execution_accuracy = 1.0`；不一致的题必须修正编译器或标注后才能关闭第 3 周。
7. 编译产物都能通过 Spec 07 的校验管线（第 4 周补测：所有快照 SQL 作为 Spec 07 的正向用例）。

第 5 周：

8. 架构 9.1.1 示例（今年与去年同期完成率对比、提升最多的 3 个行业）编译执行结果与手写真值一致（两个版本）。
9. `RANK`/`DENSE_RANK`/`CUMULATIVE_SUM` 与组内 Top-N 在 5.7 改写与 8.0 窗口函数下结果一致（集成测试，含并列与 NULL）；5.7 上 `ROW_NUMBER` 返回 `UNSUPPORTED_ON_VERSION`。
10. 嵌套示例（创建工单最多的员工及其部门和客户）编译执行与真值一致；二级嵌套返回 `Unsupported("NESTING_TOO_DEEP")`。
11. `apply_edits` 对每种操作有单元测试；对 17.5 的三轮示例（加"只看已完成"、加排序）逐轮编译结果正确。
12. `gold_ir` 对全部 `ANSWER` 题（多轮题按最后一轮的 `expected_ir`）`execution_accuracy = 1.0`。

通用：

13. `ruff`、`mypy`、`pytest`、`pytest -m integration` 全部通过。

## 6. 新增依赖

无（SQLGlot、pydantic 已有）。

## 7. 风险与说明

- **统一用派生表而不用 CTE**：8.0 也不用 CTE，牺牲一些可读性和可能的重复计算，换取两个版本同一套代码与相同语义。若 8.0 上出现性能问题，可只在 8.0 路径改为 CTE，快照测试会覆盖差异。
- **偏移与时间分桶不能同时使用**（如"按月对比今年与去年"）：当前期与偏移期的时间键不同，需要键对齐逻辑。MVP 返回 `Unsupported` 进兜底，并由 `fallback_ratio` 统计频率，作为阶段 2 扩展 IR 的依据（架构 9.4）。
- **5.7 的窗口改写成本**：关联子查询在聚合结果上计算，行数大时（例如按员工 × 天分组）会变慢；EXPLAIN 成本检查（Spec 07）兜底。
- **路径提升可能引入未经标注的口径**：只在 FORWARD 链唯一时自动提升；FORWARD 是多对一，不改变计数，但"处理记录按工单行业统计"的口径不会单独标注。若评测发现用户理解偏差，再增加一种 assumption。
- **`business_timezone` 默认 `Asia/Shanghai`** 是基于用户所在时区的假设；真实业务库 `DATETIME` 的时区语义属于 1.3 待填项（开放决策）。
- **开放决策（需用户确认）**：
  1. "最近 N 天"包含今天（本 Spec 的选择）还是不含今天。
  2. Top-N 并列全部保留（RANK 语义）是否符合业务预期。
  3. 维度关联不到时归入 NULL 组（LEFT JOIN）而不是丢弃。
  4. 业务库时间字段的时区（1.3"字符集与时区设置"）。
