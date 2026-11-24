# Spec 09：Delivery 多轮对话、Schema Diff 与发布、公开 API、查询页面

- 状态：已确认（2026-11-20）
- 日期：2026-11-20
- 修订记录：
  - 2026-11-22：采纳大 Schema 调研（`docs/research/01-text2sql-sota.md` 建议 2、3）。4.3 影响分析额外列出受影响的问答样例（Spec 05 S9，不写 STALE 标记）；`metadata.sync_and_diff` 在结构变化后入队 `metadata.value_index`（Spec 02 M10）。均属 **MVP**。
  - 2026-11-24：采纳 Architecture Baseline 审阅结论（`docs/research/03-architecture-baseline-review.md`、`docs/specs/00-contracts.md`）。D1 写明每轮重新授权、会话不携带授权（4.2）；D4 写明 `sql` 是实际执行语句、`certified = false` 的兜底结果在页面显示"非认证口径"，API 保持 `/api/v1`、弃用规则推迟（4.5、4.6）。属于 **MVP**，不新增接口。
- 对应架构文档：17.5（多轮对话）、9.6（口径标注上的修改）、5.1/5.2/5.5/16.1/16.2 的 MVP 修订（手动 Diff、STALE、发布与回滚、门禁）、第二十章（API）、第二十一章（MVP 部署）、22.2（MVP 验收）、第二十三章（第 6 周）
- 依赖：Spec 01（API 骨架、队列、Compose）、Spec 02（快照、身份、release 与指针、`move_pointer`、STALE）、Spec 03（评测运行与判定）、Spec 04/05（依赖关系、`build_candidate_release`）、Spec 06（`IREditOp`、`apply_edits`）、Spec 07（鉴权、审计）、Spec 08（`run_query`、`QueryResponse`、`Clarification`、`query_execution`、LLM 接口）
- 被依赖：无（MVP 最后一个子项目）

## 1. 目标

完成 MVP 的对外交付：基于 IR 增量修改的基础多轮对话与澄清回答；手动触发的 Schema Diff、影响分析与 STALE 标记；带评测门禁的 release 发布与回滚；第二十章的公开 API；一个最小查询页面。第 6 周末按 22.2 做 MVP 验收。

## 2. 范围

### 2.1 本期做

| 编号 | 能力 | 说明 |
| --- | --- | --- |
| D1 | 会话与续问 | 追问编辑 IR、澄清回答、口径调整、跨轮 release 兼容检查 |
| D2 | Schema Diff 与影响分析 | 快照对比、人工重命名映射、依赖对象 STALE |
| D3 | 发布与回滚 | 评测门禁、未解释退化拦截、回滚前兼容检查、STALE 自动解决 |
| D4 | 公开 API | `/api/v1/...`，鉴权与角色 |
| D5 | 最小查询页面 | 静态页面，由 API 进程提供 |
| D6 | 应用容器化 | Dockerfile + Compose `app` profile（api、worker） |
| D7 | MVP 验收 | 两个版本各跑一次 Agent 评测并归档 |

### 2.2 本期不做

- Migration Hook、定时 Snapshot、Reconciliation、自动增量构建（阶段 2）
- 重命名的自动识别（只接受人工映射，架构 4.3）
- 查询取消 `POST /api/v1/query/{id}/cancel`（MVP 查询同步执行，受 `agent_deadline_s` 约束；阶段 2 引入异步查询时再做）
- 注册数据源 `POST /api/v1/datasources`（MVP 一个进程一个数据源，由配置决定；多数据源在阶段 4）
- 历史结果重取（结果不落库，见 Spec 08）
- 前端工程化（React 等）、自动图表（后续）
- 监控看板（阶段 3，MVP 用 `/api/v1/metrics` 返回 JSON）

## 3. 目录与文件

```
src/text2sql/
├── conversation/
│   ├── models.py           # D1 conversation 表
│   ├── service.py          # D1 continue_query()
│   ├── edit_understanding.py  # D1 LLM edit_ir 任务
│   └── adjustments.py      # D1 口径调整选项
├── metadata/
│   ├── evolution/
│   │   ├── diff.py         # D2
│   │   ├── impact.py       # D2
│   │   └── jobs.py         # D2 队列 handler：metadata.sync_and_diff
│   └── publisher/
│       ├── publish.py      # D3
│       └── rollback.py     # D3
├── api/routes/
│   ├── query.py            # D4
│   ├── feedback.py
│   ├── datasources.py
│   ├── metadata.py
│   ├── jobs.py
│   ├── metrics.py
│   ├── evaluation.py
│   └── ui.py               # D5 静态文件挂载
prompts/edit_ir.yaml
apps/frontend/
├── index.html
├── app.js
└── style.css
deployment/docker/Dockerfile          # D6
deployment/compose.yaml               # 增加 api、worker（profile: app）
migrations/versions/0009_delivery.py  # conversation、query_feedback、metadata_change_event、metadata_rename_mapping
tests/unit/delivery/…
tests/integration/delivery/…
```

## 4. 详细设计

### 4.1 配置

| 字段 | 环境变量 | 类型 | 默认值 |
| --- | --- | --- | --- |
| `ui_enabled` | `T2S_UI_ENABLED` | `bool` | `True` |

### 4.2 D1 会话与续问

**`conversation`**：`conversation_id VARCHAR(40)` 主键（`c_` + uuid4 hex）；`principal_id`；`tenant_id`；`datasource_id`；`created_at`；`last_query_id`。迁移给 `query_execution.conversation_id` 加外键（Spec 08 预留）。`POST /api/v1/query` 未带 `conversation_id` 时新建会话。

续问请求：

```python
class ContinueRequest(BaseModel):
    message: str | None = None             # 自然语言追问或对澄清的文字回答
    option_id: str | None = None           # 选择澄清选项
    accept_default: bool = False           # 澄清时回答"都行"
    edits: list[IREditOp] | None = None    # 页面上的口径调整（只允许 set_time_field / set_join_role / set_time_range）
    options: QueryOptions = QueryOptions()
    # 四者恰好提供一个
```

```python
def continue_query(parent_query_id: str, request: ContinueRequest, principal: Principal) -> QueryResponse: ...
```

处理规则：

| 父查询状态 | 输入 | 处理 |
| --- | --- | --- |
| `needs_clarification` | `option_id` | 对 `clarification.pending_ir` 施加该选项的 `edits` → `run_query(initial_ir=…)` |
| `needs_clarification` | `accept_default` | 有 `default_option_id` 时同上；无默认（实体指代）→ `failed`，消息"请从候选中选择一个" |
| `needs_clarification` | `message` | LLM `edit_ir` 任务，上下文额外包含澄清问题与选项文本；输出须为某个选项或编辑 |
| `completed` | `message` | LLM `edit_ir` 任务（见下）|
| `completed` | `edits` | 直接 `apply_edits` 到父查询的 IR，不调用 LLM |
| `rejected` / `failed` | 任意 | 视为新问题：`message` 作为新的 `run_query`（同一会话） |

`edit_ir` 任务（本 Spec 把 Spec 08 的 `LLMTask` 扩展为包含 `"edit_ir"`）：

```python
class EditDecision(BaseModel):
    kind: Literal["EDIT", "NEW_QUESTION", "UNRESOLVABLE"]
    ops: list[IREditOp] = []
    reason: str = ""
```

- 输入：父查询规划后的 IR（含口径）、追问原文、语义目录（同 Spec 08 理解节点）。**不包含**父查询的结果数据（架构 11.6"结果不回传"）。
- `EDIT` → `apply_edits` → `run_query(initial_ir=…, parent_query_id=…)`；`assumptions` 由规划器重新生成，用户显式修改过的口径不再出现默认标注，未修改的默认口径继续标注（架构 17.5 修订"默认口径随 IR 继承"）。
- `NEW_QUESTION` → 以追问原文正常 `run_query`（同一会话）。
- `UNRESOLVABLE` → `failed`，消息"请说明具体名称或条件"。用于"上一个结果里第一个部门"这类指向结果数据的指代：MVP 不把结果发给模型，因此无法解析指向结果行的代词，只支持指向上一轮 IR 中已有过滤值、维度、指标的指代（这是对 17.5 修订"指代消解指向上一轮结果中的实体"的收窄，原因见第 7 节）。
- 跨轮 release 变化：续问总是使用当前活跃 release。若父查询的 IR 在新 release 上规划失败且错误为 `UNKNOWN_*`/`OBJECT_STALE`，返回 `failed`，消息"元数据已更新，上一轮的口径不再适用，请重新提问"（架构 17.5"旧 Query IR 也需要检查兼容性"）。
- **每轮重新授权**（2026-11-24，Spec 00 I3，Spec 08 4.12 的续问规则）：`continue_query` 只从父查询取已确认的 IR、澄清选项和 `release_id` 作为输入，不取任何授权结论；新一轮按**本次请求**的 `principal`（重新经 `get_principal` 鉴权，角色或租户变化立即生效）走完整的 `run_query` → 规划 → `Gateway.execute`。父查询的 SQL、编译结果和执行结果都不复用。父查询为兜底结果时，续问结果不会因此变为已认证。
- `turn_index = 父查询 turn_index + 1`；会话最多 20 轮，超过返回 `CONVERSATION_TOO_LONG`。
- 评测多轮题：`AgentPredictor`（Spec 08）对 Spec 03 题目的 `turns` 依次调用 `continue_query(message=…)`，以最后一轮作为 `Prediction`；中间某轮不是 `completed` 时以该轮状态作为结果。实现后多轮题不再返回 `SKIPPED`。

口径调整选项（`adjustments.suggest(planned_ir, bundle) -> list[Adjustment]`，随 API 响应返回，页面渲染为按钮）：

```python
class Adjustment(BaseModel):
    label: str                 # 如 "改为按完成时间统计"
    edits: list[IREditOp]
```

| 口径类型 | 生成的调整 |
| --- | --- |
| `DEFAULT_TIME_FIELD` | 锚表上其他非废弃的日期/时间字段（按字段注释或名称生成文案），`set_time_field` |
| `DEFAULT_JOIN_ROLE` | 该维度在锚表上的其他路径，`set_join_role` |
| `DEFAULT_TIME_RANGE` | 最近 7 天 / 最近 90 天 / 今年至今，`set_time_range` |

### 4.3 D2 Schema Diff 与影响分析

```python
ChangeKind = Literal[
    "TABLE_ADDED", "TABLE_REMOVED", "TABLE_RENAMED",
    "COLUMN_ADDED", "COLUMN_REMOVED", "COLUMN_RENAMED", "COLUMN_TYPE_CHANGED",
    "COLUMN_NULLABILITY_CHANGED", "COLUMN_DEFAULT_CHANGED", "COMMENT_CHANGED",
    "PRIMARY_KEY_CHANGED", "UNIQUE_INDEX_ADDED", "UNIQUE_INDEX_REMOVED",
    "INDEX_ADDED", "INDEX_REMOVED", "FOREIGN_KEY_ADDED", "FOREIGN_KEY_REMOVED",
    "VIEW_DEFINITION_CHANGED",
]

@dataclass(frozen=True)
class SchemaChange:
    kind: ChangeKind
    object_type: Literal["TABLE", "COLUMN"]
    object_key: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None

@dataclass(frozen=True)
class ImpactItem:
    object_type: ObjectType             # Spec 02
    object_key: str
    reason_code: str                    # COLUMN_REMOVED / TYPE_CHANGED / UNIQUENESS_LOST / RENAMED / DEPENDS_ON_STALE
    caused_by: str                      # 变更的 object_key

@dataclass(frozen=True)
class RenameMapping:              # metadata_rename_mapping 的一行
    object_type: Literal["TABLE", "COLUMN"]
    old_key: str
    new_key: str

def diff_snapshots(old: SchemaSnapshot, new: SchemaSnapshot,
                   renames: Sequence[RenameMapping] = ()) -> list[SchemaChange]: ...
@dataclass(frozen=True)
class ImpactReport:                     # 2026-11-22：原返回 list[ImpactItem]，增加样例清单
    items: tuple[ImpactItem, ...]
    affected_examples: tuple[tuple[str, str], ...]   # (example_id, 原因：依赖的 object_key)

def analyze_impact(changes: Sequence[SchemaChange], bundle: ReleaseBundle) -> ImpactReport: ...
```

- 对比基准：当前活跃 release 的快照（不是上一份快照），因为 STALE 的意义是"活跃 release 中哪些对象已不可信"。
- 重命名：不自动识别（架构 4.3）。管理员在 Diff 后用 `uv run python -m text2sql.metadata rename --type COLUMN --old t_work_order.handler_id --new t_work_order.assignee_id --by NAME` 写入 `metadata_rename_mapping`，然后重新执行 Diff，原来的"删除 + 新增"合并为 `*_RENAMED`。快照不可变，新字段的 `object_uuid` 不回写；映射只用于 Diff 展示、影响分析的提示文案（"字段已重命名为 …，请更新语义定义"）和审计。
- 字段类型变化只在类型族（Spec 04 `TYPE_INCOMPATIBLE` 校验使用的类型族：整数、定点/浮点、字符串、日期时间）改变时视为破坏性；同族变化（如 `int` → `bigint`）记录但不产生 STALE。
- 影响分析的依赖来源（MVP 即时计算，不持久化依赖图）：
  - 边：两端列；`to` 侧唯一性证据（唯一索引被删除且边没有 `HUMAN_CONFIRMED` → `UNIQUENESS_LOST`）。
  - 语义对象：`SemanticModel.dependencies()`（Spec 05）。
  - 字段注解与枚举：对应字段。
  - 传递：依赖于被标记边或指标的对象（如引用该边的维度、引用该指标的比率）→ `DEPENDS_ON_STALE`。
  - 问答样例（2026-11-22，Spec 05 S9）：依赖了被标记指标或维度的样例列入 `ImpactReport.affected_examples`（样例 ID 与原因），**不**写 `metadata_stale_mark`（样例不是 Spec 02 的 `ObjectType`），因为样例的可用性本来就在加载时按依赖实时计算（Spec 05 4.9）；列出来是为了提醒样例 owner 复核或修改。
  > 参考：架构 17.2"历史 SQL 依赖变化时标记失效、重新验证"；[WrenAI](https://github.com/Canner/WrenAI) 的 SQL pairs 随 MDL 部署重建索引。
- 每个影响项调用 `mark_stale(source="SCHEMA_DIFF", change_event_id=…)`（Spec 02）；被删除的表与字段本身也标记。新增类变化不产生 STALE。`COMMENT_CHANGED` 且新注释被注入检测命中时，不产生 STALE，但在变更列表中标出"注释待复核"。

**`metadata_change_event`**：`id`；`datasource_id`；`from_snapshot_id`；`to_snapshot_id`；`kind`；`object_type`；`object_key`；`before JSONB`；`after JSONB`；`breaking BOOLEAN`（是否产生了 STALE）；`created_at`；`trace_id`。迁移给 `metadata_stale_mark.change_event_id` 加外键（Spec 02 预留）。同一对 `(from_snapshot_id, to_snapshot_id)` 重新 Diff 时，先删除旧事件再写入（事件表是可重算的派生数据）。

**`metadata_rename_mapping`**：`id`；`datasource_id`；`object_type`；`old_key`；`new_key`；`created_by`；`created_at`。

队列任务 `metadata.sync_and_diff`（payload 同 Spec 02 的 `metadata.sync`）：采集保存快照 → 快照有变化（`unchanged = false`）且存在活跃 release 时执行 Diff、写事件、标记 STALE → 快照有变化时入队 `metadata.value_index`（Spec 02 4.11，`requested_by` 沿用本任务）→ 任务摘要（变更数、STALE 数、受影响样例数、新快照 ID）写入日志（带任务的 `trace_id`）；变更明细通过 `GET /api/v1/metadata/changes` 查询。Spec 01 的 `jobs` 表不存放任务结果，本 Spec 不为此修改队列。

### 4.4 D3 发布与回滚（架构 5.5、16.1 修订）

```python
def publish_release(session: Session, release_id: int, *, actor: str, reason: str,
                    regression_note: str | None = None) -> PublishReport: ...
def rollback_release(session: Session, *, datasource_id: str, to_release_id: int | None,
                     actor: str, reason: str, force: bool = False) -> RollbackReport: ...
```

`publish_release` 门禁（全部满足才移动指针；不满足抛 `AppError("RELEASE_GATE_FAILED", 详情, 409)`）：

1. release 状态为 `CANDIDATE`，组件齐全（快照、Join Graph、语义、`prompt_version`、`model_version`）。
2. 存在 `eval_run`：`release_id` 为该 release、`predictor = "agent"`、`status = COMPLETED`、`verdict = PASS`（Spec 03 E8），且 `target` 等于该 release 快照的 `target_key()`。
3. 当前进程的 `prompt_version` 与 `model_version` 等于 release 中记录的值（确保评测时的提示词与模型就是将要上线的）。
4. **不允许未解释的退化**（架构 15.4 修订）：与当前活跃 release 关联的评测运行逐题对比，凡"原来通过、现在失败"的题，必须提供非空 `regression_note`，否则拒绝；`regression_note` 与退化题号写入 `metadata_release_event.reason`。
5. 通过后：`release.evaluation_status = PASSED`、`eval_run_id` 写入；`move_pointer(action="PUBLISH")`；解决 STALE（下条）。全部在一个事务内。

STALE 自动解决：发布 release R 时，对该数据源每条未解决、来源为 `SCHEMA_DIFF` 或 `EXECUTION_DRIFT` 的标记：若对象是表/字段且在 R 的快照中存在，或对象是边/指标/维度且在 R 中存在（R 构建时已对照新快照校验通过），或对象在 R 中已不存在 → `resolve_stale(note="resolved by release R")`。`MANUAL` 标记只能人工解决。

`rollback_release`：

- 目标默认为指针的 `previous_release_id`；目标必须曾经 `PUBLISHED` 且属于同一数据源。
- **兼容性检查**（架构 5.5"Metadata 回滚不等于数据库回滚"）：现场采集一次快照（`collect_snapshot` + `save_snapshot`，去重），检查目标 release 的 Join Graph 与语义层引用的每张表、每个字段在现场快照中存在且类型族一致。不一致时拒绝并列出缺失对象；`force = true` 时允许回滚，但对缺失对象立即 `mark_stale(source="MANUAL")`，原因写入事件。
- `move_pointer(action="ROLLBACK")`。

`T2S_ENV=prod` 时，Spec 02/05 的无门禁 `activate` 被拒绝，只有本节两个函数能移动指针。CLI：`uv run python -m text2sql.release publish ID --by NAME --reason TEXT [--regression-note TEXT]`、`rollback [--to ID] --by NAME --reason TEXT [--force]`。

发布前检查清单（写入 README，代码无法验证的部分）：`uv run pytest tests/security` 全部通过（绕过测试集 100%，架构 16.1 修订的门禁之一；GitHub Actions 接入后改为 CI 强制）。

### 4.5 D4 公开 API

全部路由前缀 `/api/v1`，鉴权用 Spec 07 的 `get_principal`；`/healthz`、`/readyz` 保持 Spec 01 的路径。错误响应沿用 Spec 01 的统一结构。

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | `/query` | 任意 | 请求体 `QueryRequest`（Spec 08）；响应 `QueryApiResponse` |
| GET | `/query/{id}` | 本人；`admin` 可看同租户 | 查询记录：状态、SQL、口径、警告、澄清、血缘、执行信息；`data` 为空、`data_available: false` |
| POST | `/query/{id}/continue` | 本人 | 请求体 `ContinueRequest`；响应 `QueryApiResponse` |
| GET | `/query/{id}/trace` | 本人；`admin` 同租户 | `query_trace_event` 列表 + 对应 `query_audit` 摘要（不含 SQL 以外的数据） |
| POST | `/feedback` | 本人的查询 | `{"query_id", "rating": "up"\|"down", "comment"}`（评论 ≤ 500 字）|
| GET | `/datasources` | 任意 | 当前数据源：`datasource_id`、`dialect`、`db_version`、`active_release_id` |
| POST | `/metadata/sync` | `admin` | 入队 `metadata.sync_and_diff`，`202 {"job_id"}` |
| GET | `/jobs/{id}` | `admin` | 任务的 `kind`、`status`、`attempts`、`last_error`、`trace_id`（Spec 01 `jobs` 表字段；新增，异步同步与评测需要查询进度）；评测结果按 `trace_id` 对应的 `eval_run` 查询 |
| GET | `/metadata/releases` | `admin` | release 列表（组件、状态、评测结论、是否活跃） |
| POST | `/metadata/releases` | `admin` | 构建候选 release（`build_candidate_release`），返回 ID（新增） |
| POST | `/metadata/releases/{id}/publish` | `admin` | 带门禁发布，请求体 `{"reason", "regression_note"}`（新增） |
| POST | `/metadata/releases/{id}/rollback` | `admin` | 回滚到 `{id}`，请求体 `{"reason", "force"}` |
| GET | `/metadata/changes` | `admin` | 变更事件（可按快照过滤）与未解决的 STALE 标记 |
| GET | `/metrics` | `admin` | 运行指标（见下），参数 `days`（默认 7） |
| POST | `/evaluation/run` | `admin` | 入队 `evaluation.run`（Spec 03），请求体 `{"predictor", "release_id"}`，`202 {"job_id"}` |

`QueryApiResponse = QueryResponse（Spec 08）+ {"conversation_id": str, "adjustments": list[Adjustment]}`。

- **回答内容**（2026-11-24）：沿用 Spec 08 4.14 的受限字段；`sql` 是实际执行的语句（不是预览），`explain_sql = false` 时为空；`certified = false` 表示兜底结果（非认证口径）。`GET /query/{id}` 与 `GET /query/{id}/trace` 同样不返回检索明细、提示词与原始数据库错误。
- **API 版本**（2026-11-24）：外部路径保持 `/api/v1`（不改为 `/v1`），与 IR 版本（`ir_version`）、release 相互独立。MVP 只做向后兼容的新增（新字段、新接口），客户端须忽略未知字段；弃用公告期、迁移窗口与 Sunset 规则属于 Spec 00 §7 的仅契约内容，有外部调用方后再定。所有 Agent 结果状态（`completed`/`needs_clarification`/`rejected`/`failed`）都用 HTTP 200 返回，以 `status` 区分；4xx/5xx 只用于请求、鉴权与系统错误。

与第二十章相比：新增 `GET /jobs/{id}`、`POST /metadata/releases`、`POST /metadata/releases/{id}/publish`（发布需要显式入口与门禁）；暂不提供 `cancel` 与 `POST /datasources`（见 2.2）。

`/metrics` 返回（全部由 `query_execution`、`query_trace_event`、`metadata_stale_mark` 计算）：查询数（按状态）、`fallback_ratio`（总体与按 `fallback_reason`）、澄清率、澄清后放弃率（`needs_clarification` 后 24 小时内无续问的比例）、口径调整率（带 `edits` 的续问占比）、按错误类别的修复成功率、P50/P95 耗时、LLM token 合计、`trace_id` 覆盖率、未解决 STALE 数、按 DRAFT 口径执行的查询占比（架构 7.4、9.4、9.6 的监控项）。

### 4.6 D5 最小查询页面

- `apps/frontend/` 下三个静态文件，原生 JavaScript，无构建工具；`ui_enabled` 时由 FastAPI 以 `StaticFiles` 挂载在 `/ui/`。选择原因：MVP 只需一个页面，避免为此引入 Node 工具链；架构第二十一章 MVP 部署中的 `frontend` 由 API 进程承担。
- 功能：输入 API Key（只存 `sessionStorage`）；输入问题；显示状态、执行的 SQL（可折叠，标题写"执行的 SQL"，2026-11-24）、`certified = false` 时在结果上方显示"非认证口径：该查询未经结构化规划生成"、结果表格、口径标注（含调整按钮）、警告、澄清问题与选项按钮（有默认项时显示"都行"）、追问输入框、👍/👎 反馈、`trace_id`。
- 安全：所有数据用 `textContent` 渲染，禁止 `innerHTML`（结果数据与 SQL 都是不可信内容）；`/ui/` 响应头 `Content-Security-Policy: default-src 'self'`、`X-Content-Type-Options: nosniff`。

### 4.7 D6 应用容器化

- `deployment/docker/Dockerfile`：`python:3.12-slim`，复制 `uv`，`uv sync --frozen --no-dev`，以非 root 用户运行；同一镜像通过命令区分 `api`（`uvicorn`）与 `worker`（`python -m text2sql.jobs.worker`）。
- 按 Spec 01 4.8 的 profile 约定，在 `deployment/compose.yaml` 加入 `api`、`worker` 两个服务，放在 `profiles: [app]` 中；默认 `docker compose up` 仍只起数据库，`--profile app` 时起完整 MVP（架构第二十一章修订）。两个服务依赖数据库服务的 healthcheck；`api` 映射宿主端口 8000。

### 4.8 D7 MVP 验收

第 6 周末：发布一个通过门禁的 release，然后对 `mysql57` 与 `mysql80` 各执行一次 `run --predictor agent`（50 题全部 `REVIEWED`），报告归档到 `docs/acceptance/mvp-<日期>-<target>.md`（从 `report.md` 复制，去掉 `trace_id` 以外的运行环境细节）。两次 `verdict = PASS` 即 MVP 验收通过；否则报告中的失败分类作为阶段 2 优先级的输入（架构 23.1 第 6 周）。

## 5. 验收标准

1. 多轮（集成，`FakeLLM` 脚本化 `edit_ir`）：17.5 的三轮示例（"最近 30 天各行业工单数" → "只看已完成的" → "按数量从高到低排序"）每轮 IR 与 SQL 正确，`turn_index` 递增，第三轮仍带第一轮的默认口径标注；`AgentPredictor` 跑评测集中的多轮题时不再返回 `SKIPPED`。
2. 澄清回答：选择选项、`accept_default`（有默认/无默认两种）、文字回答三条路径各有测试；实体澄清无默认时 `accept_default` 返回 `failed`。
3. 口径调整：带 `DEFAULT_TIME_FIELD` 的结果返回"改为按完成时间统计"调整项，提交后新结果的时间字段改变且不再有该默认标注。
4. 跨轮 release 变化：父查询使用的维度在新 release 中被删除时，续问返回"元数据已更新"。
5. Diff（集成，两个版本）：在合成库上依次执行"删除 `t_work_order.handler_id`""把 `t_customer.industry_id` 改为 `VARCHAR`""删除 `t_sla_policy` 的唯一索引""新增一列""修改一条注释"，`sync_and_diff` 后：变更事件类型正确；`work_order_handler` 边、`department` 与 `employee` 维度被标记 STALE（含传递）；`work_order_sla_policy` 边以 `UNIQUENESS_LOST` 标记；新增列与注释修改不产生 STALE；被标记对象随即在查询中不可用（规划返回 `OBJECT_STALE`）。
6. 重命名映射后重新 Diff，"删除 + 新增"合并为 `COLUMN_RENAMED`。
7. 发布门禁：缺评测、评测 FAIL、`prompt_version` 不一致、存在未说明的退化题，四种情形各自被拒绝；补齐后发布成功，相关 STALE 自动解决，事件表记录 `PUBLISH`。
8. 回滚：目标 release 引用的字段在现场已删除时拒绝回滚；`force` 时成功并产生 `MANUAL` 标记；正常回滚后指针与事件正确。
9. API：表中每个接口有测试（含 401、403、他人查询 404）；`POST /query` 对四种 Agent 状态均返回 200；`/metrics` 的 `fallback_ratio` 与按 `query_execution` 手工计算一致。
10. 页面：`GET /ui/` 返回 200 且带 4.6 的安全响应头；单元测试确认 `app.js` 不包含 `innerHTML`、`outerHTML`、`insertAdjacentHTML`、`eval`；README 写明人工走查步骤（提问、澄清、调整口径、追问、反馈，以及结果值含 `<script>` 时按原文显示）。
11. `docker compose -f deployment/compose.yaml --profile app up -d` 后 `/readyz` 返回 200，页面可以完成一次查询。
12. D7：两个版本的 MVP 验收报告已归档。
13. `ruff`、`mypy`、`pytest`、`pytest -m integration` 全部通过。
14. （2026-11-22）验收 5 的场景中，依赖 `department` 维度的问答样例出现在 `ImpactReport.affected_examples`，且该样例在新 release 上不可用（Spec 05 4.9）；`sync_and_diff` 在快照变化后入队一条 `metadata.value_index` 任务，结构不变时不入队。
15. （2026-11-24）续问重新授权：用户 A 以 `pii_viewer` 完成父查询后，把 A 的角色改为 `analyst`，`POST /query/{id}/continue` 追问"加上手机号"返回 `rejected`；API Key 被停用（`active = false`）后续问返回 401。兜底结果的 API 响应 `certified = false`，页面显示非认证提示（人工走查项）。

## 6. 新增依赖

无（FastAPI 自带 `StaticFiles`；容器基础镜像不属于 Python 依赖）。

## 7. 风险与说明

- **指代消解收窄**：架构 17.5 修订要求"指代消解指向上一轮结果中的实体"。由于 MVP 默认不把结果发送给模型（11.6），本 Spec 只支持指向上一轮 IR 中已有对象的指代；指向结果行的指代返回"请说明具体名称"。若合规结论允许把维度标签（不含指标值）发给模型，可在阶段 2 放开。
- **Diff 基准是活跃 release 而非上一快照**：连续多次 DDL 而未发布新 release 时，Diff 会累积显示所有差异。这符合 STALE 的含义，但变更列表可能较长。
- **门禁依赖真实模型评测**：发布门禁要求 `agent` 评测 PASS，因此生产发布依赖 Spec 08 的模型选型落地。
- **绕过测试集不是运行期门禁**：它属于代码层面的保证，只能通过 CI 强制；在 GitHub Actions 接入前，依靠发布清单人工执行。
- **结果不落库**：`GET /query/{id}` 不返回数据，页面刷新后需要重新执行查询。
- **开放决策（需用户确认）**：
  1. Schema 变更审批人（1.4）：谁执行发布与回滚、谁写退化说明。
  2. 回滚 `force` 是否允许，以及需要谁批准。
  3. 是否接受页面由 API 进程以静态文件提供，而不是独立的 `frontend` 容器。
  4. 会话轮数上限（20）与澄清后放弃的判定窗口（24 小时）。
  5. MVP 验收是否要求 5.7 与 8.0 两个版本都 PASS（同 Spec 03 的开放决策 3）。
