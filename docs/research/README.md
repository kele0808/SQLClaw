# 调研笔记

本目录存放设计调研：业界开源实现、生产报告和论文的读后整理，以及对本项目 spec 的建议。

- **不是规范。** 以 `docs/architecture.md` 和 `docs/specs/` 为准；调研里的建议只有写进 architecture / spec 后才生效。
- 每篇写明调研日期、对照的仓库版本、核实方式（**[已核实]** / **[未核实]**）。
- 文件名按序号递增，旧笔记不改结论，过时的在开头标注「已过时，见 NN」。

| 文件 | 日期 | 主题 | 落地情况 |
| --- | --- | --- | --- |
| [01-text2sql-sota.md](01-text2sql-sota.md) | 2026-11-22 | 多表 / 大 Schema 场景的 Text2SQL 前沿（WrenAI、supersonic、CHESS、Uber QueryGPT、LinkedIn SQL Bot、Spider 2.0、BEAVER 等），以及对 spec 02–08 的修改建议 | 5 条建议全部采纳：architecture 修订说明第 9 条、24 章「调研采纳与不采纳」；spec 02/03/04/05/06/08/09 的 2026-11-22 修订 |
