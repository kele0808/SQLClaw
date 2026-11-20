# Text2SQL Agent

一个面向生产环境的 Text2SQL Agent：用户用自然语言提问，系统理解业务语义、规划查询、生成并校验 SQL，在受控环境中安全执行，并返回可追溯的结果。

> **当前状态：设计阶段。** 仓库里目前只有架构文档，代码将按下面的 6 周 MVP 计划逐步加入。

## 核心思路

不让 LLM 直接"猜" SQL，而是由元数据和语义层驱动：

- **Metadata Infrastructure**：采集表、字段、索引、主键/唯一键/外键，以不可变快照 + 发布指针管理版本。
- **Schema Evolution**：检测 Schema 变化，标记受影响的语义和 Join 关系，原子发布、可回滚。
- **Join Intelligence**：Join Graph + 基数分析，自动发现关联路径并检测扇出（多对一关系需要 PK/UK 佐证或人工确认）。
- **Semantic Layer**：指标、维度、业务术语统一定义（YAML + PR 审核），不允许 LLM 临时发明口径。
- **Query Planner**：LLM 先产出结构化的 Query IR，再由编译器生成 SQL；IR 覆盖不了的问题走"LLM 直写 SQL + 强校验"的兜底通道，并统计 `fallback_ratio`。
- **SQL Validation**：基于 SQLGlot 的 AST 校验，加上语义、权限、成本校验；按 MySQL 5.7 / 8.0 版本区分方言能力。
- **Secure Execution Gateway**：只读副本 + 租户视图/账号作为第一道防线，AST 改写作为第二道防线；禁止 `EXPLAIN ANALYZE`，字段脱敏，全量审计，配套绕过测试集。
- **Evaluation**：从第 1 周起建设 50 题验收集，按执行结果比对评测，自动回归。

## 技术栈

| 层 | 选型 |
| --- | --- |
| Agent 编排 | LangGraph |
| API | FastAPI |
| SQL 解析与校验 | SQLGlot |
| 系统元数据 / 向量 / 任务队列 | PostgreSQL + pgvector |
| 业务数据库（MVP） | MySQL（只读副本） |

## 路线图

**6 周 MVP（1～2 名开发）**

| 周 | 主线 | 交付 |
| --- | --- | --- |
| 1 | 工程骨架、MySQL 元数据采集、Schema 快照、`trace_id` 日志 | 元数据快照可查询；目标库画像与分工表填完 |
| 2 | Join Graph、候选边生成与人工确认、Join Path Finder + 扇出检测 | 验收题都能给出 Join 路径 |
| 3 | 语义层、Query IR v2、版本感知的 SQL Compiler | IR → SQL 端到端可用，评测出第一版分数 |
| 4 | Validator、Execution Gateway、绕过测试集进 CI | 安全执行链路可用 |
| 5 | LangGraph 全链路、澄清策略、兜底通道 | 50 题全链路评测 |
| 6 | 基础多轮对话、手动 Schema Diff、发布与回滚、最小 API 和页面 | MVP 验收 |

MVP 之后分三个阶段：稳定与扩展（第 7～10 周）、规模化（第 11～16 周）、平台化（第 16 周以后）。详见架构文档第二十三章。

## 文档

- [架构方案（修订版）](docs/architecture.md)：当前以此为准，含审阅修订说明。
- [原始方案](docs/prd.md)：修订前的原文，保留作对照。

## 规划中的目录结构

```
apps/        # api、worker、frontend
src/         # agent、metadata、graph、semantic、planner、sql、execution、security、evaluation ...
prompts/     # 各阶段 Prompt
tests/       # unit、integration、security（绕过测试集）、regression、evaluation
docs/        # 架构与设计文档
```

完整结构见架构文档第十八章。
