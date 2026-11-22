# 难题 02：大规模 Schema 与表检索（"表一多就不行"）

- 日期：2026-11-22（北京时间）
- 对照的仓库版本：kele0808/text2sql `8090297`
- 本篇只整理已经写在 [01-text2sql-sota.md](01-text2sql-sota.md) 和 `docs/architecture.md`（修订说明第 9 条、8.5、9.6、第二十四章）里的结论，不新增调研；数字和来源都出自那两份文档，核实状态以 01 的标注为准。

## 1. 问题是什么

库里有几十张表时，把整库的精简结构塞进提示词，LLM 基本能选对表。表到了几百、上千张，同时出现大量分表、备份表和重复的审计列以后：

- 选表这一步先错了（漏掉一张必需的表，或被名字相近的表干扰），后面的 SQL 生成再强也没用；
- 问题里的业务值（"华东区""已退款"）对不上列和原值（见 [problem-01](problem-01-大表值采集与值链接.md)）；
- 同一个问题多问几次，理解结果不一样。

## 2. 为什么难（来自 01 §0、§3.1）

- **基准上的硬数据**：
  - Spider 2.0-Snow 上，在 BIRD 上很强的经典流水线 CHESS + GPT-4o 只有 1.28%，DIN-SQL + GPT-4o 是 0.00%（[2411.07763](https://arxiv.org/abs/2411.07763)，[排行榜](https://spider2-sql.github.io/)）。
  - 企业数仓基准 BEAVER（812 张表）上，GPT-5.2 加 SOTA agent 只有 10.8%，给足子任务 oracle 提示也只到 30.1%（[2409.02038](https://arxiv.org/abs/2409.02038)）。
  - BIRD 单库只有几十张表，不代表多表难度。
- **生产报告也只做到"辅助"**：LinkedIn SQL Bot 专家评审 53% "正确或接近正确"（[2507.14372](https://arxiv.org/abs/2507.14372)）；Uber QueryGPT 没有公布准确率（[blog](https://www.uber.com/blog/query-gpt/)）。
- **漏召比多召危险**：少召回一列就必错（01 §3.2，RSL-SQL 等）。
- **噪声**：真实库里大量 `xxx_202401…` 分表和 `created_by/updated_at` 审计列（01 §3.2，ReFoRCE、DBCC）。
- **IR 路线的覆盖率代价**：dbt 2026 基准里，语义层能表达的题 100% 正确，但 "too many hops" 的题一直 0%；建模成本随表数线性增长（01 §3.3）。

## 3. 工业界怎么解决（01 §2、§3.2）

- **分层检索 域 → 表 → 列**：Uber QueryGPT 的 workspaces + Intent Agent + Table Agent（用户确认表）+ Column Prune；LinkedIn 按组织架构 / 访问历史聚类，把百万级表先缩到几千。
- **值词典 / 值索引**：supersonic 把维度取值放进 HanLP 词典；LinkedIn 对类别字段取 top-K 值。
- **已验证样例记忆**：WrenAI SQL pairs、Vanna memory、supersonic exemplar、Pinterest 把历史 SQL 转成意图描述再做 embedding。
- **多样例 self-consistency**：supersonic 用几组不同 few-shot 并行生成后投票。
- **语义层 / IR + 确定性编译**：百度 CoeusBI（[2606.15384](https://arxiv.org/abs/2606.15384)）离线把复杂 JOIN 固化成视图，LLM 出 IR，方言无关的确定性编译器出 SQL，已在生产部署；dbt 2026 基准。

## 4. 科研界怎么解决（01 §3.2）

- **双向 / 多假设链接防漏召**：RSL-SQL（[2411.00073](https://arxiv.org/abs/2411.00073)，strict recall 94%，列数减少 83%）、CRUSH4SQL（[2311.01173](https://arxiv.org/abs/2311.01173)）。
- **分层 / 多库链接**：LinkAlign（[2503.18596](https://arxiv.org/abs/2503.18596)）、MDB-Link（[2608.09588](https://arxiv.org/abs/2608.09588)）。
- **上下文压缩**：ReFoRCE（[2502.00675](https://arxiv.org/abs/2502.00675)）按名字模式把同构表分组；DBCC（[2606.28601](https://arxiv.org/abs/2606.28601)）抽取重复审计列、同构表模板化，Spider2-Snow 上下文从 2.6M 降到 34.7K tokens。
- **样例选择**：DAIL-SQL（[2308.15363](https://arxiv.org/abs/2308.15363)）。
- **多候选 + 选择 / 一致性**：CHASE-SQL（[2410.01943](https://arxiv.org/abs/2410.01943)）、XiYan-SQL（[2411.08599](https://arxiv.org/abs/2411.08599)）、MCS-SQL（[2405.07467](https://arxiv.org/abs/2405.07467)）；ReFoRCE 不一致就延后并继续探索。
- **长上下文代替 schema linking**：[2408.07702](https://arxiv.org/abs/2408.07702)、[2501.12372](https://arxiv.org/abs/2501.12372)——放得进上下文时就别剪，只适用于小规模。
- **分阶段评测**：NL2SQLBench、Schema 检索基准 [2607.13311](https://arxiv.org/abs/2607.13311)（现成 embedding recall@10 只有 60.4）。

## 5. 我们怎么解决

| 手段 | 做法 | 位置 |
| --- | --- | --- |
| 分阶段检索 | 阶段 A（MVP，约 100 张表以内）：整库精简摘要 + 关键词 / 同义词 + 术语映射，LLM 在摘要里选表再展开字段；B：加 PG FTS + pgvector 双通道、RRF 融合；C：加 Reranker、按业务域分区 | architecture 8.5 |
| 阶段升级由数据触发 | 满足任一条件启动 B：Spec 03 E10 干扰表压测 N = 500 时 `table_recall_at_8` < 0.90，或 `execution_accuracy` 比 N = 0 下降超过 5 个百分点；真实库 `failure_stage = RETRIEVAL` 占失败的三分之一以上；表数超过 300 | architecture 8.5 修订 |
| 表族折叠 + 公共列 | 一级摘要按表族折叠分表 / 备份表，审计列只列一次 | spec 02 M9（§4.10）；architecture 8.5 修订 |
| 值索引 | 离线 distinct 值，按租户存，`pg_trgm` 精确 + 模糊；实体解析先查索引再经网关确认 | spec 02 M10（§4.11）；[problem-01](problem-01-大表值采集与值链接.md) |
| 已验证样例 | 存"问题 → IR"，PR 审核，依赖对象 STALE 时自动不可用；召回作 few-shot（默认 3 个） | spec 05 S9；spec 08 A3 |
| 多候选 IR 投票 | 理解阶段生成 3 个 IR 候选，确定性规划后按规范化哈希投票；多数一致就执行，分歧转澄清 | spec 08 §4.5；architecture 9.6 修订 |
| 业务域 | 所有语义对象可选 `domain`，为阶段 B/C 的按域检索预留 | spec 05 S10 |
| 分阶段评测 + 干扰表压测 | catalog recall@K、IR 字段准确率、fallback_ratio、tokens；干扰表 0 / 100 / 500 | spec 03 E9 / E10 |
| 推断关系 | `evidence` 预留 `NAME_MATCH/VALUE_INCLUSION/QUERY_LOG`，只生成 CANDIDATE 边 | spec 04 |

## 6. 为什么这样解决 / 不采纳什么

- **先能测，再改做**（01 §5 原则）：没有干扰表压测和分阶段指标，就无法判断"表多了到底哪里坏"，也没法决定何时升级检索阶段。
- **存 IR 而不是 SQL**：IR 和物理 schema 解耦，表改名或重构后不用重写，可以直接复用 STALE 机制（01 §5 建议 2）。
- **k = 3 投票**：规划和编译是确定性的，比较候选不访问数据库，成本只是 k 倍 LLM 调用；分歧本身是歧义信号（01 §5 建议 4）。"k=3 拿到大部分收益"是推断，未实测。
- **不引入 Embedding 也先把阶段 A 做强**：值索引、样例、表族折叠都不需要新组件（architecture 8.5 修订）。
- **不采纳**（01 §5、architecture 第二十四章）：
  - 微调 / RL 模型（收益来自 BIRD 小 schema，需要训练数据和 GPU，和"第一阶段不训模型"冲突）；
  - 大规模 test-time scaling（成本和延迟不适合交互式产品）；
  - MAC-SQL 式整库交给 LLM 剪枝（几百张表放不进上下文）；
  - 无约束 agent 自由探索（违反 spec 07 安全边界）；
  - 图数据库 / 知识图谱存储（NetworkX + PG 够用，QueryWeaver 是 AGPL）；
  - CHESS 的 LLM 单元测试（IR 路径的 SQL 是确定性编译的）。

## 7. 未验证项（01 §3.3、§5、§7）

- 没有在大 schema 企业库上对"JSON IR 与直写 SQL"做过严格同条件对比的公开研究。
- Spider 2.0 榜首多是无论文的商业提交，九十多分的成绩无法复核。
- k = 3 投票的收益是推断。
- 兜底通道（子串匹配、最多 8 张表、单次生成）是目前最弱的一环，长尾问题会落到这里；效果要等 Spec 03 E10 压测出数据。
- 语义层冷启动成本：几百张表时人工写 YAML 会成为瓶颈，LLM 起草 DRAFT 语义对象放在阶段 2（spec 05 §2.2）。
