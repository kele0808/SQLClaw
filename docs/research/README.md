# 调研笔记

本目录存放设计调研：业界开源实现、生产报告和论文的读后整理，以及对本项目 spec 的建议。

- **不是规范。** 以 `docs/architecture.md` 和 `docs/specs/` 为准；调研里的建议只有写进 architecture / spec 后才生效。
- 每篇写明调研日期、对照的仓库版本、核实方式（**[已核实]** / **[未核实]**）。
- 文件名按序号递增，旧笔记不改结论，过时的在开头标注「已过时，见 NN」。

| 文件 | 日期 | 主题 | 落地情况 |
| --- | --- | --- | --- |
| [01-text2sql-sota.md](01-text2sql-sota.md) | 2026-11-22 | 多表 / 大 Schema 场景的 Text2SQL 前沿（WrenAI、supersonic、CHESS、Uber QueryGPT、LinkedIn SQL Bot、Spider 2.0、BEAVER 等），以及对 spec 02–08 的修改建议 | 5 条建议全部采纳：architecture 修订说明第 9 条、24 章「调研采纳与不采纳」；spec 02/03/04/05/06/08/09 的 2026-11-22 修订 |
| [02-value-index-large-tables.md](02-value-index-large-tables.md) | 2026-11-22 | 值索引在大表上怎么采：CHESS、CodeS、supersonic、Databricks Genie、Snowflake Cortex Analyst、SQLBot 对比；MySQL 松散索引扫描与本机 5.7 / 8.0 EXPLAIN 实测 | 采纳：spec 02 §4.11「大表索引路径」（`BIG_TABLE_NO_INDEX`、`NO_LOOSE_SCAN`，验收 17、18）、spec 05 §4.1 `values`；plan 02 Task 14、15 |
| [03-architecture-baseline-review.md](03-architecture-baseline-review.md) | 2026-11-24 | 用户提供的《SQLClaw Architecture Baseline》v1.0.2 / v1.0.3 审阅：结构摘要、§16 已知问题逐条核对、逐条采纳表、文档自身的技术问题、引用核实 | 部分采纳：新增 spec 00；spec 01/03/07/08/09 与 architecture 修订说明第 10 条的 2026-11-24 修订；plan 02 Task 17 前置提醒。待定：兜底通道去留（spec 08 顶部） |
| [04-architecture-baseline-v1.0.3.md](04-architecture-baseline-v1.0.3.md) | 2026-11-24 | 《SQLClaw Architecture Baseline》v1.0.3 原文存档（目标架构参考，非现行规格）；开头附 §0–§20 章节对照表和原文已知问题 | 不直接生效；采纳结论见 03 |

## 难题

`problem-NN-主题.md` 是**难题笔记**：一篇只讲一个工业级 NL2SQL / Text2SQL 落地难题，把散在调研笔记、architecture 和 spec 里的结论按同一个结构串起来，方便回头查"这个问题我们为什么这样做"。和调研笔记一样**不是规范**，以 architecture / spec 为准；新事实先写进调研笔记，难题笔记只引用、不另起结论。

模板：

```markdown
# 难题 NN：<主题>

- 日期 / 对照的仓库版本 / 相关调研笔记

## 1. 问题是什么        <!-- 用一个具体例子说明现象 -->
## 2. 为什么难          <!-- 约束和数据，带来源 -->
## 3. 工业界怎么解决    <!-- 产品 / 开源项目，带来源和核实标注 -->
## 4. 科研界怎么解决    <!-- 论文，带 arXiv 链接 -->
## 5. 我们怎么解决      <!-- 链接 spec 章节和 plan 任务 -->
## 6. 为什么这样解决 / 不采纳什么
## 7. 未验证项
```

| 文件 | 日期 | 难题 | 主要依据 |
| --- | --- | --- | --- |
| [problem-01-大表值采集与值链接.md](problem-01-大表值采集与值链接.md) | 2026-11-22 | 业务说法对不上库里原值（"华东精密" vs "华东精密有限公司"，0 行且不报错）；只读 MySQL 上大表怎么采值 | 调研 02、01 §3.2；spec 02 §4.11、spec 05 §4.1 |
| [problem-02-大规模schema与表检索.md](problem-02-大规模schema与表检索.md) | 2026-11-22 | 表一多就选错表：分层检索、表族折叠、值索引、已验证样例、k=3 IR 投票、数据触发的阶段升级 | 调研 01；architecture 8.5、9.6、第二十四章 |
