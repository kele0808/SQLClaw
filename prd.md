# **生产级 Text2SQL Agent：最终技术架构方案**

结合前面讨论的整体架构、复杂多表关联、Schema Evolution，以及生产环境中的数据安全、准确性、可维护性，我建议把项目正式定位为：

**Enterprise-grade Text2SQL Agent：面向企业复杂数据库的自然语言查询与智能数据分析平台。**

这次我会对前面的方案做一次架构收敛，而不是简单叠加模块。重点补齐之前容易遗漏的生产问题：

1. 数据库 Schema 和业务语义如何保持一致。
2. 多表关联如何保证正确性，而不只是找到一条 JOIN 路径。
3. 数据库 DDL 变更时，如何避免 Agent 使用过期元数据。
4. 多表 JOIN、聚合、去重、时间口径如何保证结果正确。
5. 如何防止错误 SQL、越权 SQL 和高成本 SQL。
6. 如何评估 Agent 的真实业务准确率。
7. 如何实现可观测、可回滚、可持续演进的工程体系。

我认为最重要的架构调整是：

**不要把 Text2SQL 做成一个 RAG + LLM + SQL Generator 的简单流水线，而要把它做成一个具有数据库理解、查询规划、SQL 编译、执行验证和知识持续演进能力的系统。**

# **一、产品目标与系统边界**

## **1.1 核心目标**

用户通过自然语言完成数据库查询，例如：

- 查询最近 30 天各行业的工单数量。
- 查询每个部门员工处理的工单数量及平均处理时间。
- 查询过去一个季度不同客户的工单完成率。
- 查询创建工单最多的员工，以及对应的部门和客户。
- 找出工单数量增长最快的行业。
- 对比今年和去年同期各业务线的处理效率。

系统需要支持：

| 能力        | 具体要求                  |
| --------- | --------------------- |
| 自然语言理解    | 理解业务意图、时间范围、指标和维度     |
| Schema 理解 | 理解数据库表、字段、类型、注释       |
| 多表关联      | 支持多层 JOIN、多路径选择、多对多关系 |
| 业务语义      | 理解指标口径、枚举、业务规则        |
| SQL 生成    | 支持复杂 SQL、子查询、CTE、窗口函数 |
| SQL 验证    | AST、类型、语义、权限、执行计划     |
| 错误修复      | 根据错误反馈有限次数修复          |
| Schema 演进 | 自动发现并处理数据库结构变化        |
| 多轮对话      | 理解上下文，处理追问和修改条件       |
| 数据安全      | 只读、权限隔离、字段脱敏、审计       |
| 评估体系      | 自动评测 SQL 和最终查询结果      |

## **1.2 明确不做的事情**

第一阶段不建议支持：

- 自然语言修改数据库。
- 自动执行 INSERT、UPDATE、DELETE、DDL。
- 任意跨数据库 JOIN。
- 自动修改业务数据库 Schema。
- 完全自动学习并发布未经验证的业务指标。
- 依赖 LLM 自主决定权限。

这些功能不属于第一版 Text2SQL 的核心价值。

# **二、最终总体架构**

我建议采用：

**Metadata-Driven + Hybrid RAG + Join Graph + Semantic Layer + Query Planner + Execution-Grounded Agent**

整体架构如下：

# **Text2SQL Platform**

Enterprise Architecture · Logical View

**① User Interface & API Gateway**

自然语言查询 · 多轮对话 · SQL 预览 · 结果展示 · 用户反馈

**② Agent Orchestration — LangGraph**

Intent Understanding

Schema Retrieval

Query Planning

SQL Generation

Validation

Repair & Explanation

**③ Intelligence Engine**

Hybrid RAG

Join Graph

Semantic Layer

Query IR / Planner

SQL Compiler & AST Validator

**④ Secure Execution Layer**\
SQL Policy · EXPLAIN · Cost Control · Read-only Gateway · Result Validation · Audit

**⑤ Data Infrastructure**

MySQL / PostgreSQL

Metadata Catalog

pgvector / FTS

Versioned Join Graph

**⑥ Continuous Evolution & Evaluation**

Schema Evolution · Semantic Evolution · Dependency Analysis · Evaluation · Observability

这里有一个设计原则：

- Agent 负责理解和编排。
- Metadata 负责提供可信的数据库知识。
- Planner 负责确定查询逻辑。
- SQL Compiler 负责生成可执行 SQL。
- Validator 负责规则验证。
- Execution Gateway 负责安全执行。
- Evaluation 负责持续度量质量。

不要让 LLM 直接承担所有职责。

# **三、技术选型**

建议第一阶段使用模块化单体，先不拆微服务。

| 层级            | 技术选型                     | 用途             |
| ------------- | ------------------------ | -------------- |
| Agent         | LangGraph                | 状态化工作流         |
| LLM           | GPT / Claude / Qwen      | 理解、规划、生成       |
| Backend       | Python + FastAPI         | AI 服务          |
| 企业集成          | Spring Boot              | 可选业务接入层        |
| ORM           | SQLAlchemy               | 元数据管理          |
| SQL Parser    | SQLGlot                  | SQL AST 和方言处理  |
| Metadata DB   | PostgreSQL               | Schema、版本、业务语义 |
| Vector        | pgvector                 | 向量检索           |
| Keyword       | PostgreSQL FTS / BM25 实现 | 关键词检索          |
| Reranker      | BGE Reranker             | 重排             |
| Graph         | NetworkX                 | Join Graph 算法  |
| Cache         | Redis                    | 缓存、任务协调        |
| Task Queue    | Celery / ARQ             | 异步同步任务         |
| Evaluation    | 自研 + pytest              | 自动评测           |
| Observability | OpenTelemetry            | Trace 和指标      |
| Deployment    | Docker Compose           | 初期部署           |

几个选型上的补充：

1. 不需要一开始引入 Neo4j。Join Graph 可以先用 PostgreSQL 存储关系、NetworkX 进行图算法。
2. pgvector 与全文检索可以复用 PostgreSQL，降低基础设施成本。
3. SQLGlot 负责解析和转换，不负责证明 SQL 的业务正确性。
4. LangGraph 负责工作流状态管理，不应该把业务规则全部写进 Prompt。
5. 不建议第一阶段自己训练 Text2SQL 模型，先通过模型 API 建立可靠的工程系统。

# **四、Metadata Infrastructure：数据库理解基础设施**

这是整个系统的根基。

普通 RAG 存储的是文档，而 Text2SQL 需要建立结构化的数据库知识模型。

## **4.1 Metadata 分层**

建议分为六层：

| 层级           | 内容          | 示例                                 |
| ------------ | ----------- | ---------------------------------- |
| DataSource   | 数据源信息       | MySQL business_db                  |
| Database     | 数据库及 Schema | ka_ops                             |
| Table        | 表定义         | t_industry_work_log                |
| Column       | 字段定义        | create_time                        |
| Relationship | 表之间的关系      | work_log.industry_id → industry.id |
| Semantic     | 业务语义        | 工单完成数                              |

除此之外还要保存：

- Index。
- Primary Key。
- Foreign Key。
- Unique Constraint。
- Check Constraint。
- View。
- Table Comment。
- Column Comment。
- Partition。
- Generated Column。
- Data Type。
- Nullability。
- Default Value。

注意：MySQL 并不是所有业务表都存在 Foreign Key，因此不能仅通过外键构建完整的关系图。

## **4.2 元数据表设计**

建议至少包含以下表：

```
metadata_datasource
metadata_database
metadata_table
metadata_column
metadata_index
metadata_constraint
metadata_relationship
metadata_view
metadata_snapshot
metadata_release
metadata_change_event
metadata_dependency
metadata_embedding
metadata_sync_job
```

关键字段示例：

**metadata_table**

```
CREATE TABLE metadata_table (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    datasource_id VARCHAR(64) NOT NULL,
    database_name VARCHAR(128),
    table_name VARCHAR(128) NOT NULL,
    table_comment TEXT,
    table_type VARCHAR(32),
    status VARCHAR(32),
    schema_version BIGINT,
    content_hash VARCHAR(128),
    created_at DATETIME,
    updated_at DATETIME
);
```

**metadata_column**

```
CREATE TABLE metadata_column (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    table_id BIGINT NOT NULL,
    column_name VARCHAR(128) NOT NULL,
    data_type VARCHAR(128),
    column_type VARCHAR(128),
    is_nullable BOOLEAN,
    is_primary BOOLEAN,
    is_unique BOOLEAN,
    default_value TEXT,
    column_comment TEXT,
    ordinal_position INT,
    status VARCHAR(32),
    content_hash VARCHAR(128)
);
```

生产实现还需要：

- datasource 范围内唯一约束。
- 外键和索引。
- 版本有效期。
- 逻辑删除。
- 乐观锁或版本冲突检测。

## **4.3 元数据身份问题**

这里补充一个容易遗漏的点：

不能把字段的数据库自增 ID 当成跨版本身份。

例如：

```
t_order.user_id
```

被删除后又新增同名字段，它们不一定是同一个业务字段。

建议使用：

```
datasource_id
+ database_name
+ table_name
+ column_name
```

作为自然标识，并配合内部 UUID、版本有效期和变更记录。

对于字段重命名，优先通过 Migration Event 提供明确的 Rename Mapping。不要仅凭字段相似度自动继承历史语义。

# **五、Schema Evolution：Schema 自动演进**

这是前面方案需要重点补齐的模块。

我建议把它独立成一个 Metadata CI/CD 系统。

## **5.1 总体流程**

## **5.2 五种变化处理**

| 变化   | Metadata | Embedding   | Join Graph | Semantic |
| ---- | -------- | ----------- | ---------- | -------- |
| 新增字段 | 新增       | 增量          | 检测候选关联     | 识别业务含义   |
| 删除字段 | 失效       | 删除或版本隔离     | 删除相关边      | 检查依赖     |
| 修改字段 | 更新       | 按内容 Hash 更新 | 重新验证       | 检查语义兼容   |
| 新增表  | 新增       | 全表增量        | 新节点和候选边    | 建立领域归属   |
| 删除表  | 失效       | 版本隔离        | 删除节点和边     | 检查指标依赖   |

另外必须覆盖：

- 表重命名。
- 字段重命名。
- 主键变化。
- 外键变化。
- 索引变化。
- 视图定义变化。
- 分区变化。
- 字段类型兼容性变化。
- 表注释、字段注释变化。

## **5.3 变更发现机制**

建议三种机制结合：

| 方式                      | 作用                  |
| ----------------------- | ------------------- |
| Flyway / Liquibase Hook | 变更后立即触发             |
| 定时 Schema Snapshot      | 补偿漏掉的变更             |
| 全量 Reconciliation       | 定期核对真实数据库和 Metadata |

推荐初期：

- Migration 变更后触发。
- 每小时轻量 Snapshot 检查。
- 每日完整一致性校验。

具体频率根据数据库规模和变更频率调整。

## **5.4 Dependency Graph**

必须建立依赖关系。

例如：

```
t_work_order.status
       |
       +-- Embedding
       +-- Join Relation
       +-- Metric: completed_count
       +-- SQL Example: sql_001
       +-- Business Rule: status_mapping
       +-- Query Template
```

字段删除时，自动找到所有依赖它的对象。

依赖图不仅服务于 Schema Evolution，也能用于：

- SQL 生成时的可信知识筛选。
- 指标失效检测。
- 业务影响分析。
- 历史 SQL 清理。
- 变更审计。

## **5.5 版本和原子发布**

采用不可变 Snapshot + Release Pointer：

```
Release V14
    |
    +-- Schema V14
    +-- Join Graph V14
    +-- Semantic V14
    +-- Embedding V14

Release V15
    |
    +-- Schema V15
    +-- Join Graph V15
    +-- Semantic V15
    +-- Embedding V15
```

所有组件构建完成且验证通过后，事务性切换 Active Release。

每个 Agent Query 固定一个 `metadata_release_id`。

旧版本保留到没有活跃查询引用，或者超过配置的保留期后再清理。

**注意一个关键边界：Metadata 回滚不等于数据库回滚。** 如果真实数据库已经删除字段，切回旧 Metadata 并不能让旧 SQL 重新执行。

因此，执行前还需要检查实际数据库 Schema 是否与查询使用的结构兼容。

# **六、Join Intelligence：复杂多表关联引擎**

这是整个项目最值得投入研发资源的核心模块。

我建议将其从普通 RAG 中完全独立出来。

## **6.1 为什么不能只依赖 LLM 生成 JOIN？**

假设数据库有以下表：

**工单业务数据库**

**t_work_order** · 工单主表

id, customer_id, employee_id, industry_id

**t_customer** · 客户表

id, customer_name, industry_id

**t_employee** · 员工表

id, department_id

**t_department** · 部门表

id, department_name

**t_industry** · 行业表

id, industry_name

**t_work_process** · 工单处理记录

id, work_id, operator_id, duration

用户问：

> 查询各部门员工负责的工单数量，以及平均处理时长。

涉及：

```
t_work_order
     |
     +-- t_employee
     |       |
     |       +-- t_department
     |
     +-- t_work_process
```

这不是简单的表关联问题。

`work_order` 与 `work_process` 可能是一对多。

如果直接 JOIN：

```
SELECT
    d.department_name,
    COUNT(w.id),
    AVG(p.duration)
FROM t_work_order w
JOIN t_employee e
    ON w.employee_id = e.id
JOIN t_department d
    ON e.department_id = d.id
JOIN t_work_process p
    ON p.work_id = w.id
GROUP BY d.department_name;
```

那么：

- 工单数量可能被重复计算。
- 平均处理时长的统计口径可能不正确。
- 一个工单多个处理记录可能导致结果膨胀。

因此必须引入 Cardinality Analysis 和 Aggregation Planner。

## **6.2 Join Graph 数据模型**

将数据库表建模为节点，表关联建模为边。

```
Node:
  Table
  Column

Edge:
  Source Table
  Source Column
  Target Table
  Target Column
  Relation Type
  Cardinality
  Confidence
  Evidence
```

建议支持以下关系：

| 关系类型         | 示例       |
| ------------ | -------- |
| ONE_TO_ONE   | 用户与用户详情  |
| ONE_TO_MANY  | 工单与处理记录  |
| MANY_TO_ONE  | 工单与客户    |
| MANY_TO_MANY | 用户与角色    |
| UNKNOWN      | 未验证的关联关系 |

关系证据来源：

1. Primary Key / Foreign Key。
2. Unique Index。
3. 历史 SQL。
4. 业务文档。
5. 字段命名相似度。
6. 数据采样与值分布。
7. 人工确认。

每条边需要记录：

```
{
  "source": "t_work_order.customer_id",
  "target": "t_customer.id",
  "relation_type": "MANY_TO_ONE",
  "confidence": 0.99,
  "evidence": [
    "FOREIGN_KEY",
    "VALUE_VALIDATION"
  ],
  "status": "VERIFIED"
}
```

这里的置信度是内部证据评分，不应简单等同于真实概率。

## **6.3 Join Path Finder**

当用户提出问题时：

1. Retrieval 找出候选表。
2. Join Graph 找出表之间的路径。
3. 生成多个候选 Join Path。
4. 根据关系可信度、路径长度、基数风险、业务语义打分。
5. 选择最合适的路径。
6. 无法确定时向用户澄清。

路径评分可以采用：

\\[ Score(P)= w_1R+w_2S+w_3E-w_4L-w_5F \\]

其中：

- \\(R\\)：关系可信度。
- \\(S\\)：业务语义匹配度。
- \\(E\\)：证据完整性。
- \\(L\\)：路径长度惩罚。
- \\(F\\)：关联膨胀风险。

这只是初期的候选路径排序模型，权重需要通过评测集调优。

## **6.4 多对多关系**

必须支持桥接表。

例如：

```
user
  |
user_role
  |
role
```

如果查询用户和角色信息，不能直接猜测两个实体之间的关联。

Join Planner 需要识别：

- Bridge Table。
- 复合主键。
- 多列关联条件。
- 关系唯一性。
- 是否存在额外过滤条件。

## **6.5 Cardinality Analysis**

这是防止错误统计的关键。

在 JOIN 前，Planner 需要判断：

```
work_order 1 : N work_process
```

如果用户查询工单数，建议采用预聚合：

```
WITH process_agg AS (
    SELECT
        work_id,
        AVG(duration) AS avg_duration
    FROM t_work_process
    GROUP BY work_id
)
SELECT
    d.department_name,
    COUNT(DISTINCT w.id) AS work_count,
    AVG(p.avg_duration) AS avg_process_duration
FROM t_work_order w
JOIN t_employee e
    ON w.employee_id = e.id
JOIN t_department d
    ON e.department_id = d.id
LEFT JOIN process_agg p
    ON p.work_id = w.id
GROUP BY d.department_name;
```

这里的聚合方式也必须与业务定义一致。例如，平均每张工单的处理时长和所有处理记录的平均时长不是同一个指标。

## **6.6 多路径冲突**

例如：

```
work_order → customer → industry

work_order → industry
```

两个路径都能到达行业表。

此时不能简单选择最短路径。

需要判断：

- 工单所属行业还是客户所属行业？
- 两个字段是否表达同一个业务概念？
- 哪个关系有明确业务定义？
- 历史 SQL 通常使用哪条路径？

如果业务口径无法判断，应当让 Agent 提问，而不是静默猜测。

# **七、Semantic Layer：业务语义层**

这是之前架构中需要进一步强化的部分。

Schema 只告诉 Agent：

```
status INT
create_time DATETIME
duration BIGINT
```

但业务查询需要知道：

- 哪些状态代表完成？
- 工单完成率的分母是什么？
- 处理时长是否包含暂停时间？
- 最近 30 天按照创建时间还是完成时间？
- 行业归属以客户还是工单为准？

这些不是 Schema 能够回答的。

## **7.1 业务语义模型**

建议设计四种核心对象：

| 对象            | 含义   | 示例         |
| ------------- | ---- | ---------- |
| Business Term | 业务术语 | 工单         |
| Metric        | 指标   | 工单完成率      |
| Dimension     | 维度   | 行业、部门      |
| Business Rule | 业务规则 | 状态 3 表示已完成 |

### **Metric 示例**

```
metric:
  name: completed_work_order_count
  display_name: 已完成工单数

  expression:
    aggregation: count_distinct
    table: t_work_order
    column: id

  filters:
    - column: status
      operator: "="
      value: 3

  time_dimension:
    column: complete_time

  owner: business_team

  status: VERIFIED
```

### **Dimension 示例**

```
dimension:
  name: industry
  display_name: 行业

  source:
    table: t_industry
    column: industry_name

  join_path:
    - t_work_order.industry_id
    - t_industry.id

  status: VERIFIED
```

## **7.2 指标不能随意让 LLM 定义**

建议业务指标有明确状态：

```
DRAFT
  |
  v
REVIEWING
  |
  v
VERIFIED
  |
  v
DEPRECATED
```

只有 VERIFIED 指标可以默认用于生产查询。

对于没有定义的指标：

- 允许 Agent 提出候选计算方式。
- 展示计算口径。
- 必要时询问用户。
- 未经确认不应把猜测结果标成权威业务指标。

## **7.3 语义层和 Schema Evolution 联动**

例如：

原来：

```
status = 3 → 已完成
```

后来业务修改：

```
status = 3 → 已完成
status = 4 → 已取消
status = 5 → 已关闭
```

虽然数据库结构没有变化，但语义层需要更新。

因此：

```
Schema Evolution
       |
       +-- Table / Column
       +-- Relationship
       |
Semantic Evolution
       |
       +-- Metrics
       +-- Dimensions
       +-- Enum Mapping
       +-- Business Rules
```

两者统一进入 Metadata Release，但不能混成一个简单的 Schema Diff。

# **八、Hybrid RAG：数据库知识检索**

RAG 的任务不是直接生成 SQL，而是为 Planner 提供正确、精简、可追溯的知识。

## **8.1 检索对象**

不要只给 Table 做 Embedding。

建议构建五类索引：

| 索引                 | 内容           |
| ------------------ | ------------ |
| Table Index        | 表名、描述、业务领域   |
| Column Index       | 字段名、类型、注释、别名 |
| Relationship Index | 表关联、业务关系     |
| Metric Index       | 指标定义、统计口径    |
| SQL Example Index  | 已验证的历史 SQL   |

## **8.2 Hybrid Retrieval**

推荐流程：

1. BM25：匹配精确表名、字段名、业务关键词。
2. Vector：匹配语义相似的表、字段和指标。
3. Semantic Retrieval：检索已定义指标和维度。
4. RRF：融合不同检索通道。
5. Reranker：重排候选。
6. Join Graph Expansion：补充必要的关联表。

初始参数可以：

| 阶段                 | 参数       |
| ------------------ | -------- |
| BM25               | Top 30   |
| Vector             | Top 30   |
| RRF                | Top 30   |
| Reranker           | Top 10   |
| Join Expansion     | 按需扩展     |
| 最终 Planner Context | 相关表及必要字段 |

这些参数不是固定值，应通过 Recall\@K 和 Token 成本共同优化。

## **8.3 防止过期索引**

每个 Embedding 必须带有：

```
{
  "object_id": "column_1001",
  "object_version": 15,
  "release_id": "release_0015",
  "content_hash": "sha256:xxx",
  "status": "ACTIVE"
}
```

检索时必须约束 Active Release，不能让已删除字段通过历史向量重新进入上下文。

## **8.4 进一步降低 Token 消耗**

不要把整个数据库 Schema 塞给 LLM。

采用三级上下文：

```
Level 1: Database Overview
         |
Level 2: Candidate Tables + Relationships
         |
Level 3: Required Columns + Business Rules + Examples
```

只有在当前查询确实需要时，才继续展开。

这既能降低成本，也能减少无关 Schema 对模型的干扰。

# **九、Query Planner：不要直接让 LLM 写 SQL**

这是我认为整个架构最重要的设计决策之一。

建议引入中间表示：

**Query IR（Intermediate Representation）**

让 LLM 先理解用户的查询意图，转换成结构化查询计划，再交给 SQL Generator。

## **9.1 Query IR 示例**

用户：

> 查询最近 30 天每个行业的工单数量、完成数量和平均处理时长。

Query IR：

```
{
  "intent": "ANALYTICAL_QUERY",
  "metrics": [
    "work_order_count",
    "completed_work_order_count",
    "avg_process_duration"
  ],
  "dimensions": [
    "industry"
  ],
  "time_range": {
    "type": "RELATIVE",
    "value": 30,
    "unit": "DAY"
  },
  "filters": [],
  "sort": [],
  "limit": 100
}
```

随后 Planner 将它转成逻辑查询计划：

```
Query Plan
   |
   +-- Source: t_work_order
   |
   +-- Dimension: industry
   |
   +-- Metric: COUNT(DISTINCT work_order.id)
   |
   +-- Metric: Completed Work Order Count
   |
   +-- Metric: Average Process Duration
   |
   +-- Join: industry relation
   |
   +-- Join: process aggregate
   |
   +-- Filter: create_time >= ...
   |
   +-- Group By: industry
```

注意：如果没有业务定义明确指定时间字段，`最近 30 天` 仍可能有歧义，需要询问用户。

## **9.2 Planner 模块**

建议拆分：

```
planner/
├── query_understanding.py
├── query_ir.py
├── metric_resolver.py
├── dimension_resolver.py
├── join_planner.py
├── aggregation_planner.py
├── filter_planner.py
├── time_planner.py
├── query_decomposer.py
└── plan_validator.py
```

其中：

- Metric Resolver：找到指标定义。
- Dimension Resolver：找到维度定义。
- Join Planner：确定表关联路径。
- Aggregation Planner：解决聚合和数据膨胀。
- Filter Planner：处理过滤条件。
- Time Planner：处理时间语义。
- Query Decomposer：复杂查询拆分。
- Plan Validator：验证计划完整性。

## **9.3 复杂查询拆分**

对于非常复杂的分析问题，可以考虑分解为多个子查询。

但必须注意：

**多个子查询的结果不能被 Agent 随意拼接成一个看似正确的结论。**

每个子查询需要：

- 明确数据范围。
- 明确指标口径。
- 明确分组维度。
- 明确结果粒度。
- 明确关联键。

只有粒度兼容时才能合并。

# **十、SQL Generation & Validation**

## **10.1 SQL Generation**

建议：

```
Query IR
   |
   v
Logical Plan
   |
   v
Physical Plan
   |
   v
SQL Generator
   |
   v
SQL AST
   |
   v
Validation
```

SQL Generator 根据数据库方言生成 SQL。

第一版重点支持 MySQL，后续再支持 PostgreSQL。

不要让生成模型自行决定数据库方言。

## **10.2 AST Validation**

使用 SQLGlot 解析 SQL，检查：

- 是否只有 SELECT。
- 是否存在多语句。
- 是否包含 DDL/DML。
- 是否访问未授权表。
- 是否访问未授权字段。
- 是否存在危险函数。
- 是否包含不合理的笛卡尔积。
- 是否存在非法 JOIN。
- 是否符合数据库方言。

建议采用 AST 白名单，而不是仅通过正则匹配 `SELECT`。

例如：

```
import sqlglotfrom sqlglot import expdef validate_sql(sql: str):    statements = sqlglot.parse(sql, read="mysql")    if len(statements) != 1:        raise ValueError("Only one statement is allowed")    tree = statements[0]    if not isinstance(tree, exp.Query):        raise ValueError("Only query statements are allowed")    forbidden = (        exp.Insert,        exp.Update,        exp.Delete,        exp.Drop,        exp.Alter,        exp.Create,        exp.Command,    )    if any(tree.find_all(node) for node in forbidden):        raise ValueError("Forbidden SQL operation")    return tree
```

这只是初步示例，生产版本还需要：

- 递归检查 CTE 和子查询。
- 检查表、字段的实际解析结果。
- 处理存储过程和危险函数。
- 限制 `SELECT ... INTO OUTFILE` 等能力。
- 根据方言和数据库权限进行二次验证。

## **10.3 Semantic Validation**

语法正确不代表业务正确。

需要检查：

| 校验                     | 说明             |
| ---------------------- | -------------- |
| Table Validation       | 表是否真实存在        |
| Column Validation      | 字段是否真实存在       |
| Join Validation        | JOIN 是否符合已验证关系 |
| Metric Validation      | 指标是否定义正确       |
| Aggregation Validation | 聚合粒度是否正确       |
| Time Validation        | 时间字段和范围是否合理    |
| Filter Validation      | 过滤条件是否符合业务规则   |
| Null Validation        | NULL 是否可能影响结果  |

## **10.4 Query Cost Validation**

执行前必须执行：

```
EXPLAIN SELECT ...
```

根据执行计划判断：

- 是否全表扫描。
- 是否使用合理索引。
- JOIN 顺序是否合理。
- 是否存在高成本排序。
- 是否存在超大中间结果。
- 是否存在明显的笛卡尔积。

注意：EXPLAIN 本身也可能消耗资源，必须对 SQL 长度、复杂度和分析过程设置限制。对于支持的数据库，可以使用只生成计划而不实际执行的方式，避免误触发高成本查询。

# **十一、Secure Execution Gateway：生产安全**

这部分我建议完全独立于 Agent。

不要让 Agent 直接拿到业务数据库连接字符串和任意 SQL 执行权限。

## **11.1 安全架构**

```
Agent
  |
  v
SQL Validation
  |
  v
Authorization Policy
  |
  v
Cost Guard
  |
  v
Execution Gateway
  |
  v
Read-only Database Account
  |
  v
Result Masking
  |
  v
Audit
```

## **11.2 必须具备的安全能力**

| 能力      | 实现                |
| ------- | ----------------- |
| 身份认证    | 用户身份和 Token       |
| 数据源权限   | DataSource ACL    |
| 表级权限    | Table ACL         |
| 字段级权限   | Column ACL        |
| 行级权限    | Row-Level Policy  |
| 只读      | 数据库只读账号           |
| SQL 白名单 | AST Policy        |
| 超时      | Statement Timeout |
| 结果限制    | Max Rows          |
| 数据脱敏    | Result Masking    |
| 审计      | SQL、用户、时间、结果摘要    |

### **特别注意：行级权限**

例如：

用户 A 只能查看：

```
tenant_id = 1001
```

用户 B 只能查看：

```
tenant_id = 1002
```

不能依赖 LLM 记住添加租户过滤条件。

应该由可信的 Policy Engine 强制执行。

同时，必须考虑：

- UNION。
- CTE。
- 子查询。
- JOIN。
- 视图。
- 嵌套查询。

不能只在 SQL 字符串尾部拼接一个 `WHERE tenant_id = ?`。

最好使用数据库原生 Row-Level Security（支持时）或者经过严格验证的 AST Policy Rewriter。

## **11.3 资源限制**

建议初期配置：

| 限制            | 示例值     |
| ------------- | ------- |
| 最大返回行数        | 1000    |
| SQL 执行超时      | 10 秒    |
| 最大 JOIN 表数量   | 10      |
| 最大子查询层数       | 5       |
| 最大 Agent 修复次数 | 2       |
| 最大上下文 Token   | 按模型预算配置 |
| 并发查询数         | 按数据源限流  |

这些是初始保护阈值，不是所有数据库都适用的固定值。生产上线前需要通过压测和数据库容量测试确定。

另外，结果集过大时不能先拉回应用内存再截断，应当在数据库执行层面限制返回量。

# **十二、Agent Architecture：LangGraph 工作流**

不建议一开始设计多个互相协作的自主 Agent。

采用单主 Agent + 专业化节点更容易控制。

## **12.1 主流程**

## **12.2 State 设计**

建议：

```
class Text2SQLState(TypedDict):    query_id: str    conversation_id: str    datasource_id: str    user_question: str    user_context: dict    metadata_release_id: str    intent: dict    schema_context: dict    semantic_context: dict    join_paths: list    query_ir: dict    query_plan: dict    generated_sql: str    validated_sql: str    validation_errors: list    execution_result: dict    repair_count: int    status: str    trace_id: str
```

生产实现还要加入：

- 用户身份。
- 权限上下文。
- 超时 Deadline。
- 取消标记。
- 数据源连接配置引用。
- 数据敏感等级。
- 幂等请求 ID。

不要把密码、Token 等敏感凭证放进 Agent State 或 Checkpoint。

## **12.3 Agent 节点职责**

| Node                | 负责什么          | 是否使用 LLM |
| ------------------- | ------------- | -------- |
| Understanding       | 解析自然语言        | 是        |
| Retrieval           | 检索 Schema     | 可选       |
| Join Expansion      | 展开关系图         | 否        |
| Planning            | 构建 Query Plan | 部分使用     |
| Plan Validation     | 规则校验          | 否        |
| Generation          | 生成 SQL        | 是        |
| AST Validation      | 解析与安全校验       | 否        |
| Semantic Validation | 业务校验          | 规则为主     |
| Execution           | SQL 执行        | 否        |
| Repair              | 错误修复          | 有限使用     |
| Explanation         | 解释结果          | 是        |

这里的关键是：能用确定性程序解决的问题，不要交给 LLM 自由判断。

# **十三、SQL Repair：错误修复与自恢复**

生产级系统不可能一次生成永远正确的 SQL。

但也不能让 Agent 无限循环。

## **13.1 错误分类**

| 错误类型               | 处理                  |
| ------------------ | ------------------- |
| Unknown Column     | 检查 Schema，重新生成      |
| Unknown Table      | 检查 Metadata Version |
| Type Mismatch      | 检查字段类型和转换           |
| Ambiguous Column   | 明确表别名               |
| Invalid Group By   | 修复聚合                |
| Incorrect Join     | 重新规划关联路径            |
| Permission Denied  | 不自动绕过权限             |
| Query Timeout      | 优化或拒绝               |
| Data Too Large     | 限制结果或要求缩小范围         |
| Semantic Ambiguity | 询问用户                |

## **13.2 Repair 策略**

```
SQL Execution Error
       |
       v
Error Classifier
       |
       +-- Schema Error
       |
       +-- Syntax Error
       |
       +-- Type Error
       |
       +-- Semantic Error
       |
       +-- Permission Error
       |
       +-- Resource Error
       |
       v
Targeted Repair
       |
       v
Re-validate
       |
       v
Retry <= 2
```

注意：

- 权限错误不能通过换表绕过。
- 数据过大不能无限重试。
- Schema 已删除时不能通过旧 Embedding 继续猜。
- 业务语义不明确时应询问用户。
- 修复后的 SQL 必须重新经过完整安全校验。

# **十四、Result Validation：结果验证**

这是之前架构中需要特别补充的部分。

很多 Text2SQL 项目只验证：

```
SQL 能不能执行？
```

但生产环境更重要的是：

```
SQL 执行结果是不是用户真正想要的结果？
```

建议增加独立 Result Validator。

## **14.1 验证内容**

- 返回列是否符合 Query IR。
- 结果粒度是否正确。
- 聚合结果是否异常。
- 是否出现意外 NULL。
- 是否出现重复记录。
- 是否违反指标定义。
- 是否存在明显的数量膨胀。
- 是否存在不合理的时间范围。

## **14.2 防止模型编造结果**

最终回答应该严格基于数据库实际返回的数据。

例如：

```
查询结果：
行业 A：120
行业 B：80
行业 C：40
```

Agent 可以总结：

行业 A 的工单数量最多。

但不能在没有查询依据时推断：

行业 A 的工单增长最快。

增长需要同比、环比或时间序列数据。

## **14.3 结果血缘**

每个返回指标建议保留：

```
{
  "metric": "work_order_count",
  "source_tables": [
    "t_work_order"
  ],
  "source_columns": [
    "t_work_order.id"
  ],
  "aggregation": "COUNT(DISTINCT id)",
  "filters": [],
  "metadata_release_id": "release_0015"
}
```

这可以让用户追溯：

- 这个数字从哪里来？
- 用了哪些表？
- 使用了什么过滤条件？
- 指标采用什么统计口径？

# **十五、Evaluation：独立评测体系**

不能只通过人工对话判断系统是否好用。

需要从项目第一阶段就建立 Benchmark。

## **15.1 数据集分层**

建议准备至少五类问题：

| 类别       | 测试内容          |
| -------- | ------------- |
| Simple   | 单表查询          |
| Medium   | 两三张表 JOIN     |
| Complex  | 多层 JOIN       |
| Advanced | 多对多、嵌套聚合、窗口函数 |
| Semantic | 业务指标、时间口径、歧义  |

建议第一版建立 200～500 条经过人工验证的测试问题，重点保证复杂查询的覆盖度，而不是单纯追求数量。

## **15.2 核心指标**

| 指标                  | 说明          |
| ------------------- | ----------- |
| Schema Recall\@K    | 正确表和字段是否被召回 |
| Join Path Accuracy  | 关联路径是否正确    |
| SQL Validity        | SQL 是否合法    |
| Execution Success   | 是否执行成功      |
| Execution Accuracy  | 结果是否正确      |
| Semantic Accuracy   | 是否符合业务口径    |
| Result Accuracy     | 最终答案是否正确    |
| Repair Success Rate | 修复成功率       |
| P50/P95 Latency     | 延迟          |
| Cost per Query      | 单次查询成本      |

其中最重要的是 Execution Accuracy 和 Semantic Accuracy。

不要把 Execution Success 当成最终准确率。

## **15.3 评测方法**

建议采用多种方式：

1. Exact Match：SQL 结构匹配，适用于部分简单任务。
2. Execution Accuracy：比较预测 SQL 和标准 SQL 的结果。
3. Result Equivalence：处理结果顺序、NULL 和浮点误差。
4. Semantic Review：复杂业务口径由人工验证。
5. Safety Evaluation：验证越权和危险查询是否被拦截。

执行结果比较还需要注意：

- 测试数据库数据固定。
- 时间范围固定。
- 排序和分页口径固定。
- 浮点精度容差固定。
- NULL 语义一致。
- 不允许测试 SQL 和预测 SQL 在不同数据快照上比较。

## **15.4 自动回归**

每次修改以下内容都运行 Benchmark：

- Prompt。
- LLM。
- Embedding 模型。
- Reranker。
- Join Graph 算法。
- Query Planner。
- SQL Generator。
- Semantic Rules。

可以进一步建设 Prompt / Model / Retrieval 参数的版本对比平台。

# **十六、Schema Evolution 与 Semantic Evolution 的最终闭环**

这部分将前面的方案统一起来。

系统中有两种不同变化：

**Schema Evolution**

结构变化

- 表新增、删除、重命名
- 字段新增、删除、修改
- 索引、主键、外键变化
- 视图和约束变化

  **Semantic Evolution**

  业务变化
- 指标计算口径变化
- 枚举值含义变化
- 业务术语变化
- 业务关系变化
- 统计时间口径变化

  **统一发布**
  ## **Metadata Release**
  Schema + Join Graph + Semantic + Embedding + Dependency

## **16.1 最终发布流程**

```
DDL / Business Rule Change
           |
           v
    Change Detection
           |
           v
    Change Classification
           |
           v
    Dependency Analysis
           |
           v
    Incremental Build
           |
           v
    Consistency Validation
           |
           v
    Evaluation / Safety Check
           |
           v
    Publish Metadata Release
           |
           v
    Agent Uses New Release
```

## **16.2 数据库变化和 Agent 查询之间的竞态问题**

这是生产上线前必须解决的。

例如：

1. Agent 使用 V14 生成 SQL。
2. DBA 删除了某个字段。
3. Metadata V15 尚未完成发布。
4. Agent 执行旧 SQL。

因此不能只依赖 Metadata Release。

建议增加：

- DDL 变更通知。
- Schema Drift 状态。
- 执行前 Schema Compatibility Check。
- 破坏性变更期间的查询阻断。
- 查询执行时间限制。
- 旧查询的取消策略。

如果业务数据库无法提供可靠的 DDL 变更通知，可以通过定时检测、数据库权限控制和迁移流程约束降低风险，但不能声称完全消除了竞态。

对于高风险生产场景，建议查询只读副本，并通过迁移发布流程管理其 Schema 兼容性。

# **十七、补充的生产级能力**

前面的设计还有一些值得补齐的基础设施。

## **17.1 Data Profiling**

仅靠 Schema 无法充分理解数据库。

建议对关键字段建立数据画像：

| 数据画像               | 示例                |
| ------------------ | ----------------- |
| Row Count          | 100 万行            |
| Null Rate          | 2%                |
| Distinct Count     | 1000              |
| Min / Max          | 2024-01 至 2026-11 |
| Top Values         | status: 1、2、3     |
| Value Distribution | 状态分布              |
| Cardinality        | 关联键匹配率            |

但要注意：

- 不要默认扫描所有大表。
- 对敏感字段进行脱敏。
- 采用采样和统计信息。
- 记录统计时间。
- 设置过期时间。
- 数据画像不能替代业务定义。

例如 `status = 3` 可能是已完成，但数据分布本身无法证明这一点。

## **17.2 SQL Example Management**

历史 SQL 是非常有价值的知识。

建议将已经验证的 SQL 作为独立知识对象：

```
{
  "example_id": "sql_001",
  "question": "查询各行业工单数量",
  "sql": "SELECT ...",
  "tables": [
    "t_work_order",
    "t_industry"
  ],
  "business_domain": "work_order",
  "status": "VERIFIED",
  "metadata_release_id": "release_0015"
}
```

但历史 SQL 不能永远被认为正确。

当其依赖字段或业务规则发生变化时，应该：

1. 检查依赖。
2. 标记失效。
3. 重新验证。
4. 通过后再恢复检索。

## **17.3 Views 和复杂 SQL**

不要遗漏数据库视图。

视图可能是企业业务逻辑的重要载体。

需要采集：

- View Definition。
- 依赖表。
- 依赖字段。
- 是否可查询。
- 权限。
- 视图版本。

对于复杂视图，可以将其作为逻辑实体提供给 Planner，而不要求 Agent 每次展开底层全部表。

存储过程和触发器则建议先采集定义和依赖，第一版不允许 Agent 自由调用。

## **17.4 多租户和多数据源**

建议所有元数据和检索对象都有：

```
tenant_id
datasource_id
database_name
schema_name
object_id
release_id
```

任何检索和执行都必须在租户、数据源、权限和版本范围内进行。

多数据源不代表第一版就需要跨数据库 JOIN。可以先支持多个独立数据源，再研究跨源查询。

## **17.5 多轮对话**

例如：

用户：

> 查询最近 30 天各行业工单数量。

接着：

> 只看已完成的。

再接着：

> 按数量从高到低排序。

Agent 应维护 Query IR，而不是仅把所有历史对话拼到 Prompt 中。

```
Query IR V1
  metrics: work_order_count
  dimensions: industry
  time: last 30 days

Query IR V2
  + filter: status = completed

Query IR V3
  + sort: work_order_count DESC
```

如果后续 Schema Release 变化，旧 Query IR 也需要检查兼容性。

## **17.6 缓存**

建议设计三层缓存：

| 缓存                 | Key                                  | 失效条件         |
| ------------------ | ------------------------------------ | ------------ |
| Schema Cache       | datasource + release                 | Release 更新   |
| Retrieval Cache    | query + release + permission         | 版本或权限变化      |
| Query Result Cache | SQL hash + parameters + data version | 数据更新或 TTL 到期 |

结果缓存不能只依赖 SQL Hash。

还需要考虑：

- 用户权限。
- 租户。
- 参数。
- 数据更新情况。
- 数据库快照。
- 脱敏策略。

否则可能产生跨用户的数据泄露。

# **十八、工程目录最终设计**

建议最终工程采用如下结构：

```
text2sql-agent/
│
├── apps/
│   ├── api/
│   │   ├── main.py
│   │   ├── routes/
│   │   └── middleware/
│   │
│   ├── worker/
│   │   ├── schema_sync.py
│   │   ├── embedding_worker.py
│   │   └── evaluation_worker.py
│   │
│   └── frontend/
│
├── src/
│   │
│   ├── agent/
│   │   ├── graph.py
│   │   ├── state.py
│   │   ├── nodes/
│   │   ├── checkpoints/
│   │   └── policies/
│   │
│   ├── metadata/
│   │   ├── collector/
│   │   ├── catalog/
│   │   ├── evolution/
│   │   ├── versioning/
│   │   ├── dependency/
│   │   └── publisher/
│   │
│   ├── retrieval/
│   │   ├── bm25/
│   │   ├── vector/
│   │   ├── hybrid/
│   │   ├── fusion/
│   │   └── reranker/
│   │
│   ├── graph/
│   │   ├── models/
│   │   ├── builder/
│   │   ├── path_finder/
│   │   ├── cardinality/
│   │   └── scorer/
│   │
│   ├── semantic/
│   │   ├── terms/
│   │   ├── metrics/
│   │   ├── dimensions/
│   │   ├── business_rules/
│   │   └── validation/
│   │
│   ├── planner/
│   │   ├── query_ir.py
│   │   ├── understanding/
│   │   ├── join_planner.py
│   │   ├── aggregation_planner.py
│   │   ├── filter_planner.py
│   │   ├── time_planner.py
│   │   └── plan_validator.py
│   │
│   ├── sql/
│   │   ├── generator/
│   │   ├── parser/
│   │   ├── dialect/
│   │   ├── ast/
│   │   ├── validator/
│   │   └── optimizer/
│   │
│   ├── execution/
│   │   ├── gateway.py
│   │   ├── connection_pool.py
│   │   ├── explain.py
│   │   ├── cost_guard.py
│   │   ├── result_validator.py
│   │   └── result_masking.py
│   │
│   ├── security/
│   │   ├── authentication.py
│   │   ├── authorization.py
│   │   ├── row_policy.py
│   │   ├── column_policy.py
│   │   └── audit.py
│   │
│   ├── evaluation/
│   │   ├── datasets/
│   │   ├── runner.py
│   │   ├── metrics.py
│   │   ├── comparison.py
│   │   └── reports.py
│   │
│   ├── observability/
│   │   ├── tracing.py
│   │   ├── metrics.py
│   │   └── logging.py
│   │
│   └── common/
│       ├── config.py
│       ├── exceptions.py
│       └── schemas.py
│
├── prompts/
│   ├── understanding.yaml
│   ├── planning.yaml
│   ├── generation.yaml
│   ├── repair.yaml
│   └── explanation.yaml
│
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   ├── evolution/
│   ├── regression/
│   └── evaluation/
│
├── benchmarks/
├── deployment/
│   ├── docker/
│   └── compose.yaml
│
├── docs/
│   ├── architecture.md
│   ├── metadata-model.md
│   ├── join-graph.md
│   ├── semantic-layer.md
│   ├── schema-evolution.md
│   └── security.md
│
├── pyproject.toml
└── README.md
```

这里是逻辑模块目录，不意味着每个目录都必须在 MVP 第一周实现。

# **十九、数据库表与版本设计**

建议 PostgreSQL 负责存储系统自身的数据，而业务数据库保持独立。

核心业务表分为六组：

| 领域    | 主要表                                                  |
| ----- | ---------------------------------------------------- |
| 数据源   | datasource、datasource_permission                     |
| 元数据   | metadata_table、metadata_column、metadata_index        |
| 关系    | metadata_relationship、metadata_dependency            |
| 语义    | semantic_metric、semantic_dimension、business_rule     |
| 版本    | schema_snapshot、schema_change_event、metadata_release |
| Agent | query_session、query_execution、query_feedback         |

额外可以维护：

```
query_trace
query_sql_history
query_result_summary
query_evaluation
embedding_record
schema_sync_job
```

### **版本管理建议**

统一发布记录：

```
{
  "release_id": "release_0015",
  "datasource_id": "ds_001",
  "schema_version": 15,
  "semantic_version": 8,
  "join_graph_version": 15,
  "embedding_version": 15,
  "evaluation_status": "PASSED",
  "status": "PUBLISHED",
  "created_at": "2026-11-19T10:00:00"
}
```

注意：各子模块版本可以独立递增，但每个 Release 必须明确记录它们的组合，避免出现无法追溯的混合状态。

# **二十、API 设计**

建议核心 API：

| Method | API                                       | 说明          |
| ------ | ----------------------------------------- | ----------- |
| POST   | `/api/v1/query`                           | 自然语言查询      |
| GET    | `/api/v1/query/{id}`                      | 查询状态及结果     |
| POST   | `/api/v1/query/{id}/continue`             | 多轮追问        |
| POST   | `/api/v1/query/{id}/cancel`               | 取消查询        |
| GET    | `/api/v1/query/{id}/trace`                | 查询 Trace    |
| POST   | `/api/v1/feedback`                        | 用户反馈        |
| GET    | `/api/v1/datasources`                     | 数据源列表       |
| POST   | `/api/v1/datasources`                     | 注册数据源       |
| POST   | `/api/v1/metadata/sync`                   | 同步元数据       |
| GET    | `/api/v1/metadata/releases`               | 版本列表        |
| GET    | `/api/v1/metadata/changes`                | Schema 变更记录 |
| POST   | `/api/v1/metadata/releases/{id}/rollback` | 回退元数据发布指针   |
| GET    | `/api/v1/metrics`                         | 运行指标        |
| POST   | `/api/v1/evaluation/run`                  | 执行评测        |

查询请求：

```
{
  "datasource_id": "ds_001",
  "question": "查询最近30天每个行业的工单数量",
  "conversation_id": "conv_001",
  "options": {
    "auto_execute": true,
    "max_rows": 100,
    "explain_sql": true
  }
}
```

返回内容：

```
{
  "query_id": "q_001",
  "status": "completed",
  "metadata_release_id": "release_0015",
  "sql": "SELECT ...",
  "execution": {
    "success": true,
    "elapsed_ms": 120,
    "row_count": 15
  },
  "data": [],
  "explanation": "统计最近30天各行业工单数量",
  "trace_id": "trace_001"
}
```

对于生产环境，建议增加 `dry_run` 模式：只生成、验证和展示 SQL，不执行真实查询。

# **二十一、部署架构**

第一阶段使用 Docker Compose 即可。

```
Docker Compose
│
├── frontend
│
├── text2sql-api
│   └── FastAPI
│
├── text2sql-worker
│   ├── Schema Sync
│   ├── Embedding
│   └── Evaluation
│
├── postgres
│   ├── Metadata
│   ├── pgvector
│   └── Agent Checkpoints
│
├── redis
│   ├── Cache
│   └── Queue
│
└── monitoring
    ├── OpenTelemetry
    └── Prometheus / Grafana
```

业务 MySQL 独立部署。

建议至少区分：

- Development。
- Staging。
- Production。

生产环境必须有独立只读账号、网络隔离、Secret 管理和审计。

# **二十二、评估指标与 MVP 验收标准**

以下是第一版建议目标，不是未经测试的性能承诺。

| 指标                      | 目标           |
| ----------------------- | ------------ |
| Table Recall\@10        | ≥ 95%        |
| Column Recall\@10       | ≥ 90%        |
| Join Path Accuracy      | ≥ 90%        |
| SQL Execution Success   | ≥ 95%        |
| Result Accuracy         | ≥ 85%        |
| 安全违规执行                  | 0            |
| Trace 覆盖率               | 100%         |
| Schema Change Detection | 目标 5 分钟内发现   |
| Metadata 发布失败回滚         | 100% 保持旧发布可用 |

还需要专门评估：

- 多表 JOIN。
- 一对多聚合。
- 多对多关系。
- 字段重命名。
- 表删除。
- 业务指标变化。
- 跨租户查询。
- 高成本查询。
- Schema 变更与执行并发。

不要只在简单单表查询上获得高分，就认为系统已经达到生产级。

# **二十三、研发实施路线：10 周计划**

我建议调整之前的 8 周计划。

之前的方案偏重于功能开发，但生产级系统必须给 Schema Evolution、Semantic Layer、Evaluation 和安全留出足够时间。

按照 1～2 名开发者、具备模型 API 和测试数据库的条件，建议规划 10 周。

# **10 周研发路线**

从数据库理解到生产级 Text2SQL MVP

1. **第 1 周：工程基础与 Metadata Catalog**
   - 初始化 Python 项目。
   - MySQL 数据源接入。
   - Schema Collector。
   - Table / Column / Index 元数据。
   - Schema Snapshot。

   交付：Metadata Catalog
2. **第 2 周：Schema Evolution**
   - Schema Diff Engine。
   - DDL 变更发现。
   - 依赖分析。
   - 增量同步。
   - Release Version。
   - 原子发布和回滚。

   交付：Metadata CI/CD
3. **第 3 周：Join Graph**
   - 关系数据模型。
   - FK / PK 采集。
   - 历史 SQL 关系提取。
   - Join Path Finder。
   - 多对多关系。
   - Cardinality Analysis。

   交付：Join Intelligence
4. **第 4 周：Hybrid RAG**
   - Table / Column Embedding。
   - BM25。
   - Vector Retrieval。
   - RRF。
   - Reranker。
   - SQL Example Retrieval。

   交付：Schema RAG
5. **第 5 周：Semantic Layer**
   - Business Terms。
   - Metric Definitions。
   - Dimensions。
   - Enum Mapping。
   - Business Rules。
   - Semantic Evolution。

   交付：Semantic Catalog
6. **第 6 周：Query Planner**
   - Intent Understanding。
   - Query IR。
   - Join Planner。
   - Aggregation Planner。
   - Filter Planner。
   - Time Planner。

   交付：Query Planning Engine
7. **第 7 周：SQL Compiler + Validation**
   - SQL Generation。
   - SQLGlot AST。
   - Semantic Validation。
   - EXPLAIN。
   - Cost Guard。

   交付：SQL Engine
8. **第 8 周：Agent + Execution**
   - LangGraph。
   - Agent State。
   - Execution Gateway。
   - SQL Repair。
   - Result Validation。
   - 多轮对话。

   交付：端到端 Agent
9. **第 9 周：Evaluation + Security**
   - Benchmark。
   - Execution Accuracy。
   - 安全测试。
   - 权限控制。
   - Trace。
   - 自动回归。

   交付：质量评估体系
10. **第 10 周：Frontend + Production**
    - 查询工作台。
    - SQL 预览。
    - 结果展示。
    - 查询历史。
    - Docker 部署。
    - 压测和验收。

    交付：Text2SQL MVP

## **23.1 开发优先级**

如果实际时间不够，我建议按以下顺序取舍：

| 优先级 | 模块                | 是否第一版必须         |
| --- | ----------------- | --------------- |
| P0  | Metadata Catalog  | 必须              |
| P0  | Join Graph        | 必须              |
| P0  | Query Planner     | 必须              |
| P0  | SQL Validation    | 必须              |
| P0  | Execution Gateway | 必须              |
| P0  | Schema Evolution  | 必须              |
| P0  | Evaluation        | 必须              |
| P1  | Semantic Layer    | 核心指标必须，复杂治理可以迭代 |
| P1  | Hybrid RAG        | 必须              |
| P1  | Result Validation | 必须              |
| P1  | 多轮对话              | 基础能力            |
| P2  | 自动图表              | 后续              |
| P2  | 跨数据库查询            | 后续              |
| P2  | 模型微调              | 后续              |
| P2  | 自进化 Agent         | 后续              |

# **二十四、最终技术决策清单**

将整个架构收敛为一份最终决策表。

| 技术问题      | 最终决策                              |
| --------- | --------------------------------- |
| 整体架构      | Metadata-Driven Text2SQL          |
| Agent 模式  | 单主 Agent + LangGraph              |
| 数据库理解     | Metadata Catalog                  |
| Schema 更新 | Schema Evolution                  |
| 业务语义      | Semantic Layer                    |
| 表关系       | Join Graph                        |
| 关联路径      | Join Path Finder                  |
| 多表聚合      | Cardinality + Aggregation Planner |
| 检索        | BM25 + Vector + RRF + Reranker    |
| 中间表示      | Query IR                          |
| SQL 生成    | Query Plan → SQL                  |
| SQL 解析    | SQLGlot                           |
| SQL 安全    | AST + Policy                      |
| 执行        | 独立 Execution Gateway              |
| SQL 优化    | EXPLAIN + Cost Guard              |
| 错误修复      | Error Classifier + Limited Repair |
| 结果校验      | Result Validator                  |
| 版本管理      | Immutable Snapshot + Release      |
| 向量数据库     | pgvector                          |
| 元数据存储     | PostgreSQL                        |
| 缓存        | Redis                             |
| 评测        | Execution + Semantic + Safety     |
| 观测        | OpenTelemetry                     |
| 部署        | Docker Compose                    |

# **二十五、我认为还可以进一步研究的三个方向**

这三个方向不是 MVP 的必要条件，但会决定系统后续的技术上限。

## **25.1 Self-Evolving Text2SQL**

让 Agent 能够从失败查询和用户反馈中持续改进。

```
用户查询
   |
   v
SQL 执行
   |
   v
失败 / 用户反馈
   |
   v
Failure Analysis
   |
   v
知识修正候选
   |
   v
Evaluation
   |
   v
人工审批
   |
   v
Metadata / Prompt / Rules 更新
```

注意，不要让 Agent 自动修改生产环境的 Schema、业务指标或权限。

自进化应该先从提出改进建议开始，经过评测后再发布。

## **25.2 Query Understanding Benchmark**

专门建立一个复杂企业数据库查询基准集。

不仅评估 SQL 是否正确，还评估：

- 用户意图是否正确理解。
- 关联表是否完整。
- JOIN 路径是否合理。
- 聚合粒度是否正确。
- 业务指标是否准确。
- 是否应该主动澄清。

这可以成为项目非常有价值的技术资产。

## **25.3 Text2SQL 与 Agentic Analytics**

未来可以从单纯的 SQL 问答演进到多步骤数据分析：

用户：

> 最近一个季度工单完成率为什么下降？

Agent：

1. 查询季度完成率。
2. 对比历史季度。
3. 按行业拆分。
4. 按部门拆分。
5. 定位主要下降来源。
6. 生成分析结论。
7. 展示证据和 SQL。

这已经不再是简单的 Text2SQL，而是 Agentic Analytics。

# **二十六、最终结论：这套系统最值得投入的地方**

我把整个项目归纳为五大核心竞争力：

# **五大核心引擎**

**1. Database Intelligence**

基础

Metadata Catalog + Schema Evolution + Data Profiling

让 Agent 真正理解数据库。

**2. Join Intelligence**

Join Graph + Cardinality + Join Planner

解决复杂多表关联和统计膨胀问题。

**3. Semantic Intelligence**

Business Terms + Metrics + Rules

解决业务含义和指标口径问题。

**4. Query Intelligence**

Hybrid RAG + Query IR + SQL Compiler

将自然语言转换为可靠的结构化查询。

**5. Reliability Intelligence**

Validation + Execution + Evaluation + Observability

保证查询安全、正确、可追溯、可持续改进。

我的最终判断：

**真正有技术壁垒的生产级 Text2SQL，不是让大模型更擅长写 SQL，而是建立一套让大模型能够可靠使用数据库知识的基础设施。**

如果是我来负责这个项目，会优先把下面这条主链路打通：

```
Schema Catalog
      ↓
Schema Evolution
      ↓
Join Graph + Semantic Layer
      ↓
Hybrid Retrieval
      ↓
Query IR + Query Planner
      ↓
SQL Generation
      ↓
AST + Semantic Validation
      ↓
Secure Execution
      ↓
Result Validation
      ↓
Evaluation & Feedback
```

这条链路是整个系统的核心，其他功能都围绕它建设。

**还有一个研发上的建议：先选择一个真实业务数据库，最好有 30～100 张存在复杂关联的表，构造 200～500 条真实业务问题作为 Benchmark。**

不要一开始就追求支持所有数据库、所有 SQL 方言和所有 Agent 能力。先证明系统能在一个真实企业数据库上稳定解决复杂查询，再逐步扩展。

这样做出来的项目，不仅是一个 Text2SQL Demo，而是一套可以继续演进为企业级 AI 数据分析平台的架构。
