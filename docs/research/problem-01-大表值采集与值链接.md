# 难题 01：大表上的值采集与值链接

- 日期：2026-11-22（北京时间）
- 对照的仓库版本：kele0808/text2sql `8090297`
- 相关：调研 [02-value-index-large-tables.md](02-value-index-large-tables.md)（完整对比表和实测数据，本篇不重复）、[01-text2sql-sota.md](01-text2sql-sota.md) §3.2、§5 建议 3
- 标注约定同调研笔记：**[已核实]** / **[未核实]**

## 1. 问题是什么

用户的问题里带的是**业务说法**，库里存的是**原值**，两者经常对不上：

```
用户：华东精密上个月的工单数
LLM 看到的结构：t_customer(id, name VARCHAR(100) COMMENT '客户名称', ...)
LLM 写出：WHERE c.name = '华东精密'
库里实际：'华东精密有限公司'
```

这条 SQL 能执行，返回 0 行，**不报错**。用户看到的是"上个月没有工单"，没有任何信号提示答案错了。

值链接要回答两件事：
1. 问题里的"华东精密"对应**哪一列**（决定检索时要带上哪张表）；
2. 这一列里**真实存的值**是什么（让 LLM 直接用原值，不用猜）。

## 2. 为什么难

- **值不在元数据里。** 表结构和注释能告诉 LLM 有 `name` 这一列，但看不到列里的数据。要知道真实值，就得读业务行。
- **提问时现查不可行。** 不知道该查哪张表哪一列；几百张表逐列 `LIKE '%华东精密%'` 跑不完，MySQL 上的 `LIKE '%..%'` 也走不了索引。
- **离线采集要读业务库，成本和安全都受限。**
  - 我们连的是 MySQL 只读账号 `t2s_ro`，不能压垮业务库，不能建表、建直方图；
  - 对大表做 `SELECT DISTINCT col` 要全表扫描再去重，成本随行数线性增长；
  - 值是业务数据的副本，复制到 PG 后不再受业务库的租户隔离保护，必须自己按租户存、按租户查；敏感列（手机号、姓名、地址）不能进来。
- **大表恰恰最重要。** 订单表、工单表这类大表上的 `status`、`channel` 是最常用的过滤列。原设计对超过 200 万行的表整表跳过，等于放弃了最有价值的那部分列。
- **值会变。** 新客户、新状态要等下一次构建才搜得到。

## 3. 工业界怎么解决

（细节和来源见调研 02 §1）

- **supersonic**（腾讯音乐）：用户手动在维度上开启字典；`group by dim order by count(1) desc limit N`，默认只扫最近 1 天；值加载进 HanLP 词典做匹配 **[已核实]**。
- **Databricks Genie**：作者为最多 120 列开启 entity matching，每列最多 1024 个值、每个值最长 127 字符；带行过滤或列掩码的表整表排除；手动刷新 **[已核实]**。
- **Snowflake Cortex Analyst**：低基数维度由用户在语义模型里写 `sample_values`；高基数维度建 Cortex Search Service，扫描跑在可计费的 warehouse 上，按 `TARGET_LAG` 刷新 **[已核实]**。
- **LinkedIn SQL Bot**：对类别字段取 top-K 值放进上下文（[2507.14372](https://arxiv.org/abs/2507.14372)，见 01 §3.2）。
- **SQLBot**：没有找到自动值采集，靠字段自定义注释和术语库 **[部分核实]**。

共同点：**没有一家自动去扫大表**——要么人选列，要么限定范围，要么花数仓的钱。

## 4. 科研界怎么解决

- **CHESS**（[2405.16755](https://arxiv.org/abs/2405.16755)）：按列名关键词跳过 id/url/email/phone 等列，对其余文本列 `SELECT DISTINCT`，建 MinHash LSH，检索时再加编辑距离和 embedding **[已核实]**。
- **CodeS**：所有文本列 `SELECT DISTINCT`，建 BM25 索引 **[已核实]**。
- **FLINT**（[2610.03524](https://arxiv.org/abs/2610.03524)）：金融库把概念存成不透明整数键时，通用方法低于 50%，需要 lookup agent 去解析参照表（见 01 §3.2）。

它们都在 BIRD / Spider 这类 SQLite 小库上验证，扫描成本不是问题；证明的是"值链接是主要错误来源之一"，没有回答"大表上怎么采"。

## 5. 我们怎么解决

| 环节 | 做法 | 位置 |
| --- | --- | --- |
| 选列 | 只看非键、非敏感、低基数的文本列；按租户存；一组有先后顺序的跳过原因 | spec 02 §4.11 列筛选；plan 02 Task 14 |
| 行数估计 | 构建时现读 `information_schema.TABLES.TABLE_ROWS`（8.0 先 `SET SESSION information_schema_stats_expiry = 0`），不用快照里可能过时的值；快照之后被删的表记 `TABLE_GONE` | spec 02 §4.11 构建时现读行数；plan 02 Task 15、17 |
| 大表 | 逐列判断：有 `(租户列, 本列)` 前导整列 BTREE 索引才采，否则 `BIG_TABLE_NO_INDEX` | spec 02 §4.11；plan 02 Task 14 |
| 大表执行前 | `EXPLAIN FORMAT=TRADITIONAL`，看到 `Using index for group-by` 才执行，否则 `NO_LOOSE_SCAN` | spec 02 §4.11 采集；plan 02 Task 15 |
| SQL | `DISTINCT` 只放裸列，租户键在 Python 里转字符串；`MAX_EXECUTION_TIME` + `LIMIT max_distinct + 1` | spec 02 §4.11；plan 02 Task 15 |
| 存储与检索 | PG 三张表，`pg_trgm` 精确 + 模糊匹配，强制按租户过滤，保留最近 2 次构建 | spec 02 §4.11 存储 / 检索；plan 02 Task 16 |
| 没有索引的大表枚举列 | 由人在 `columns.yaml` 写 `values: [...]`，不扫业务库 | spec 05 §4.1 |
| 兜底 | 值索引只给候选；最终值由网关到业务库 `IN (...)` 精确确认 | spec 08 A5 |
| 验收 | spec 02 验收 14–19；spec 05 验收 12 | — |

## 6. 为什么这样解决 / 不采纳什么

- **为什么用松散索引扫描**：它的成本和 distinct 值个数成正比，和行数无关；5.7、8.0 都支持；只读账号就能用；`EXPLAIN` 能在执行前确认（MySQL 手册 GROUP BY Optimization，本机实测见调研 02 §2.2）。
- **为什么还要 `EXPLAIN` 门槛**：快照里有索引不等于优化器一定选松散扫描（小表上它就不选）；门槛保证大表上绝不会退化成全表扫描。
- **为什么 `DISTINCT` 只放裸列**：实测 `CAST(tenant_id AS CHAR)` 和 `NULL AS tenant_key` 都会让松散扫描失效。
- **为什么用 `pg_trgm` 而不是 LSH / embedding**：PG 自带，不新增服务和依赖（spec 02 §4.11 参考）。
- **不采纳**：抽样（5.7 无 `TABLESAMPLE`、`ORDER BY RAND()` 全表扫描、`LIMIT N` 有偏）；依赖 8.0 直方图（`t2s_ro` 建不了，5.7 没有）；只扫最近 N 天（需要时间列且有索引）；直接调大行数阈值（只是推迟问题）。详见调研 02 §4。

## 7. 未验证项

- `WHERE` 里的 `IS NOT NULL` / `<> ''` / `CHAR_LENGTH` 不影响松散扫描：只在本机 10 万行临时表上验证，plan 02 Task 15 Step 7 会在夹具上再测一次。
- 行数估计在构建时现读，8.0 会话里关掉统计缓存（本机 8.0.46 实测有效：同一张表默认读到 `0`，关掉后 `100464`）；但 `TABLE_ROWS` 仍是 InnoDB 估计，偏小时靠 `MAX_EXECUTION_TIME` 截断成 `TIMEOUT`。不执行 `ANALYZE TABLE`。
- 中文同义词、拼音、简称（"华东精密" ↔ "HDJM"）不在 MVP，阶段 2 再做。
- 真实库的租户列名是否统一、值能否进 LLM 上下文：spec 02 开放决策 3。
