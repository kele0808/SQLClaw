# Spec 00：跨 Spec 契约

- 状态：已确认（2026-11-24）
- 日期：2026-11-24
- 修订记录：
  - 2026-11-24：新建。采纳《SQLClaw Architecture Baseline》v1.0.2 / v1.0.3 中与现设计一致的跨模块契约（审阅与采纳表见 `docs/research/03-architecture-baseline-review.md`）。
- 对应架构文档：修订说明第 10 条、9.4、第二十四章
- 定位：只写**跨 Spec 的不变量、名称映射和预留契约**，不定义新表、新接口。具体字段与行为仍以 01～09 为准；本 Spec 与 01～09 冲突时，以 01～09 为准并回来修订本 Spec。
- 被依赖：01～09 全部

## 1. 不变量

以下规则任何 Spec 的实现都不得违反；违反视为缺陷，不是可配置项。

| 编号 | 不变量 | 现有落实 |
| --- | --- | --- |
| I1 | 模型的任何输出（IR、兜底 SQL、修复结果）不经 `Gateway.execute` 不得执行 | 07 §4.8、08 §4.8/4.9 |
| I2 | 每次执行都完整重新校验，不信任调用方此前的 `check` 结果或上一轮的结论 | 07 §4.8 |
| I3 | 每一轮续问都产出**完整** IR，并重新经过规划、网关校验；会话只保存已确认的意图、条件和版本引用，**从不保存授权**；每轮按当前 `principal` 与当前活跃 release 重新判断 | 09 §4.2、08 §4.12 |
| I4 | STALE 对象立即退出检索与规划，查询返回 `OBJECT_STALE`；执行期发现未知表/字段时不重试，先 `mark_stale` | 02 §4.8、07 §4.8、08 §4.9 |
| I5 | 不自动把失效字段替换为同名、相似或"看起来等价"的字段；重命名只接受人工映射 | 09 §4.3 |
| I6 | 审计在返回结果**之前**写入；审计写入失败时不返回结果（`INTERNAL_ERROR`） | 07 §4.11 |
| I7 | 用户无权看到的内容（`PII`/`SECRET` 字段的原值、其他租户的数据）不得进入模型上下文；可见性在检索内部生效（如值索引按租户检索），不是检索之后再过滤 | 08 §4.3/4.4、02 M10 |
| I8 | 歧义可以在两个位置被发现：理解之前（术语、实体）和规划之后（`JOIN_ROLE`、候选分歧），两处都必须能转到澄清 | 08 §4.7 |
| I9 | 兜底 SQL（`plan_mode = FALLBACK_SQL`）的结果一律带"非认证口径"标识，单独统计，不得自动导出为问答样例 | 08 顶部待定决策、08 §4.14 |

## 2. 名称映射（Baseline v1.0.x → 现有名称）

不改现有名称；Baseline 文档中的名称只作为对照。

### 2.1 查询状态

| Baseline 状态 | 现有表示（08 §4.12 `status` + `error_code`） |
| --- | --- |
| `created` / `classified` / `queued` / `running` | `running`（MVP 同步执行，不细分） |
| `succeeded` | `completed` |
| `awaiting_clarification` | `needs_clarification` |
| `rejected` | `rejected` |
| `abstained` | `failed` + `OUT_OF_COVERAGE`（2026-11-24，08 §4.5） |
| `unsupported` | `failed` + `UNSUPPORTED_ON_VERSION` 等；或进入兜底（见 08 待定决策） |
| `failed` | `failed` |
| `timed_out` | `failed` + `TIMEOUT` / `AGENT_DEADLINE_EXCEEDED` |
| `budget_exceeded` | `failed` + `LLM_BUDGET_EXCEEDED`（08 §4.2） |
| `cancel_requested` / `cancelled` | 不实现（08、09 §2.2，阶段 2） |
| `clarification_expired` | 不单设状态：澄清后不续问即结束；`/metrics` 统计"澄清后放弃率"（09 §4.5） |

### 2.2 语义对象生命周期

| Baseline | 现有（05 S3） |
| --- | --- |
| `draft` / `review` | `DRAFT` / `REVIEWING` |
| `approved` | `VERIFIED` |
| `released` | 进入已发布的 release（09 D3），不是对象状态 |
| `deprecated` | `DEPRECATED` |

差异：05 §4.2 允许 DRAFT/REVIEWING 对象参与查询并标注"口径未经业务确认"；Baseline 要求 Governed 只用已发布对象。MVP 保留 05 的做法。

### 2.3 版本对象与表示层

| Baseline | 现有 |
| --- | --- |
| Platform Release / Semantic Bundle / Policy Bundle / Metadata Snapshot | 一个 `metadata_release`（02 M6）：快照 + Join Graph 版本 + 语义版本 + `prompt_version` + `model_version`；策略来自 05 字段注解与 07 角色，随 release 固定 |
| Execution Snapshot | `query_execution.release_id` + `query_audit.release_id`（release 本身不可变） |
| Governed DSL | `QueryIR` v2（06 §4.2，`ir_version = 2`） |
| Logical IR | `compile_ir` 规划后的 `planned_ir`（06 §4.5） |
| Authorized Logical IR | **MVP 不存在**：隔离靠租户视图/账号 + 网关校验（07 §2.2 不做行级改写） |
| Physical Plan | 06 §4.6 逻辑计划（分块、预聚合、连接顺序） |
| `policy_decision_id` | MVP 不单设；`query_audit.id` 承担同一追溯作用 |
| 外部 API `/v1/...` | 保持 `/api/v1/...`（01 修订记录、09 D4）；弃用规则见 §7 |

## 3. 错误码命名空间

错误码大写下划线，按来源分前缀；新增错误码写入 `text2sql.common.errors`（01），不在多个模块重复定义。

| 前缀 / 码 | 来源 |
| --- | --- |
| `METADATA_*`、`RELEASE_*`、`STALE_*` | 02 |
| 规划错误（`UNKNOWN_*`、`OBJECT_STALE`、`AMBIGUOUS_JOIN_ROLE`、`FANOUT_UNSAFE`、`UNSUPPORTED_ON_VERSION` …） | 06 §4.4 |
| 网关类别（`DB_UNKNOWN_TABLE`、`DB_UNKNOWN_COLUMN`、`TIMEOUT`、`TOO_BUSY`、`PERMISSION_DENIED` …） | 07 §4.10 |
| `AGENT_*`、`LLM_*`、`OUT_OF_COVERAGE` | 08 |
| `CONVERSATION_*` | 09 |

`SCHEMA_DRIFT_DETECTED`（Baseline §3.8）**不新增**，作为下列现有码的统称：执行期 `DB_UNKNOWN_TABLE` / `DB_UNKNOWN_COLUMN`（07 §4.8）与规划期 `OBJECT_STALE`（06）。指标与日志按这三个码统计"漂移"。

## 4. 表示层约定（后续扩展 IR 或新增 DSL 时遵守）

- **`COUNT(*)`** 用显式操作数：`{"function": "count", "operand": {"type": "all_rows"}}`；`COUNT(col)` 用 `{"type": "field", "field_id": "..."}`。不得用 `field_id: null` 表示"全部行"（JSON `null` 是显式值，与缺省不同）。当前 IR v2 的计数由语义层指标定义（如 `count_distinct(t_work_order.id)`，05 §4.5），不受影响。
- **带类型引用**：引用指标、维度、字段时写成 `{"type": "metric" | "dimension" | "field", "id": "..."}`，不使用一个裸 `field_id` 同时指向指标和维度。IR v2 现有字段按名称分区（`metrics` / `dimensions` / `filters`），不需要改；新增的跨类引用（如排序、派生表达式）遵守本条。

## 5. LLM 错误分类（摘要，细节见 08 §4.2）

| 类别 | 处理 | 计入 |
| --- | --- | --- |
| 传输错误、5xx、超时 | 重试 1 次（首次 + 1）；剩余时限不足以再等一次 `llm_timeout_s` 时不重试 | 调用次数、token |
| 429 | 有 `Retry-After` 且等待后仍在剩余时限内才重试 1 次；否则失败 `LLM_RATE_LIMITED` | 同上 |
| 输出无法解析 / 不符合 JSON Schema | 重问 1 次；仍失败 `LLM_OUTPUT_INVALID` | 同上，不计修复次数 |
| 规划可修复错误（IR 层面） | `repair_ir`，计入 `max_repair_attempts` | 修复次数 |
| 语义歧义 | 不重试，转澄清 | — |
| 权限、STALE、版本不支持 | 不重试，拒绝或失败 | — |
| 单题 token 超过 `llm_max_tokens_per_query` | 停止后续模型调用，失败 `LLM_BUDGET_EXCEEDED` | — |

## 6. 统一追踪字段（Baseline v1.0.3 §9.3.4 → 现有字段）

| Baseline 字段 | 现有字段 |
| --- | --- |
| `query_run_id` / `parent_query_run_id` | `query_execution.query_id` / `parent_query_id` |
| `session_id` / `turn_id` | `conversation_id` / `turn_index` |
| `mode` | `plan_mode`（`IR` / `FALLBACK_SQL`） |
| coverage 状态、终态 | `status` + `error_code` |
| 版本 ID | `release_id`（含 `prompt_version`、`model_version`） |
| `policy_decision_id` | `query_audit.id` |
| IR / 计划哈希 | `canonical_ir_hash`（06 §4.2）；SQL 哈希 `query_audit.sql_hash` |
| 缓存命中、降级 | MVP 无（编译缓存为进程内 LRU，不记录） |
| LLM 重试 / 修复次数、token | `llm_calls`、`repair_count`、`llm_input_tokens`、`llm_output_tokens` |
| 延迟、行数、字节 | `elapsed_ms`、`row_count`、`truncated`（字节数阶段 2） |
| 是否执行了 SQL | `query_audit.action = EXECUTE` 是否存在 |

日志与追踪表**不记录**结果数据、PII 明文与提示词全文（08 §4.13）。

## 7. 仅契约、MVP 不实现

下列内容只记边界，进入实现前需要单独写 Spec 或修订对应 Spec：

| 内容 | 边界（实现时必须满足） |
| --- | --- |
| Organization / Workspace / 完整 PDP-PEP / Authorized IR | 平台租户与数据租户（07 `tenant_id`）不混用；授权决策只产生一次并被改写、网关、审计共同引用 |
| 检索缓存 / 编译缓存 / 结果缓存 | 三类分开设计；检索缓存不能跨可见范围复用；编译缓存基于规划后 IR；没有可靠的数据版本时不做结果缓存（architecture 17.6） |
| 数据源执行槽位租约（多实例并发） | 原子领取、续约、回收；租约 ≥ 查询超时 + 余量，回收前先 `KILL QUERY`；在此之前只有 07 的进程内 Semaphore |
| 异步查询、定时任务、导出 | 每次触发和执行前重新授权；导出不是在线限制的旁路 |
| Organization / Workspace / 步骤级 token 预算 | MVP 只有单题上限（§5）与总时限 `agent_deadline_s` |
| OpenTelemetry GenAI 约定 | 不默认采集输入/输出消息全文 |
| 检索降级矩阵 | 可见性过滤与网关不可降级（fail-closed）；MVP 检索无向量与 Reranker，不涉及 |
| API 弃用流程 | 同一主版本内只做向后兼容的新增；破坏性变更进新主版本，带公告期与迁移说明 |
