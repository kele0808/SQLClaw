# 值索引在大表上怎么采：工业界、科研界的做法与本机实测

- 调研日期：2026-11-22（北京时间）
- 对照的仓库版本：kele0808/text2sql `d32bcb7`（plan 02 Task 2 已完成，值索引还没写代码）
- 方法：直接读开源项目的源码文件、厂商文档页面和 MySQL 官方手册；在本机 Compose 的 MySQL 5.7.44 / 8.0.46 上用 10 万行的表实测 `EXPLAIN`。
- 标注约定同 01：**[已核实]** 表示这次亲自打开过源码、文档或跑过实验；**[未核实]** 表示没有打开原文，或只看了二手资料。
- 落地：spec 02 §4.11 的 2026-11-22 修订（大表索引路径、`BIG_TABLE_NO_INDEX`、`NO_LOOSE_SCAN`，验收 17、18），spec 05 §4.1 的 `values`，plan 02 Task 14、15。对应的难题笔记：[problem-01](problem-01-大表值采集与值链接.md)。

---

## 0. 结论先行

1. **原来的设计是整表跳过。** `row_estimate` 超过 200 万行的表，整张表都不采（`TOO_MANY_ROWS`）。问题在于，订单表这类大表上的 `status`、`channel` 往往正是用户最常拿来过滤的列。
2. **没有一家工业产品会自动扫大表。** 它们靠三种办法：
   - 用户显式选列（supersonic、Genie entity matching）；
   - 限定扫描范围（supersonic 默认只扫最近 1 天）；
   - 把扫描成本交给数仓算力（Snowflake Cortex Search 用 warehouse 定时刷新）。
   科研项目（CHESS、CodeS）面向 SQLite 小库，对整列直接 `SELECT DISTINCT`，完全没考虑大表。
3. **我们连的是 MySQL 只读库，能利用的是松散索引扫描（loose index scan）。** `DISTINCT` 的列正好是某个 BTREE 索引的最左前缀时，MySQL 每个不同值只读一次索引，成本和 distinct 值个数成正比，和行数无关。
4. **采纳的规则**：大表逐列判断，有 `(租户列, 本列)` 前导整列索引才采，否则 `BIG_TABLE_NO_INDEX`；执行前 `EXPLAIN`，看到 `Using index for group-by` 才执行，否则 `NO_LOOSE_SCAN`。没有索引的枚举列，到 Spec 05 时由人在 `columns.yaml` 里用 `values` 声明。
5. **实测纠正了原来 SQL 的一处写法**：`DISTINCT CAST(tenant_id AS CHAR), col` 和 `DISTINCT NULL AS tenant_key, col` 都会让松散扫描失效，退化成全索引扫描。所以 `DISTINCT` 里只放裸列，租户键在 Python 里转字符串。`WHERE` 里的 `IS NOT NULL`、`<> ''`、`CHAR_LENGTH` 则不影响松散扫描。

---

## 1. 各家怎么做

| 项目 | 选列方式 | 怎么取值 | 大表怎么办 | 刷新 | 来源 |
| --- | --- | --- | --- | --- | --- |
| **CHESS**（科研，BIRD） | 列名含 `_id`、`url`、`email`、`phone`、`date`、`address` 等关键词就跳过；再看 distinct 值的总长度和个数（例如 `count_distinct < 100`，或总长 < 2000000 且平均长度 < 25） | 先 `SELECT SUM(LENGTH(..)), COUNT(..) FROM (SELECT DISTINCT col ...)` 统计，再 `SELECT DISTINCT col ... WHERE col IS NOT NULL`，超时 480 秒；建 MinHash LSH | 不考虑（SQLite 小库，整列扫描） | 离线一次 | [preprocess.py](https://github.com/ShayanTalaei/CHESS/blob/main/src/database_utils/db_values/preprocess.py) **[已核实]** |
| **CodeS**（科研，BIRD/Spider） | 所有文本列 | `SELECT DISTINCT col FROM t WHERE col IS NOT NULL`，用 pyserini 建 BM25 索引 | 不考虑 | 离线一次 | [build_contents_index.py](https://github.com/RUCKBReasoning/codes/blob/main/build_contents_index.py) **[已核实]** |
| **supersonic**（腾讯音乐，工业） | 不自动选，用户在维度上手动开启字典 | `select dim, count(1) from tbl where <日期范围> group by dim order by count(1) desc limit N`，按频次取 top N | 默认日期范围是最近 1 天（`s2.item.value.date.start/end` 默认 1），只扫一小片数据 | 离线任务 `DictTask` 拉取（01 §2.2）；调度频率 **[未核实]** | [DictUtils.java](https://github.com/tencentmusic/supersonic/blob/master/headless/server/src/main/java/com/tencent/supersonic/headless/server/utils/DictUtils.java) **[已核实 SQL 模板与默认值]** |
| **Databricks Genie**（工业） | entity matching：作者为最多 120 列开启，每列最多 1024 个 distinct 值，每个值最长 127 字符 | 平台内部采集，用作者的数据权限 | 带行过滤（row filter）或列掩码的表整表排除，和我们的租户规则是同一个思路 | 作者手动点 Refresh | [Tune Genie Agent quality](https://docs.databricks.com/aws/en/genie/knowledge-store) **[已核实]** |
| **Snowflake Cortex Analyst**（工业） | 低基数（约 1–10 个值）维度：用户在语义模型里直接写 `sample_values`；高基数或常变的维度：单独建 Cortex Search Service | Search Service 是 `SELECT DISTINCT dim FROM ...`，跑在 warehouse 上 | 扫描成本交给数仓（`WAREHOUSE = xsmall`、`TARGET_LAG = '1 hour'`），额外计费 | 按 `TARGET_LAG` 自动刷新 | [Improve literal search](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/cortex-analyst-search-integration) **[已核实]** |
| **SQLBot**（DataEase，工业） | 没有找到自动值采集；值的含义靠用户写字段的自定义注释（如"1=已支付，0=未支付"）和术语库 | — | — | — | [DeepWiki：Data Source Management](https://deepwiki.com/dataease/SQLBot/3.2-data-source-management)、[datasource.py](https://github.com/dataease/SQLBot/blob/v1.10.3/backend/apps/datasource/api/datasource.py) **[部分核实：只读了 DeepWiki 和一个 API 文件，没有通读源码]** |
| WrenAI、Vanna | — | — | — | — | 这次没有找到对应的值采集代码 **[未核实]** |

**归纳：**
- 科研项目只解决"小库里值怎么链接"，扫描成本不是它们的问题。
- 工业项目都把"扫哪些列"交给人或配置，而且都有明确的上限（Genie 的 120 列 × 1024 值、supersonic 的 top N + 最近 1 天）。
- 只有 Snowflake 会自动扫高基数列，前提是有可计费的数仓算力。我们的业务库是 MySQL 只读副本，不能照搬。

---

## 2. MySQL 能用的机制

### 2.1 松散索引扫描（loose index scan）**[已核实]**

[MySQL 手册 GROUP BY Optimization](https://dev.mysql.com/doc/refman/5.7/en/group-by-optimization.html) 的条件（`DISTINCT` 同样适用）：

- 单表查询；
- "all distinct attributes refer to columns that form a leftmost prefix of the index"：`DISTINCT` 的每一项都必须是索引最左前缀里的列；
- 索引要存整列值，前缀索引（如 `INDEX (c1(10))`）不行；
- 索引要有序（BTREE 可以，HASH 不行）；
- 能用时，`EXPLAIN` 的 `Extra` 显示 `Using index for group-by`。

所以 spec 的规则是：BTREE、前导列依次为 `(租户列, 本列)`、这几列 `sub_parts` 为空。快照里的 `IndexInfo` 已经有有序的 `columns`、`sub_parts`、`index_type`，不需要改模型。

### 2.2 本机实测（2026-11-22，Compose 容器 5.7.44 / 8.0.46）**[已核实]**

表：10 万行，`tenant_id` 2–3 个取值，`status` 4–6 个取值（含 `NULL` 和空串），索引 `(tenant_id, status)`；`ANALYZE TABLE` 之后执行。两个版本的结果一致：

| 查询 | `type` | `Extra` | 结论 |
| --- | --- | --- | --- |
| `DISTINCT tenant_id, status`（无 `WHERE`） | `range`，rows 3–4 | `Using index for group-by; Using temporary` | 松散扫描 |
| `DISTINCT tenant_id, status` + `WHERE status IS NOT NULL AND status <> '' AND CHAR_LENGTH(status) <= 128` | `range`，rows 3–4 | `Using where; Using index for group-by; Using temporary` | `WHERE` 不影响 |
| `DISTINCT CAST(tenant_id AS CHAR), status` | `index`，rows 约 10 万 | `Using index; Using temporary` | **失效**，全索引扫描 |
| `DISTINCT NULL AS tenant_key, status`（另一张表，`status` 单列索引） | `range`，rows 约 5 万 | `Using where; Using index; Using temporary` | **失效** |
| `DISTINCT status`（同上，`status` 单列索引） | `range`，rows 2–7 | `Using where; Using index for group-by; Using temporary` | 松散扫描 |
| `DISTINCT NULL, status`，索引是 `(tenant_id, status)` | `index`，rows 约 10 万 | `Using index; Using temporary` | 本列不是最左列，失效 |
| `DISTINCT tenant_id, channel`（`channel` 无索引） | `ALL` | `Using where; Using temporary` | 全表扫描 |
| 4 行小表上的 `DISTINCT tenant_id, status` | `index` | `Using where; Using index; Using temporary` | 小表优化器不选松散扫描 |

另外确认了：
- `t2s_ro`（只有 `SELECT`）可以执行 `EXPLAIN FORMAT=TRADITIONAL`，5.7 和 8.0 都支持这个写法；
- 新建表 `ANALYZE TABLE` 之后，`information_schema.TABLES.TABLE_ROWS` 在两个版本上都约为 10 万（8.0 的 `information_schema_stats_expiry` 缓存也被刷新）。

`WHERE` 过滤的结论只在临时表和测试表上验证过，**[待 Task 15 在 5.7 / 8.0 夹具上再次 EXPLAIN 确认]**（plan 02 Task 15 Step 7）。即使某个版本上不成立，`EXPLAIN` 门槛也会把列记成 `NO_LOOSE_SCAN`，不会退化成全表扫描。

### 2.3 8.0 直方图 **[已核实：手册；未实测]**

8.0 的 `information_schema.COLUMN_STATISTICS` 存列的直方图，singleton 类型的直方图里有每个取值。但：
- 建直方图要执行 `ANALYZE TABLE ... UPDATE HISTOGRAM`，需要 `SELECT` 以外的权限，`t2s_ro` 做不了，只能读 DBA 已经建好的；
- 5.7 没有直方图。
所以只适合在阶段 2 作为只读补充。

---

## 3. 采纳的规则（spec 02 §4.11）

1. 小表（`row_estimate ≤ value_index_max_rows`）：规则和 SQL 不变（除了下面第 4 条），直接执行。
2. 大表：先过完其他所有规则；最后检查是否有 BTREE 索引，前导列依次是 `(租户列, 本列)`（关闭租户规则时本列是第一列），且是整列索引。没有就记 `BIG_TABLE_NO_INDEX`。include 不能放宽它。
3. 大表的列，执行前先 `EXPLAIN FORMAT=TRADITIONAL` 同一条 SQL，`Extra` 含 `Using index for group-by` 才执行，否则记 `NO_LOOSE_SCAN`。
4. 采集 SQL 的 `DISTINCT` 只放裸列：`DISTINCT tenant_id AS tenant_key, col AS value`，或关闭租户规则时的 `DISTINCT col AS value`；租户键在 Python 里转字符串。
5. `TIMEOUT`、`TOO_MANY_VALUES` 以及硬规则不变。
6. 大表上没有索引的枚举列：Spec 05 落地后在 `columns.yaml` 用 `values: [...]` 声明，不扫业务库（借鉴 Snowflake 的 `sample_values`）。

## 4. 不采纳

| 不采纳 | 原因 |
| --- | --- |
| 抽样 | 5.7 没有 `TABLESAMPLE`；`ORDER BY RAND()` 本身要全表扫描；`LIMIT N` 偏向聚簇索引靠前的旧数据，会漏掉新出现的状态值 |
| 依赖 8.0 直方图 | `t2s_ro` 不能建；5.7 没有；只能作为阶段 2 的只读补充 |
| supersonic 式"只扫最近 N 天" | 需要知道每张表的时间列和分区方式；在没有时间列索引的表上，`WHERE created_at >= ...` 照样是全表扫描 |
| 直接调大 `value_index_max_rows` | 全表 `DISTINCT` 的成本随行数线性增长，只是把问题往后推 |

## 5. 未验证 / 局限

- `WHERE` 过滤不影响松散扫描：只在本机 10 万行临时表 / 测试表上验证过，真实库的数据分布和统计信息不同，优化器可能选别的计划（`EXPLAIN` 门槛兜底）。
- `row_estimate` 来自快照：结构不变时沿用旧快照的值，8.0 还有统计缓存，可能过时。表在两次快照之间变大时，这次仍按普通表执行，单条查询会被 `MAX_EXECUTION_TIME` 截断成 `TIMEOUT`（spec 02 §4.11）。
- 带 `NULL` 和空串时，`LIMIT max_distinct + 1` 是在 `WHERE` 过滤之后计数的，和原设计一致；如果以后大表 SQL 去掉 `WHERE`，计数会把 `NULL`、空串也算进去，`TOO_MANY_VALUES` 会稍早触发。
- SQLBot、WrenAI、Vanna 的值采集没有通读源码。
- MariaDB 没有测。
