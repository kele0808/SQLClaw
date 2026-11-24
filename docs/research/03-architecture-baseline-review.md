# SQLClaw Architecture Baseline v1.0.2 / v1.0.3 审阅与采纳

- 调研日期：2026-11-24（北京时间）
- 对照的仓库版本：kele0808/SQLClaw `ea743d3`（Spec 01 已实现；plan 02 Task 1～5 已完成，Task 6 待做）
- 审阅对象：用户提供的《SQLClaw Architecture Baseline》v1.0.2（2026-11-24）与 v1.0.3（在 v1.0.2 基础上新增 §9.3 Question-to-Answer）。v1.0.3 原文存档见 [04-architecture-baseline-v1.0.3.md](04-architecture-baseline-v1.0.3.md)（附章节对照表和原文已知问题）；本文只记结构、要点和采纳结论。
- 方法：逐条对照 `docs/architecture.md`、`docs/specs/01～09`、`docs/plans/`、`src/text2sql/`；4 个可疑引用用 arXiv 页面与 Crossref 核实。
- 标注约定同 01：**[已核实]** / **[未核实]**。
- 落地：`docs/specs/00-contracts.md`（新增）；spec 01 §4.6、03 §4.4/4.6/4.12、07 §4.6/4.8/§5/§7、08 顶部待定决策与 §4.2/4.5/4.7/4.9/4.14、09 §4.2/4.5 的 2026-11-24 修订；architecture 修订说明第 10 条、9.4、第二十四章；plan 02 Task 17 前置提醒。

---

## 0. 结论先行

1. **方向一致，不整体采纳。** 语义优先、LLM 只产出结构化表示、确定性编译、网关在模型之外强制校验、评测前移，这些与 architecture 修订版一致。
2. **文档的 Spec 目录（§15）与仓库不符。** 它假设存在 `00-contracts.md` 与 10～17，并把 Schema Drift 放进 02、把 09 改成"API / 异步 / 定时 / 导出"；仓库只有 01～09，Schema Diff 在 09（02 §2.2 明确不做）。本次**不重排编号**，只新增一份精简的 00。
3. **§16"已知问题"表约 6 条成立**（01 租约续约、03 Decimal 比较、03 缺弃答/业务不变量题、07 PII 规则矛盾、07 进程内 Semaphore、README 落后），另有 08 token 预算缺失；其余多条在现有 spec 中已解决或不适用（见 §3）。
4. **唯一的根本冲突**是"LLM 不得写 SQL + Exploratory 单表 DSL"与现有 `FALLBACK_SQL` 兜底通道（architecture 9.4、06、07、08 A7、03 `fallback_ratio`）。**暂不决定**：保留兜底、加"非认证口径"标识并单独统计，写 08 plan 之前用评测数据决定（见 §4）。

## 1. 文档结构摘要

| 章节 | 内容 |
| --- | --- |
| §0 | 定位"Governed Semantic Query Engine"；7 项架构决策；3 条底线（LLM 不绕过 DSL/IR/Policy/Gateway；权限由确定性 Policy Engine 判断；不默认多候选/多 Agent/多轮修复）；Governed / Exploratory 双模式；M0 冻结 8 类横切契约 |
| §2 | Control Plane / Data Plane / Customer Data / Governance 四区；模块化单体 + Worker |
| §3 | Semantic Layer 对象、Metric 必要字段（Grain 等）、Bootstrap 只产候选、Lifecycle、Semantic CI、Exploratory → Governed 晋升、Schema Drift 契约（Additive / Breaking / Semantic / Permission） |
| §4 | Governed DSL 与 Exploratory DSL v1（单表、白名单算子）；`COUNT(*)` 用 `operand: {type: all_rows}` |
| §5 | Pre-DSL Coverage Gate、Post-IR Capability Gate、Clarification Pool、多轮 Session 契约 |
| §6 | DSL → Logical IR → Authorized Logical IR → Physical Plan → Compiler |
| §7 | 租户概念、PDP/PEP、`policy_decision_id`、纵深防御、PII 聚合披露 |
| §8 | 查询生命周期（14 个状态）与定时任务生命周期；资源硬限制；PostgreSQL 执行槽位租约；取消；Job Queue 可靠性；LLM 错误分类与重试边界；四层 Token 预算；异步/定时/导出 |
| §9 | 多路召回 + RRF；三类缓存（Retrieval / Compile / Result）；检索降级矩阵；**v1.0.3 新增 §9.3 Question-to-Answer**（主流程图、时序图、9.3.3 多轮续问规则、9.3.4 统一 QueryRun 追踪字段） |
| §10 | Answer Payload 受限、`sql_preview` 与 `executed_sql` 分离、外部 API 版本契约 |
| §11 | 五层评测、Walking Skeleton 20 轮、Coverage 指标、Semantic Oracle、发布门禁 |
| §12～14 | Platform Release / Semantic Bundle / Policy Bundle / Execution Snapshot；审计、隐私、观测；技术选型 |
| §15～19 | Spec 目录 00～17 与优先级；已知问题表；M0～M5 路线；MVP 验收；决策记录 |

## 2. v1.0.3 §9.3 的审阅意见

§9.3.3（会话只存已确认的意图、条件和版本引用，**从不存授权**；每轮生成完整新 DSL 并完整重新校验）与 9.3.4（统一追踪字段）采纳为契约。主流程图有以下问题，已在 00 / 08 中按现有设计写明：

1. 可见性过滤应在检索**内部**（候选生成时）生效，而不是检索之后再过滤；否则无权对象已参与排序。
2. 检索缓存的查询应在检索**之前**；结果缓存检查应在 PDP **之后**、准入与执行槽位领取**之前**（MVP 两者都不做）。
3. 审计必须在返回答案**之前**写入，写入失败不返回结果（保留 07 §4.11 fail-closed，文档"按部署策略决定"更弱）。
4. 规划器发现的歧义（`JOIN_ROLE`，Spec 06）必须能回到澄清；文档只在 DSL 之前设澄清入口。
5. 流程图缺少 Schema Drift、`budget_exceeded`、LLM 失败、等待澄清的出口状态。
6. 文档"结构性修复最多 1 次"与 08 `repair_ir` 最多 2 次不一致，需统一（08 §4.9 修订）。

## 3. §16 已知问题逐条核对 **[已核实：对照仓库文件]**

| 文档条目 | 结论 | 依据 |
| --- | --- | --- |
| 01 Job Lease 缺续约 | 成立 | spec 01 §4.6 `claim` 只设一次 `locked_until`；`jobs/queue.py` 无续约函数；值索引任务可能超过 300 秒租约 |
| 03 Decimal/int/float 全转 float | 成立 | spec 03 §4.6 规则 1 |
| 03 缺 Coverage/Abstention/Oracle | 部分成立 | 已有 CLARIFY/REJECT 与 `clarify_recall`；缺"超出覆盖应弃答"和业务不变量题 |
| 05 DSL Schema 版本不明确 | 不成立 | spec 06 `ir_version: Literal[2]`；08 用 `model_json_schema()` 约束输出 |
| 05 Bootstrap 误把 SQL 用法当口径 | 不成立（MVP） | spec 05 §2.2 把 SQL 抽取与 LLM 起草放在阶段 2，且只产 DRAFT |
| 05 发布与回归不完整 | 基本不成立 | 05 S6 编译样例、09 D3 评测门禁与未解释退化拦截、09 `affected_examples` |
| 07 PII 聚合规则矛盾 | 成立 | 07 §4.6 analyst"任何位置含函数参数"拒绝 vs §5 第 5 条"`COUNT(DISTINCT phone)` 对两种角色都通过" |
| 07 进程内 Semaphore | 成立（MVP 可接受） | 07 §4.8 `threading.BoundedSemaphore` |
| 07 Policy Rewrite 与 PDP 未绑定 | 不适用 | 07 §2.2 不做 AST 行级改写；隔离靠租户视图/账号 |
| 08 Coverage 与澄清循环依赖 | 未发现 | 08 §4.12 为单向流程，歧义评分在规划之后且写明理由 |
| 08 Out-of-Coverage / Exploratory 分支不完整 | 只在新设计下成立 | 现设计：`OUT_OF_SCOPE` → rejected，`Unsupported` → 兜底 |
| 08 Bounded Regeneration 边界不明确 | 基本不成立 | 08 §4.2 重问一次、§4.9 修复表；只缺 429 与预算 |
| 09 取消未贯穿执行层 | 有意推迟 | 08/09 §2.2；超时由 07 §4.8 `KILL QUERY` 兜底 |
| 多轮继承旧授权 / 局部 DSL | 不成立 | 续问 `apply_edits` 得到完整 IR → `run_query`；网关每次完整校验（07 §4.8） |
| 缓存跨用户复用 | 不成立 | 无结果缓存（architecture 17.6）；编译缓存键与用户无关（06 §4.11） |
| Token 只记录不限制 | 成立 | 08 只有 `agent_deadline_s`，无 token 上限、未处理 429 |
| Schema Drift 同名字段误替换 | 不成立 | STALE（02 §4.8）、执行期漂移不重试（07 §4.8）、重命名只接受人工映射（09 §4.3）、重建对象新 UUID（02 M4） |
| 异步/定时/导出授权 | 不在 MVP 范围 | — |
| API 与 DSL/IR 版本耦合 | 部分成立 | 已有 `/api/v1`，缺弃用规则 |
| 检索降级 | 不适用（MVP） | MVP 检索为确定性关键词 + 值索引，无向量与 Reranker |
| README 落后 | 成立 | README 仍写"设计阶段，只有架构文档" |

## 4. 采纳表

| 条目 | 结论 | 原因 | 落地位置 |
| --- | --- | --- | --- |
| 不变量：模型输出不经网关不执行；每次执行完整重校验；会话不携带授权；STALE 阻断；不自动替换同名/重命名字段；审计失败不返回结果 | **采纳** | 现设计已大体满足，写成显式契约 | 00 §1 |
| 名称映射（状态、版本对象、DSL/IR、API 路径） | **采纳** | 避免两套术语并存，不改现有名称 | 00 §2 |
| `SCHEMA_DRIFT_DETECTED` 错误语义 | **采纳为别名** | 映射到 `DB_UNKNOWN_TABLE` / `DB_UNKNOWN_COLUMN` / `OBJECT_STALE` | 00 §3 |
| `COUNT(*)` 用 `operand: {type: all_rows}`；带类型引用 `{type, id}` | **采纳（契约）** | IR v2 当前无 `null` 表示全部行，作为后续扩展的约束 | 00 §4 |
| LLM 错误分类、429 `Retry-After`、剩余时限不足不重试、单题 token 上限、修复次数统一 | **采纳** | 08 缺 429 与预算 | 08 §4.1/4.2/4.9，00 §5 |
| 多候选默认关闭（`understanding_samples = 1`） | **采纳** | 底线 3；k = 3 收益未实测 | 08 §4.1/4.5，architecture 9.6 / 第二十四章 |
| 规划器发现的歧义可转澄清 | **保留并写明** | 现设计已如此，文档缺这条路径 | 08 §4.7 |
| 多轮续问规则（9.3.3） | **采纳** | 与 09 D1 一致，写成显式规则 | 08 §4.12，09 §4.2 |
| 统一追踪字段（9.3.4） | **采纳（名称映射）** | 字段大部分已在 `query_execution` / `query_audit` | 00 §6 |
| Answer Payload 受限、`sql_preview` vs `executed_sql` | **采纳** | 现响应 `sql` 字段语义不清 | 08 §4.14，09 §4.5 |
| int/Decimal 精确比较 | **采纳** | 修 03 缺陷 | 03 §4.6 |
| 超出覆盖应弃答题、业务不变量（Semantic Oracle）与"SQL 能跑但口径错"题 | **采纳** | 补 03 缺口 | 03 §4.4、§4.12 |
| PII 与 `COUNT(DISTINCT)` 统一规则；限制 pii_viewer 在 WHERE 中用 PII | **采纳** | 修 07 矛盾与推断风险 | 07 §4.6、§5 |
| Semaphore 只限单进程 | **采纳（风险说明）** | 多实例推迟 | 07 §4.8、§7 |
| Job 租约续约与心跳 | **采纳** | 值索引长任务需要 | 01 §4.6；plan 02 Task 17 之前实现 |
| Organization/Workspace、PDP/PEP、Authorized IR、三类缓存、执行槽位租约、定时/导出、四层预算、OTel GenAI、检索降级矩阵、API 弃用流程 | **仅契约，MVP 不实现** | 单人学习项目 6 周 MVP 范围外；先记边界 | 00 §7 |
| 新建 Spec 10～17、重排 02/09 职责 | **不采纳** | 与现有编号冲突；需要时再写 | — |
| Walking Skeleton 20 轮替代 50 题 | **不采纳** | 03 已有 50 题；20 轮可作冒烟子集 | — |
| Governed 只能用已发布对象 | **不采纳（MVP）** | 05 §4.2 允许 DRAFT 执行并标注"口径未经业务确认"，冷启动需要 | — |
| 审计失败"按部署策略决定" | **不采纳** | 保留 07 §4.11 fail-closed | 00 §1 |
| **LLM 不得写 SQL + Exploratory 单表 DSL** | **待定决策** | 见下 | 08 顶部、architecture 9.4 / 修订说明第 10 条 |

**待定决策：`FALLBACK_SQL` 还是 Exploratory 单表 DSL。** 现在保留 `FALLBACK_SQL`，结果必须带"非认证口径"标识并单独统计。写 Spec 08 的 implementation plan 之前，用 Spec 03 评测的 `fallback_ratio` 和 Spec 06 Q9 的 `ir_expressibility` 标注决定：兜底占比低 → 删除兜底、采用 Exploratory 单表 DSL；占比高 → 扩展 IR 或启用语义视图通道（Spec 06 §7）。

## 5. 文档自身的技术问题

1. 查询生命周期图缺 `budget_exceeded`、`expired` 的转移（§13.1 审计里有）；created/classified 阶段的 LLM 失败与超预算无出口；queued、awaiting_clarification 无到 cancelled 的边；"过期"有 `expired` / `clarification_expired` 两种名字。
2. 执行槽位租约：租约过期而数据库查询仍在执行时会被重分配，导致超额并发；租约应 ≥ 查询超时 + 余量，回收前先 `KILL QUERY`。MVP 更简单的做法：租约 = 超时 + 余量，并用 MySQL 原生 `max_user_connections`（按租户账号）做硬上限。
3. LLM 重试与时限：`llm_timeout_s = 60`、总时限 90 秒时"超时重试一次"放不下，需要"剩余时限不足不重试"。
4. 检索缓存既把可见范围指纹放进键、又要求命中后重新过滤，二选一即可；结果缓存参数 HMAC 需要密钥管理，MVP 不做。
5. DSL 示例引用歧义：`order_by.field_id` 指向指标、`time_range.dimension_id` 指向字段；Exploratory 示例 `field` 与 `field_id` 混用 → 改为带类型引用。
6. Exploratory 的 `COUNT DISTINCT`"条件支持"没给条件，小样本抑制没给阈值。
7. §18 的 Time to First Correct Answer、Bootstrap Precision@K 需要真实业务 Owner，本项目无法测量。

## 6. 引用核实

| 引用 | 结果 |
| --- | --- |
| EntSQL，arXiv 2606.03363 | **[已核实]** 存在，HKUST(GZ) / 阿里，1,066 条中英语义样例 |
| PlanPool，arXiv 2610.02739 | **[已核实]** 存在，Cornell / AWS，"Beyond Correctness: Resolving Underspecification in Agentic Text-to-SQL" |
| NIFT-SQL，arXiv 2611.12731 | **[已核实]** 存在，SAP，2026-11；依赖每个客户的问题做微调，MVP 用不上 |
| XiYan-SQL，DOI 10.1109/TKDE.2026.3657851 | **[已核实：Crossref]** TKDE 第 38 卷第 4 期，2474–2487 页 |
| Agentar-Scale-SQL，arXiv 2509.24403 | **[已核实]** 存在 |
| Spider 2.0 ICLR 链接；Snowflake、dbt、OWASP、OTel、PostgreSQL、JSON Schema 文档链接 | **[未核实]** 未逐个打开 |

## 7. 未验证项

- `understanding_samples` 1 与 3 的准确率/成本对比：第 5 周用 Spec 03 E9 实测。
- `fallback_ratio` 与 Q9 `FALLBACK_EXPECTED` 占比：决定兜底去留的输入，尚无数据。
