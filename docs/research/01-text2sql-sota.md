# Text2SQL 前沿调研：多表 / 大 Schema 场景，以及对我们 spec 的修改建议

- 调研日期：2026-11-22（北京时间）
- 对照的仓库版本：kele0808/text2sql `da3ded3`（plan 02 已写，未开工）
- 方法：用 GitHub API 和浅克隆核对仓库，直接读关键源码；论文全部用 arXiv API 核对 ID 和摘要；排行榜分数取自官网，查看日期为 2026-11-22。
- 标注约定：**[已核实]** 表示这次亲自打开过源码、摘要或页面；**[未核实]** 表示只来自二手资料或记忆，没有打开原文。

---

## 0. 结论先行

1. Codex 那份清单有一半能参考，但有 4 处问题：
   - **Vanna 已在 2026 年归档**。
   - **tianyuqic1/Text2Sql 是 0 star、只有 1 个 commit 的个人 demo**。
   - **Text-2-SQL-Agent/text2sql 只有 2 star，它的 "95%" 只是在 20 道 Spider 题上测出来的**。
   - **WrenAI 已经整体转型**：Codex 描述的旧版 Haystack 流水线（wren-ai-service）在 2026-Q2 被挪到了 `legacy/v1` 分支。
   - 另外还有几处小问题：DIN-SQL 和 CHESS 的链接写错了；NL2SQLBench 仓库只有 3 star。
2. "表多了就不行"在研究里有硬数据支撑：
   - **Spider 2.0-Snow** 上，CHESS + GPT-4o 只有 **1.28%**，DIN-SQL + GPT-4o 是 **0.00%**。这些都是在 BIRD 上很强的经典流水线。
   - 企业数仓基准 **BEAVER** 上，GPT-5.2 加上 SOTA agent 只有 **10.8%**。
   - 真正有效的不是"更强的 SQL 生成"，而是把**数据库上下文工程**做好：
     - 分层检索：领域 → 表 → 列；
     - 值索引；
     - 结构压缩：同构表、重复审计列；
     - 已验证的问答样例记忆；
     - 多候选 + 一致性，答案不一致时就转澄清。
3. **我们的 IR + 语义层 + 确定性编译器路线，和工业界的证据是一致的**：
   - 百度 CoeusBI 2026 用的就是 IR + 方言无关的确定性编译器；
   - dbt 2026 的基准：语义层范围内 100% 正确，直写 SQL 只有 51%~62%。
   - 但这条路线的代价也很明确：**IR 和语义层覆盖不到的问题是 0 分**（dbt 的 "too many hops" 题全部 0%）。
   - 所以"表多"的压力会转移到两处：**语义层冷启动的人工成本**，以及**兜底通道**。这两处正是我们 spec 现在最薄弱的地方。
4. 建议改 5 处 spec，按优先级排列：
   1. 03 加分阶段召回指标和"干扰表压力测试"；
   2. 05/08 加已验证的 Q→IR 样例库；
   3. 02/05/08 加值索引；
   4. 08 加多候选 IR + 一致性投票，不一致时转澄清；
   5. 02/04/05 为规模化打钩子：表族识别、业务域字段、多来源候选边。

   明确**不要做**的：微调/RL、大规模 test-time scaling、MAC-SQL 式"整库丢给 LLM 剪枝"、无约束的 agent 自由探索、换成图数据库。

---

## 1. 仓库核验（GitHub API，2026-11-22 查询；`pushed_at` 为 UTC）

| 仓库 | 实际地址 | Star | 最近推送 | 许可 | 结论 |
| --- | --- | --- | --- | --- | --- |
| WrenAI | [Canner/WrenAI](https://github.com/Canner/WrenAI) | 18.4k | 2026-11-20 | 按路径分：core/sdk 为 Apache-2.0，docs 为 CC-BY-4.0（预留 AGPL） | **活跃，但已转型**。CHANGELOG「2026-Q2 Repo consolidation」把 `wren-ai-service`、`wren-ui` 归档到 [`legacy/v1`](https://github.com/Canner/WrenAI/tree/legacy/v1)。现在是"给 Claude Code/Cursor 等 agent 用的语义层 + 上下文层 CLI"。Codex 描述的旧流水线要去 legacy 分支看 |
| Vanna | [vanna-ai/vanna](https://github.com/vanna-ai/vanna) | 23.8k | 2026-02-02 | MIT | **已归档（archived=true）**。最后一次提交是 2.0.2。2.0 已经从"DDL/文档/SQL 训练 RAG"改成"工具调用 agent + 记忆"，0.x 的代码放在 `src/vanna/legacy` |
| DB-GPT | [eosphoros-ai/DB-GPT](https://github.com/eosphoros-ai/DB-GPT) | 20.3k | 2026-11-22 | MIT | 活跃。定位是平台，参考价值主要在 AWEL 工作流，text2sql 本身不深 |
| QueryWeaver | [FalkorDB/QueryWeaver](https://github.com/FalkorDB/QueryWeaver) | 1.1k | 2026-11-17 | **AGPL-3.0** | 活跃，规模小。机制见 2.4。AGPL 不要抄代码 |
| Chat2DB | 已迁移到 [OtterMind/Chat2DB](https://github.com/OtterMind/Chat2DB) | 28.4k | 2026-11-11 | 修改版 Apache-2.0（5.3.0 起） | Codex 给的地址会 301 跳转。它是客户端产品；Chat2DB-Agent 在 Spider2-Snow 上得 38.39 |
| SQLBot | [dataease/SQLBot](https://github.com/dataease/SQLBot) | 7.2k | 2026-11-20 | **修改版 GPLv3**（不能去掉 LOGO 等） | 活跃。机制：表检索是向量和关键词混合，`TABLE_EMBEDDING_ALPHA=0.4`，取 top 10（`backend/common/core/config.py`）；另有术语库和"数据训练"样例，各取 top 5 |
| supersonic | [tencentmusic/supersonic](https://github.com/tencentmusic/supersonic) | 5.2k | 2026-11-06 | Apache-2.0 + 附加条件 | 活跃。**和我们最像的中文项目**，机制见 2.2 |
| MAC-SQL | [wbbeyourself/MAC-SQL](https://github.com/wbbeyourself/MAC-SQL) | 352 | 2025-02-27 | 未声明 | 论文代码，基本停更 |
| DIN-SQL | Codex 给的地址 **404**。实际是 [MohammadrezaPourreza/Few-shot-NL2SQL-with-prompting](https://github.com/MohammadrezaPourreza/Few-shot-NL2SQL-with-prompting) | 423 | 2024-03-30 | MIT | 论文代码，停更 |
| CHESS | Codex 给的 ServiceNow/CHESS **404**。实际是 [ShayanTalaei/CHESS](https://github.com/ShayanTalaei/CHESS) | 289 | 2025-05-26 | Apache-2.0 | 论文代码，停更。机制见 2.3 |
| SQLCoder | [defog-ai/sqlcoder](https://github.com/defog-ai/sqlcoder) | 4.0k | 2024-05-23 | Apache-2.0 | **停更 2.5 年**，是模型仓库，与我们无关 |
| NL2SQLBench | [neurdb/NL2SQLBench](https://github.com/neurdb/NL2SQLBench) | **3** | 2025-11-13 | 未声明 | 论文配套，几乎没人用。**思路有用**：把系统拆成 Schema Selection / Candidate Generation / Query Revision 三个模块，分别统计指标和 token |
| Text-2-SQL-Agent/text2sql | [链接](https://github.com/Text-2-SQL-Agent/text2sql) | **2** | 2026-04-04 | MIT | **玩具级**：约 2.4k 行 Python，只给 LLM 一个 `execute_sql` 工具让它自己探索。README 的 "19/20 (95%) on Spider" 只有 20 题，没有统计意义 |
| tianyuqic1/Text2Sql | [链接](https://github.com/tianyuqic1/Text2Sql) | **0** | 2026-07-06 | 无许可证 | **个人 demo，只有 1 个 commit**，约 14k 行。README 的模块清单和我们的架构几乎一样（LangGraph、语义层 YAML、混合检索、sqlglot 守卫），**参考价值低于我们自己的 spec**。无许可证意味着不能复用代码 |
| NL2SQL_Handbook | [HKUSTDial/NL2SQL_Handbook](https://github.com/HKUSTDial/NL2SQL_Handbook) | 1.6k | 2026-11-11 | 未声明 | 活跃的论文索引。对应综述是 [arXiv 2408.05109](https://arxiv.org/abs/2408.05109) |
| BIRD | [bird-bench.github.io](https://bird-bench.github.io/) | — | 2026-10-28 有更新 | 数据 CC BY-SA 4.0 | 活跃。新出了 LiveSQLBench-Large（约 1K 列/库）和 BIRD-Interact |

另外补充三个相关仓库：
- [Snowflake-Labs/ReFoRCE](https://github.com/Snowflake-Labs/ReFoRCE)：143 star，Apache-2.0
- [Satissss/LinkAlign](https://github.com/Satissss/LinkAlign)：85 star
- [xlang-ai/Spider2](https://github.com/xlang-ai/Spider2)：890 star

---

## 2. 重点项目的真实机制（读源码得出）

### 2.1 WrenAI v5（main `31390f6`）[已核实]

文档在 `docs/core/concepts/correctness.md` 和 `memory_system.md`，代码在 `core/wren/src/wren/`。

- **MDL**：用 YAML 声明 model、column、relationship、calculated field、view 和 cube。agent **只能引用建模过的对象**，没建模的列它"看不见"。
- **Agent 写的是"面向 MDL 的 SQL"**，然后由 `wren dry-plan` 展开成物理 SQL。引擎在 `core/wren-core`（Rust，`mdl/`、`logical_plan/`），展开时会：
  - 把 relationship 内联成 CTE；
  - 注入 `knowledge/rules/` 里的策略过滤；
  - 做列级访问控制。
  - 列名写错时，返回可用的列，并给出结构化错误。
- **Memory**（`core/wren/src/wren/memory/`）：
  - 真相源是仓库里的 `knowledge/sql/*.md`，也就是确认过的 NL→SQL 对，可以走 PR 审核。
  - 索引是派生出来的，可以随时重建：默认用 grep，装了 extra 就用 LanceDB 向量。
  - `recall` 找相似问答，`fetch` 找相关的 schema 片段，`store` 把确认过的结果写回。
- **对我们的启示**：
  - 它的"semantic SQL + 引擎展开"和我们的"JSON IR + 编译器"是同一个思想的两种实现。它的表达力接近完整 SQL，代价是要有一个能理解 SQL 的改写引擎。
  - 它的 memory 设计"源文件进 git，索引可重建"，可以直接照搬到我们的样例库（见建议 2）。

### 2.2 supersonic（腾讯音乐，main 2026-11-06）[已核实]

路径都在 `headless/chat/src/main/java/com/tencent/supersonic/headless/chat/` 下。

- **Mapper（schema linking）**：`mapper/KeywordMapper.java`。
  - 先用 HanLP 自定义词典做匹配（`HanlpDictMatchStrategy`）。词典里**不只有指标名和维度名，还有维度的取值**，由 `DictTask` 离线从库里拉取，用来做中文分词和值链接。
  - 再按需做数据库匹配（`DatabaseMatchStrategy`）。
  - 最后用 `EmbeddingMapper` 补充语义相似匹配。
- **Parser**：`parser/llm/OnePassSCSqlGenStrategy.java`。
  - LLM 生成的是 **S2SQL**：针对语义数据集写的 SQL，列就是指标名和维度名。
  - 用**几组不同的 few-shot 样例**分别构造 prompt，并行生成，最后用 `ResponseHelper.selfConsistencyVote` 投票（self-consistency）。
- **Corrector**：`corrector/*`，规则修正器链，包括 Time、Where、GroupBy、Having、Agg、Schema、Select 等，外加 `LLMSqlCorrector`。
- **Translator**：`headless/core/translator/parser/calcite/*`，用 Calcite 把 S2SQL 翻译成物理 SQL，补 JOIN、展开指标表达式、处理比率指标（`MetricRatioParser`）。
- **对我们的启示**：
  - 它证明了"语义层 + 编译"在中文 ChatBI 里可以落地。
  - 它在三件事上比我们的 spec 多走了一步：**值词典**、**多样例 self-consistency**、**exemplar 召回**。

### 2.3 CHESS（论文 [2405.16755](https://arxiv.org/abs/2405.16755)，代码 [ShayanTalaei/CHESS](https://github.com/ShayanTalaei/CHESS)）[已核实]

- **值索引**：`src/database_utils/db_values/preprocess.py`。
  - 对每个文本列做 `SELECT DISTINCT`，跳过 id、url、email、time、phone、date、address 这类列，并按长度和基数设阈值。
  - 然后建 **MinHash LSH**。
- **检索**：`information_retriever/tool_kit/retrieve_entity.py`。
  - 从问题里抽关键词，用 LSH 召回 top 10 候选值；
  - 再用编辑距离过滤（阈值 0.3）；
  - 再用 embedding 相似度过滤（阈值 0.6）；
  - 只保留接近最高分的那些。
- **Schema Selector**：
  - 依次是 `filter_column` → `select_tables` → `select_columns`。
  - 论文的说法是：token 减少到 1/5，准确率提升约 2%。
- **注意**：CHESS 在 Spider 2.0 上只有 1.28%（Snow）和 3.84%（Lite）。它的设计是为 BIRD 那种"单库几十张表"调出来的。

### 2.4 QueryWeaver（FalkorDB，AGPL）[已核实]

- 代码在 `api/graph.py`。
- 先让 LLM 根据问题"想象"出相关表和列的描述（`find.tables`，类似 HyDE）。
- 再对每条描述分别在 Table 节点和 Column 节点上做向量 top 3。
- 然后取外键一跳邻居（`_find_tables_sphere`）。
- 最后对候选表两两之间跑 `allShortestPaths((a)-[*..6]-(b))`，把中间表补进来（`_find_connecting_tables`）。
- 结论：**这本质上就是我们的 "检索 + Join Graph Expansion"（架构 8.2 第 6 步、spec 08 A3）**。而且它只有外键边，没有基数和角色，比我们 spec 04 的人工确认边弱。没有必要为此引入图数据库。

### 2.5 MAC-SQL [已核实]

- `core/agents.py` 里的 `Selector._is_need_prune`：平均每表列数 ≤ 6 且总列数 ≤ 30 时不剪枝。
- 否则**把整库 schema 放进一个 prompt**，让 LLM 输出要保留的表和列。
- 这种做法在几百张表时直接放不进上下文，**不能当作"多表"方案**。它的 Decomposer 和 Refiner 只是普通的 CoT 加执行报错修复。

### 2.6 Vanna 2.0 [已核实]

- `src/vanna/tools/`：`run_sql`、`agent_memory`（`SaveQuestionToolArgs`、`SearchSavedCorrectToolUses`）、`visualize_data`、`python`。
- 本质上就是"工具调用 agent + 成功案例记忆"。
- 仓库已归档，只适合了解思路，不建议依赖。

---

## 3. 研究前沿：大 Schema / 企业场景（arXiv ID 全部已核实）

### 3.1 现状：基准分数说明了什么

- **Spider 2.0**（[2411.07763](https://arxiv.org/abs/2411.07763)）：企业库，常超过 1000 列，最多超过 3000 列；方言有 BigQuery 和 Snowflake。论文发布时 o1-preview 只有 17.1%。
- [Spider 2.0 排行榜](https://spider2-sql.github.io/)（2026-11-22 查看）：
  - **Snow 榜**：第一是 Genloop Sentinel Agent v2 Pro，96.70；有论文的 ReFoRCE + o3 是 62.89；**CHESS + GPT-4o 1.28，DIN-SQL + GPT-4o 0.00**。
  - **Lite 榜**：第一是腾讯 Tianqiong Data Agent，76.23；RSL-SQL + o3 33.09，LinkAlign + DeepSeek-R1 33.09。
  - 注意：榜首大多是没有论文的商业提交，Snow 榜九十多分的成绩**无法复核**。只能看出趋势：**agent 化 + 探索 + 上下文工程 ≫ 经典流水线**。
- [BIRD 排行榜](https://bird-bench.github.io/)（2026-11-22 查看）：
  - Test 第一是 AT&T AskData，83.40；有论文的 Agentar-Scale-SQL（蚂蚁，[2509.24403](https://arxiv.org/abs/2509.24403)）是 81.67；人类是 92.96。
  - BIRD 单库只有几十张表，**不代表多表难度**。
- **BEAVER**（[2409.02038](https://arxiv.org/abs/2409.02038)）：来自真实私有数仓，812 张表。GPT-5.2 加 SOTA agent 只有 **10.8%**；把所有子任务的标注作为 oracle 提示给进去，也只到 **30.1%**。
- **data.world 保险基准**（[2311.07509](https://arxiv.org/abs/2311.07509)）：GPT-4 直接对 SQL 库 16%，加上知识图谱语义层后 54%。
- **生产报告**：
  - LinkedIn SQL Bot（[2507.14372](https://arxiv.org/abs/2507.14372)）专家评审：**53%** "正确或接近正确"；
  - Uber QueryGPT（[blog](https://www.uber.com/blog/query-gpt/)）：约 300 日活，78% 的用户认为省了时间，**没有公布准确率**。
  - 所以"生产级很难落地"是事实，业界也只做到"辅助"的程度。

### 3.2 多表场景真正有效的手段

| 手段 | 代表工作（已核实） | 机制要点 | 和我们的关系 |
| --- | --- | --- | --- |
| **分层检索：域 → 表 → 列** | Uber QueryGPT 的 workspaces + Intent Agent + Table Agent（用户确认表）+ Column Prune；LinkedIn 按组织架构/访问历史 ICA 聚类，把百万级表先缩到几千；[LinkAlign 2503.18596](https://arxiv.org/abs/2503.18596)；[MDB-Link 2608.09588](https://arxiv.org/abs/2608.09588) | 先选业务域或子库，再选表，再选列；每层都可以用 LLM 重排，或者让用户确认 | 我们的 8.5 阶段 B/C 有"按业务域分区"的说法，**但 spec 05 的对象上没有 `domain` 字段** |
| **双向/多假设链接，防漏召** | [RSL-SQL 2411.00073](https://arxiv.org/abs/2411.00073)：前向+后向剪枝，strict recall 94%，列数减少 83%；[EviLink 2605.29670](https://arxiv.org/abs/2605.29670)；[CRUSH4SQL 2311.01173](https://arxiv.org/abs/2311.01173)：先让 LLM"幻想"一个最小 schema 再去检索 | 漏召比多召危险：少一个列就必错 | 我们的兜底通道只取"命中表 ≤ 8 张"，有漏召风险 |
| **上下文压缩** | [ReFoRCE 2502.00675](https://arxiv.org/abs/2502.00675)：按名字模式把同构表分组压缩；[DBCC 2606.28601](https://arxiv.org/abs/2606.28601)：重复审计列抽取、同构表模板化，Spider2-Snow 上下文从 2.6M 降到 34.7K tokens | 真实库里大量 `xxx_202401…` 分表、`created_by/updated_at` 审计列，都是噪声 | **spec 02 采集时就能识别**，成本很低 |
| **值链接/值索引** | CHESS 的 LSH；supersonic 的 HanLP 值词典；LinkedIn 对类别字段取 top-K 值；[FLINT 2610.03524](https://arxiv.org/abs/2610.03524)：金融库概念存成不透明整数键时，通用方法低于 50%，用 lookup agent 解析参照表 | 问题里的"华东区""已退款"要落到具体列和具体值 | 我们只有枚举（`enums.yaml`）和 `entity_lookup`（实时查业务库），**没有离线值索引** |
| **已验证样例记忆** | WrenAI `knowledge/sql`；Vanna memory；supersonic exemplar；Pinterest 把历史 SQL 转成"意图描述"再做 embedding（[blog](https://medium.com/pinterest-engineering/unified-context-intent-embeddings-for-scalable-text-to-sql-793635e60aac)）；FLINT 的模板库；[DAIL-SQL 2308.15363](https://arxiv.org/abs/2308.15363) 证明样例选择很重要 | 相似问题直接复用已确认的解法 | 架构 17.2 有 SQL Example Management，**但没有落到任何 spec** |
| **多候选 + 选择/一致性** | [CHASE-SQL 2410.01943](https://arxiv.org/abs/2410.01943)：多生成器 + 成对选择器；[XiYan-SQL 2411.08599](https://arxiv.org/abs/2411.08599)；[MCS-SQL 2405.07467](https://arxiv.org/abs/2405.07467)；ReFoRCE：多数投票，**不一致就"延后"并进一步探索**；supersonic self-consistency | 候选一致说明置信度高；不一致本身就是歧义信号 | 我们 spec 08 只生成单个 IR |
| **语义层 / IR + 确定性编译** | **[CoeusBI（百度）2606.15384](https://arxiv.org/abs/2606.15384)**：离线 View Generation Agent 把复杂 JOIN 固化成视图，在视图上分层检索，LLM 产出 IR 后由**方言无关的确定性 SQL 编译器**编译，已在百度生产部署；[GROUND 2608.26157](https://arxiv.org/abs/2608.26157)：受治理的语义定义加执行前校验，行级安全零违规；dbt [2026 基准](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026) | 用确定性组件保证正确性，LLM 只做意图映射 | **这就是我们的路线（spec 05/06）** |
| **长上下文代替 schema linking** | [The Death of Schema Linking? 2408.07702](https://arxiv.org/abs/2408.07702)；[Long Context NL2SQL 2501.12372](https://arxiv.org/abs/2501.12372) | schema 放得进上下文时就别剪，避免漏召 | 和我们 8.5 阶段 A 的"≤60 个对象全量放入"一致，**只适用于小规模** |
| **推理模型 / RL 微调** | [Reasoning-SQL 2503.23157](https://arxiv.org/abs/2503.23157)、[Arctic-Text2SQL-R1 2505.20315](https://arxiv.org/abs/2505.20315)、[OmniSQL 2503.02240](https://arxiv.org/abs/2503.02240)、[Alpha-SQL 2502.17248](https://arxiv.org/abs/2502.17248)（MCTS）、[ReViSQL 2603.20004](https://arxiv.org/abs/2603.20004)：在清洗过的 BIRD 上做 RLVR，宣称达到人类水平 | 提升"单库内推理"能力 | 都是在 BIRD 这种小 schema 上验证的，**对多表问题帮助有限**，也不符合我们"不训模型"的决策（架构第三章） |
| **评测方法** | [NL2SQLBench（论文配套仓库）](https://github.com/neurdb/NL2SQLBench)：按模块统计指标和 token；Uber 的 Table Overlap、Decoupled 评测；[Schema 检索基准 2607.13311](https://arxiv.org/abs/2607.13311)：现成 embedding 的 recall@10 只有 60.4，按目标库做语料自适应微调后到 75.6 | **分阶段评测**，先定位是检索错了还是生成错了 | spec 03 只评端到端执行结果 |

### 3.3 IR 和直写 SQL：证据怎么说（要诚实）

- **支持 IR 的证据**：
  - dbt 2026 基准，同一题集各跑 20 次：
    - 在语义层能表达的题上，语义层是 **100%**；直写 SQL 是 Sonnet 4.6 **62.5%**、GPT-5.3 Codex **51.2%**。
    - 语义层答不了的时候会**明确报错**，不会给出一个看起来合理的错数。
  - CoeusBI 是百度的生产系统，也用 IR + 确定性编译器。
  - GROUND 说明，光有准确的指标定义而没有执行前的访问策略，仍然会泄露数据。这和我们 07 网关的设计一致。
  - NatSQL（[2109.05153](https://arxiv.org/abs/2109.05153)）和 IRNet/SemQL（[1905.08205](https://arxiv.org/abs/1905.08205)）是 LLM 之前时代 IR 有效的证据，现在参考意义有限。
- **反面证据和风险**：
  - **覆盖率**：dbt 基准里 3/11 的题因为 "too many hops" 超出语义层的表达能力，语义层在这些题上一直是 **0%**；直写 SQL 在这些题上反而有 70%~100%。补 3 个模型之后，语义层到了 98.2%，直写 SQL 也到了 90%。
  - 这说明：**IR 路线的上限取决于建模的覆盖面，而建模成本会随表的数量线性增长**。
  - BIRD 和 Spider 2.0 的榜首**全部是直写 SQL 加 agent**。不过这些基准不考察治理和口径一致性，不能直接比较。
  - 目前**没有**在大 schema 企业库上，对"JSON IR 与直写 SQL"做严格同条件对比的公开研究。这一点要承认。
- **我的判断**：
  - IR 作为主通道，用于有治理要求的指标问题，是对的。
  - 但"表多"的场景下，大量长尾问题会落到**兜底通道**，而我们的兜底通道现在是最弱的一环：检索靠子串匹配、最多 8 张表、单次生成、没有样例。
  - 另外，语义层要靠人工写 YAML，几百张表时冷启动会成为瓶颈。CoeusBI 的离线 View Agent、dbt 的"LLM 提议最少建模"、Pinterest/LinkedIn 的 AI 文档生成，都是在解决这个问题。

---

## 4. 逐个 spec 对照

### Spec 02 Metadata

- **已对齐**：
  - 不可变快照、release 指针、STALE 标记，相当于 LinkedIn "按 DataHub 下线废弃表" 和 Wren "索引可重建" 的严格版本。
  - 注释注入检测，对应 GROUND 的安全思路。
- **缺失**：
  - (a) **表族识别**：同构分表，以及同一前缀加日期或数字后缀的表；
  - (b) **重复审计列识别**：`created_at/updated_at/is_deleted/tenant_id` 这类；
  - (c) **值画像**：低基数文本列的 distinct 值，已推迟到阶段 2；
  - (d) 行数估计已有，可以复用到检索排序里（LinkedIn 用的是访问热度）。
- **建议**：
  - (a)(b) 都是**纯采集后计算，不需要访问数据**，可以在 plan 02 里加一个小 Task：在快照上派生 `table_family` 和 `common_columns`，存进快照的派生表或视图，不影响内容哈希。
  - (c) 见建议 3。

### Spec 03 Eval

- **已对齐**：按执行结果比对；合成库刻意埋了陷阱（扇出、多路径、重名等）；50 题分类配比。
- **缺失**：
  - (a) **分阶段指标**：目录召回 recall@K（指标、维度、表、列）、IR 字段级正确率、兜底占比与兜底准确率、token 和调用次数（参考 NL2SQLBench、Uber Table Overlap）；
  - (b) **规模压力**：合成库只有 13 张表，**完全测不到"表多了"的问题**。
- **建议**（**优先级 1**）：
  - 加一个"干扰扩展"模式：生成器按种子额外生成 N 张**语义上相近的干扰表**（比如 `t_work_order_bak`、`t_ticket_archive_2023`、同名字段、相似注释），以及 M 个干扰指标和维度。用同一批题测 N = 0/100/500 时的召回和准确率曲线。
  - 这是唯一能**用数据回答"表多了行不行"**的办法，也是决定是否进入检索阶段 B 的依据。架构 8.5 本来就要求"以 Recall@K 实测决定"，但现在没有测量手段。
  - 可选：阶段 2 用 [BIRD Mini-Dev 的 MySQL 版本](https://bird-bench.github.io/) 做外部对照，规模仍然小；或用 LiveSQLBench-Large（约 1K 列）。

### Spec 04 Join Graph

- **已对齐**：人工确认边、`join_role`、基数证据、扇出和 chasm 判定、语义歧义不按分数选。比 QueryWeaver（只有外键）和多数开源项目都严格。CoeusBI 也把"JOIN 太多"列为准确率的头号杀手，说明把 JOIN 从 LLM 手里拿走是对的。
- **缺失**：
  - 候选边只来自外键（4.3）。真实 MySQL 业务库**外键覆盖率往往很低**（架构 1.3 还没填），几百张表时人工手写边不现实。
  - 路径枚举用的是"≤ 4 步的全部简单路径"，前提是表的规模不超过 100。
- **建议**（**优先级 5 的一部分**）：
  - 现在就把 `evidence` 的枚举预留出 `NAME_MATCH`、`VALUE_INCLUSION`、`QUERY_LOG` 三种（只能产生 CANDIDATE 边）。实现放到阶段 2：
    - 字段命名匹配，如 `xxx_id → t_xxx.id`；
    - 采样值包含检验；
    - 解析历史 SQL 的 JOIN（LinkedIn 从查询日志统计"常见 JOIN"）。
  - 在 7 节风险里写明：表数 > 100 时，路径查找要改成"以检索命中表为终点的 Steiner 树近似"，也就是 QueryWeaver 的两两最短路径合并。

### Spec 05 Semantic

- **已对齐**：
  - 指标只能来自 YAML 并走 PR 审核；
  - `RATIO_TIME_MISMATCH` 在加载期就做硬校验；
  - `terms.yaml`、`enums.yaml`、默认口径。
  - 这些和 dbt/MetricFlow、WrenAI MDL、supersonic 的思路一致，而且口径校验更严格。
- **缺失**：
  - (a) 没有 `domain` 或业务域字段，阶段 B/C 无法按域分区检索（Uber workspaces、LinkedIn 聚类）；
  - (b) 没有**已验证样例**这种对象（架构 17.2）；
  - (c) 列注解里没有"可做值索引"的开关；
  - (d) 冷启动完全靠人工。
- **建议**：
  - (a) 所有对象加可选字段 `domain: str`，几乎零成本。
  - (b) 见建议 2。
  - (c) `columns.yaml` 加 `value_index: true|false`，默认按规则推断：低基数文本列、非敏感、非 ID。
  - (d) 阶段 2：`semantic draft` 命令让 LLM 根据快照、注释、历史 SQL **生成 DRAFT 指标和维度**，仍然要经过 REVIEWING→VERIFIED。CoeusBI 的 View Generation Agent 和 dbt "LLM 只建最少的模型" 是先例。

### Spec 06 IR Compiler

- **已对齐**：确定性规划和编译、`assumptions`、兜底判定、版本能力矩阵。CoeusBI 和 dbt 的证据支持这条路线。
- **风险（要写进 7 节）**：
  - IR 的表达力决定了能覆盖多少问题。dbt 基准显示，超出建模范围的题是 0 分。
  - 架构 9.1.1 说"用 200 道真实问题反推 IR"，但 spec 03 只有 50 道合成题。
- **建议**：
  - 在第 3 周编译器开工前，把 50 道验收题逐题标注"IR 是否能表达"，统计覆盖率，作为 IR v2 字段取舍的依据。
  - 在 7 节记录一个**备选方案**：阶段 2 可以考虑 "semantic SQL"，也就是 WrenAI/supersonic 那样让 LLM 在语义视图上写 SQL，再由 SQLGlot 改写和展开。它能覆盖窗口、嵌套等 IR 难以表达的形态，可以作为兜底通道的强化版。**现在不做**。

### Spec 07 Gateway

- **已对齐**：数据库层隔离作为主防线、AST 白名单、边校验、扇出校验、成本检查、错误净化、绕过测试集。GROUND 的结论（访问策略必须在执行前强制执行，语义定义本身替代不了）支持这一设计。
- **缺失**：不算缺失，只是需要说明。ReFoRCE 和 Spider-Agent 一类 agent 会执行"探索性查询"（看列的取值、试跑子查询）。我们的网关只允许最终 SQL 执行。
- **建议**：**保持不变**。如果以后兜底通道需要探索，只开放"对白名单列做 `SELECT DISTINCT … LIMIT k`"这一种受控工具，同样走审计。值索引（建议 3）可以离线解决大部分探索需求。

### Spec 08 Agent

- **已对齐**：
  - LangGraph 工作流；LLM 只产出 IR；
  - 规则化的歧义判定，每轮只问一次；
  - 兜底通道全量校验；有限修复；
  - 结果校验和口径标注；数据区隔离防注入。
  - 澄清机制和 BIRD-Interact 的发现一致：交互能显著提升可靠性。
- **缺失**：
  - (a) A3 检索是**中文不分词的子串匹配**，上限是指标 15、维度 15、表 8。对象超过 60 个后没有任何语义召回，也没有召回率监控。
  - (b) 没有样例召回。
  - (c) A5 实体解析每次都实时查业务库，而且只支持 exact/contains，不能处理"华东精密"写成"华东精密公司"这类变体。
  - (d) 只生成单个 IR，没有置信度信号。
  - (e) 兜底通道检索太弱。
- **建议**：见下面的建议 2、3、4。另外，在 A3 的 `RetrievalResult` 里记录每个对象的命中来源和分数，供 spec 03 计算召回率。

---

## 5. 优先级最高的 5 处 spec 修改

> 原则：先能"测"，再改"做"；每一项都不推翻现有架构，只加组件或钩子。

### 建议 1（P0）：spec 03 加分阶段指标和"干扰表规模压力测试"

- **修改位置**：
  - spec 03 §4.2：E2 生成器加 `--distractors N`；
  - §4.7/§4.8：Runner 记录各阶段的产物；
  - §4.9：报告增加 catalog recall@K、IR 字段准确率、fallback_ratio、tokens。
  - spec 08 §4.4：`RetrievalResult` 加 `scores`。
- **理由**：
  - 用户的担心是"表多了就不行"，但我们目前的 13 张表测不出来。
  - Uber、NL2SQLBench、[2607.13311](https://arxiv.org/abs/2607.13311) 都把检索当作一等公民来单独评测。
  - 架构 8.5 要求"以 Recall@K 实测决定是否升级"，现在没有测量手段。
- **成本**：低，只涉及生成器和报告字段。

### 建议 2（P1）：spec 05 和 08 加"已验证问答样例库"，存 Q→IR

- **修改位置**：
  - spec 05 新增 S9：`semantic_definitions/<model>/examples/*.yaml`，字段为 `question`、`ir`、`status`、`owner/reviewer`、`depends_on`（指标、维度、边）。加载时用 IR 规划器校验，依赖对象 STALE 或 DEPRECATED 时自动失效（对应架构 17.2 的四步）。
  - spec 08 A3 召回 top-k（MVP 用关键词重叠，阶段 B 用向量），A4 把召回结果作为 few-shot 放进 prompt。
  - spec 09 加一个入口：用户确认的答案可以"提交为样例候选（DRAFT）"。
- **理由**：
  - WrenAI memory、Vanna、supersonic exemplar、Pinterest、FLINT 模板库都把它当作核心资产，DAIL-SQL 也证明了样例选择对效果的影响。
  - **存 IR 而不是存 SQL**：IR 和物理 schema 解耦，表改名或重构后不用重写，可以直接复用我们的 STALE 机制，这一点比 Wren/Vanna 存 SQL 更好。
  - 样例同时也能作为评测题的来源。

### 建议 3（P1）：值索引（Value Index）

- **修改位置**：
  - spec 02 新增 M9（或放进 spec 05/08 的一个小任务）：对 `value_index` 列（规则：文本类型、非敏感、非 ID、distinct ≤ 阈值，比如 5000）离线采集 distinct 值，存入 PG 并带 `release_id`。中文用 **PG `pg_trgm` 三元组相似度**，或者 n-gram 加编辑距离，**不需要引入新组件**。
  - spec 08 A3 用它把问题中的字面量链接到"列 = 值"；A5 实体解析先查索引，再按需查业务库。
- **理由**：
  - CHESS（LSH + 编辑距离 + embedding）、supersonic（HanLP 值词典）、LinkedIn（类别字段 top-K 值）、FLINT（不透明 ID 时准确率低于 50%）都表明，值链接是多表企业库的主要错误来源之一。
- **合规**：值属于数据，进入模型要遵守 11.6。只把**命中的少量值**放进上下文，敏感列不建索引。

### 建议 4（P2）：spec 08 A4 加"多候选 IR + 一致性"

- **修改位置**：
  - spec 08 §4.5/§4.7：配置 `understanding_samples`（MVP 默认 1，评测时开到 3）。
  - 用不同的样例子集或温度生成 k 个 IR → 分别规划 → 比较**规划后 IR 的规范化哈希**（或编译后 SQL 的哈希）。
  - 多数一致就执行；分歧集中在某个字段（指标、维度角色、过滤值）时，**把分歧本身转成一个 ambiguity 项**进入现有的 A6 澄清机制；没有可解释的分歧就按多数执行，并标注"置信度低"。
- **理由**：
  - supersonic 的 self-consistency、ReFoRCE 的"不一致则延后"、CHASE/XiYan 的多候选选择，都是 BIRD 和 Spider 2.0 上被反复验证的有效手段。
  - 我们的规划和编译是确定性的，**比较 k 个候选不需要访问数据库**，成本只是 k 倍的 LLM 调用。
  - 它还给 9.6 的"影响大小"判定提供了廉价信号（spec 08 现在把影响一律当作 HIGH）。

### 建议 5（P2，主要是预留钩子）：为"规模化"在 02/04/05 预留扩展点

- **修改位置**：
  - spec 02：派生 `table_family`（同构分表）和 `common_columns`（审计列），检索时把同一族的表折叠成一项（ReFoRCE 的表分组、DBCC 的同构模板化）。
  - spec 05：所有对象加 `domain`（Uber workspaces、LinkAlign/MDB-Link 的分层）。
  - spec 04：`evidence` 预留 `NAME_MATCH/VALUE_INCLUSION/QUERY_LOG`，只能生成 CANDIDATE 边。
  - spec 05 §2.2：写明阶段 2 做 LLM 起草 DRAFT 语义对象（CoeusBI、dbt 2026）。
- **理由**：
  - "表多"的场景下，真正的瓶颈是**建模和确认的人工成本**，以及**检索噪声**，而不是 SQL 生成。
  - 现在只加字段和枚举，几乎没有成本；等到真的上规模时，就不需要做 schema 迁移。

### 明确**不建议**采用的

| 不做 | 原因 |
| --- | --- |
| 微调/RL 模型（SQLCoder、OmniSQL、Arctic-R1、Reasoning-SQL、ReViSQL） | 都是在 BIRD 小 schema 上的收益；需要训练数据和 GPU；与架构第三章"第一阶段不训模型"冲突；SQLCoder 已停更 |
| 大规模 test-time scaling（Agentar-Scale-SQL、CHASE 几十个候选 + 选择模型） | 成本和延迟不适合交互式产品；建议 4 的 k=3 已经拿到大部分收益 [这是推断，未实测] |
| MAC-SQL 式"整库 schema 交给 LLM 剪枝" | 几百张表时放不进上下文（源码已确认它把整库放进一个 prompt） |
| 无约束的 agent 自由探索（Text-2-SQL-Agent 的单 `execute_sql` 循环、Spider-Agent） | 违反 spec 07 的安全边界，生产库上有成本和泄露风险；我们的目标是可治理，不是刷榜 |
| 引入图数据库（QueryWeaver/FalkorDB）或知识图谱存储 | NetworkX + PG 已经够用；QueryWeaver 是 AGPL；它的检索逻辑可以用 PG 加最短路径实现 |
| CHESS 的 LLM 单元测试（Unit Tester） | 我们 IR 路径的 SQL 是确定性编译出来的，正确性靠规划器和快照测试；LLM 生成单测的收益低、成本高 |
| 照抄 tianyuqic1/Text2Sql | 0 star、只有 1 个 commit、没有许可证，设计不比我们的 spec 更完整 |

---

## 6. 推荐阅读顺序（最多 5 个，每个都写明看哪里）

1. **dbt《Semantic Layer vs. Text-to-SQL: 2026 Benchmark Update》**：<https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026>（数据和代码在 [dbt-labs/dbt-llm-sl-bench](https://github.com/dbt-labs/dbt-llm-sl-bench)）
   - 看什么：表格 "Too Many Hops True/False"，以及"补 3 个模型"那一节。
   - 为什么：15 分钟就能理解我们为什么走 IR、IR 的天花板在哪里，以及为什么必须有兜底通道。
2. **supersonic**：<https://github.com/tencentmusic/supersonic>，目录 `headless/chat/src/main/java/com/tencent/supersonic/headless/chat/`
   - `mapper/KeywordMapper.java` → `HanlpDictMatchStrategy.java`：中文分词加值词典链接（对应建议 3）；
   - `parser/llm/OnePassSCSqlGenStrategy.java`：多样例 self-consistency 投票（对应建议 2、4）；
   - `corrector/`：规则修正器链；
   - `headless/core/translator/parser/calcite/`：语义 SQL 到物理 SQL 的翻译（和我们 spec 06 对照着看）。
   - 为什么：它和我们一样是"中文 + 语义层 + 编译"，而且是在生产环境用过的代码。
3. **CHESS 论文 + 代码**：<https://arxiv.org/abs/2405.16755>、<https://github.com/ShayanTalaei/CHESS>
   - `src/database_utils/db_values/preprocess.py`：怎么挑选要建索引的列，以及 MinHash LSH；
   - `src/workflow/agents/information_retriever/tool_kit/retrieve_entity.py`：LSH → 编辑距离 → embedding 的三级过滤；
   - `src/workflow/agents/schema_selector/tool_kit/`：剪枝。
   - 为什么：值索引和 schema 剪枝最清晰的参考实现。读的时候记住，它在 Spider 2.0 上只有 1%，所以只借鉴组件，不借鉴整体。
4. **ReFoRCE 论文**：<https://arxiv.org/abs/2502.00675>（代码 [Snowflake-Labs/ReFoRCE](https://github.com/Snowflake-Labs/ReFoRCE)）
   - 看什么：database information compression（按模式给表分组）和 consensus enforcement（投票不一致就延后）两节。
   - 为什么：这是 Spider 2.0（上千列）上有论文、可以复现的方法。对应建议 4 和建议 5 的表族识别。
5. **WrenAI v5 文档 + memory 模块**：<https://github.com/Canner/WrenAI>
   - `docs/core/concepts/correctness.md`、`memory_system.md`；
   - `core/wren/src/wren/memory/`（`store.py`、`schema_indexer.py`）。
   - 为什么：看"语义契约 + dry-plan 展开 + 样例记忆，源文件进 git、索引可重建"这一整套治理闭环，对应建议 2。
   - 想看旧版 GenBI 流水线，就切到 `legacy/v1` 分支。

备选：
- **CoeusBI**（<https://arxiv.org/abs/2606.15384>）：工业界"IR + 确定性编译器"的最新论文，和我们的路线最接近。只读了摘要，正文未读 **[未核实细节]**。
- **Uber QueryGPT 博客**：工程演进过程写得最具体。

---

## 7. 未核实 / 局限

- Spider 2.0 和 BIRD 榜首的商业提交（Genloop、AT&T AskData 等）大多没有论文，分数无法复核，只能作为趋势参考。
- CoeusBI、GROUND、EviLink、MDB-Link、DBCC、FLINT、ReViSQL 这些 2026 年的论文**只读了 arXiv 摘要**，没有读正文和代码。文中引用的数字都来自摘要。
- dbt 2026 基准只有 11 题，每题跑 20 次，样本小；数字来自 dbt 官方博客。
- 建议 4 中"k=3 能拿到大部分收益"是推断，没有实测，需要用建议 1 的评测来验证。
- WrenAI 的 Rust 引擎（`core/wren-core`）只看了目录结构，没有读 dry-plan 的实现细节。
- Codex 清单里的 `https://docs.getwren.ai/oss/reference/architecture` 链接没有打开核实。
- 这次没有深入读 DB-GPT 和 Chat2DB 的源码，只核对了元数据。
