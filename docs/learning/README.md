# 学习笔记

这里记录实现过程中的重难点讲解

- 这些笔记只用来帮助理解代码，**不是设计依据**。设计以 `docs/architecture.md` 和 `docs/specs/` 为准。
- 每篇对应一个模块或一个概念，文件名用 `NN-主题.md`。
- 代码改了而笔记没跟上时，以代码为准，并顺手更新笔记。

## 目录

| 编号 | 主题 | 对应代码 |
| --- | --- | --- |
| 01 | [基于 PostgreSQL 的任务队列](01-job-queue.md) | `src/text2sql/jobs/queue.py` |
| 02 | [Worker：从队列领任务并执行](02-worker.md) | `src/text2sql/jobs/worker.py` |
