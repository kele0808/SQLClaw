# Spec 04：Join Graph 关联图

- 状态：已确认（2026-11-20）
- 日期：2026-11-20
- 对应架构文档：第六章（6.2～6.6 及修订）、10.3（Edge / Fan-out Validation）、1.4（Join 关系确认人）、第二十三章（第 2 周）
- 依赖：Spec 01、Spec 02（`SchemaSnapshot`、`metadata_release`、`StaleSet`、对象键）、Spec 03（合成库与 `required_tables`，用于验收）
- 被依赖：05（维度关联路径引用边 ID）、06（Join Planner、预聚合）、07（JOIN 条件与扇出校验）、08（兜底通道的允许边、Join Expansion）、09（影响分析）

## 1. 目标

用**人工确认**的关联边构建 Join Graph，提供在 VERIFIED 子图上的路径查找、扇出判定和"按列匹配边"的查询能力。第 2 周交付：任意验收题的 `required_tables` 都能在 VERIFIED 子图上连通。

## 2. 范围

### 2.1 本期做

| 编号 | 能力 | 说明 |
| --- | --- | --- |
| J1 | 边的数据模型 | 规范方向（指向唯一侧）、`join_role`、证据、状态 |
| J2 | 边定义文件 | `semantic_definitions/<model>/joins.yaml`，走 Pull Request 审核 |
| J3 | 候选边生成 | 只由**外键**生成候选，输出为待确认的 YAML 片段 |
| J4 | 加载与校验 | 对照快照校验存在性与基数证据（6.2 修订的判定表） |
| J5 | 版本存储 | `join_graph_version` 不可变记录，挂到 release |
| J6 | 路径查找 | VERIFIED 子图上连通一组表，打分排序，语义不同的路径返回"歧义" |
| J7 | 扇出判定 | 每一步是否扇出、是否存在多条独立扇出分支（chasm trap） |
| J8 | 按列匹配边 | 给定 JOIN 条件的列对，找到对应的 VERIFIED 边及方向 |
| J9 | CLI | 生成候选、校验、查看路径 |

### 2.2 本期不做

- 根据命名相似度、数据采样、历史 SQL 生成候选边（架构 6.2、17.1，阶段 2；23.1 明确 MVP 不做自动推断关系）
- 确认页面（MVP 用 YAML + PR 审核代替，原因见 4.2）
- 没有桥接表的直接 MANY_TO_MANY 边（必须拆成桥接表上的两条边）
- 依赖图持久化（架构 5.4，阶段 2；MVP 的影响分析在 Spec 09 中按 release 内容即时计算）

## 3. 目录与文件

```
semantic_definitions/ticket_demo/joins.yaml      # J2 合成库的边定义
src/text2sql/graph/
├── __main__.py / cli.py     # J9
├── models.py                # J1 JoinEdge、JoinGraph
├── loader.py                # J4
├── candidates.py            # J3
├── path_finder.py           # J6
├── cardinality.py           # J7
├── scorer.py                # J6 打分
└── store.py                 # J5
migrations/versions/0004_join_graph.py
tests/unit/graph/…
tests/integration/graph/…
```

## 4. 详细设计

### 4.1 配置

`Settings` 新增：

| 字段 | 环境变量 | 类型 | 默认值 |
| --- | --- | --- | --- |
| `semantic_dir` | `T2S_SEMANTIC_DIR` | `Path` | `Path("semantic_definitions/ticket_demo")` |

`semantic_dir` 是一个"语义模型"目录，Join 边（本 Spec）和语义定义（Spec 05）都在其中。同一个模型可用于结构相同的多个数据源（例如 `local_mysql57` 与 `local_mysql80`），加载时对照各自的快照校验。

### 4.2 J1/J2 边模型与 YAML

**规范方向**：每条边的 `to` 侧必须是唯一侧（PK/UK 或人工确认唯一）。因此 `relation_type` 只有两种：

| `relation_type` | 含义 | 反向遍历（to → from） |
| --- | --- | --- |
| `MANY_TO_ONE` | from 多、to 一 | 扇出（1:N） |
| `ONE_TO_ONE` | 两侧都唯一 | 不扇出 |

架构 6.2 中的 `ONE_TO_MANY` 通过交换 from/to 表示为 `MANY_TO_ONE`；`MANY_TO_MANY` 必须通过桥接表的两条 `MANY_TO_ONE` 表示。这样"是否扇出"只取决于遍历方向，判定规则简单且无歧义。

`joins.yaml`：

```yaml
version: 1
bridge_tables: [t_work_order_tag]
edges:
  - id: work_order_customer
    from: {table: t_work_order, columns: [customer_id]}
    to: {table: t_customer, columns: [id]}
    relation_type: MANY_TO_ONE
    join_role: WORK_ORDER_CUSTOMER
    evidence: [FOREIGN_KEY, PRIMARY_KEY]
    status: VERIFIED
    confirmed_by: <Join 关系确认人>
    confirmed_at: 2026-11-30
    description: 工单所属客户
  - id: work_order_sla_policy
    from: {table: t_work_order, columns: [tenant_id, priority]}
    to: {table: t_sla_policy, columns: [tenant_id, priority]}
    relation_type: MANY_TO_ONE
    join_role: WORK_ORDER_SLA
    evidence: [UNIQUE_KEY]
    status: VERIFIED
    confirmed_by: <Join 关系确认人>
    confirmed_at: 2026-11-30
```

```python
Evidence = Literal["FOREIGN_KEY", "PRIMARY_KEY", "UNIQUE_KEY", "HUMAN_CONFIRMED"]
EdgeStatus = Literal["CANDIDATE", "VERIFIED", "DEPRECATED"]

class EdgeEnd(BaseModel, frozen=True):
    table: str
    columns: tuple[str, ...]          # 有序，与对端一一对应

class JoinEdge(BaseModel, frozen=True):
    id: str                           # ^[a-z][a-z0-9_]{2,63}$，全文件唯一
    from_: EdgeEnd                    # YAML 键为 from
    to: EdgeEnd
    relation_type: Literal["MANY_TO_ONE", "ONE_TO_ONE"]
    join_role: str                    # ^[A-Z][A-Z0-9_]{2,63}$
    evidence: tuple[Evidence, ...]
    status: EdgeStatus
    confirmed_by: str | None
    confirmed_at: date | None
    description: str = ""
```

选择 YAML + PR 而不是数据库 + 确认页面：与语义层（架构 7.4）的审核方式一致，确认记录随代码版本可追溯；合成库与真实库切换、本地重建 PG 时不丢失人工确认结果；第 2 周不需要做页面。

合成库 `ticket_demo` 的边（全部由第 2 周确认人确认）：

| `id` | from → to | 类型 | `join_role` | 证据 |
| --- | --- | --- | --- | --- |
| `work_order_customer` | `t_work_order.customer_id → t_customer.id` | N:1 | `WORK_ORDER_CUSTOMER` | FK, PK |
| `work_order_industry` | `t_work_order.industry_id → t_industry.id` | N:1 | `WORK_ORDER_INDUSTRY` | PK, HUMAN_CONFIRMED |
| `customer_industry` | `t_customer.industry_id → t_industry.id` | N:1 | `CUSTOMER_INDUSTRY` | PK, HUMAN_CONFIRMED |
| `work_order_creator` | `t_work_order.creator_id → t_employee.id` | N:1 | `WORK_ORDER_CREATOR` | PK, HUMAN_CONFIRMED |
| `work_order_handler` | `t_work_order.handler_id → t_employee.id` | N:1 | `WORK_ORDER_HANDLER` | PK, HUMAN_CONFIRMED |
| `employee_department` | `t_employee.department_id → t_department.id` | N:1 | `EMPLOYEE_DEPARTMENT` | FK, PK |
| `department_parent` | `t_department.parent_id → t_department.id` | N:1 | `DEPARTMENT_PARENT` | PK, HUMAN_CONFIRMED |
| `process_work_order` | `t_work_process.work_id → t_work_order.id` | N:1 | `PROCESS_WORK_ORDER` | FK, PK |
| `process_operator` | `t_work_process.operator_id → t_employee.id` | N:1 | `PROCESS_OPERATOR` | PK, HUMAN_CONFIRMED |
| `status_log_work_order` | `t_work_order_status_log.work_id → t_work_order.id` | N:1 | `STATUS_LOG_WORK_ORDER` | PK, HUMAN_CONFIRMED |
| `work_order_tag_work_order` | `t_work_order_tag.work_id → t_work_order.id` | N:1 | `TAG_LINK_WORK_ORDER` | PK, HUMAN_CONFIRMED |
| `work_order_tag_tag` | `t_work_order_tag.tag_id → t_tag.id` | N:1 | `TAG_LINK_TAG` | PK, HUMAN_CONFIRMED |
| `work_order_sla_policy` | `t_work_order.(tenant_id, priority) → t_sla_policy.(tenant_id, priority)` | N:1 | `WORK_ORDER_SLA` | UNIQUE_KEY |
| `satisfaction_work_order` | `t_satisfaction.work_id → t_work_order.id` | 1:1 | `SATISFACTION_WORK_ORDER` | UNIQUE_KEY, PK |
| `contact_note_customer` | `t_customer_contact_note.customer_id → t_customer.id` | N:1 | `CONTACT_NOTE_CUSTOMER` | PK, HUMAN_CONFIRMED |

不建 `tenant_id → t_tenant.id` 的边：租户隔离由数据库视图完成（Spec 07），查询不需要关联租户表。

### 4.3 J3 候选边生成

```python
def fk_candidates(snapshot: SchemaSnapshot, existing: Sequence[JoinEdge]) -> list[JoinEdge]: ...
```

- 只处理快照中的外键；已有同列边（任意状态）的跳过。
- 外键引用列集合等于目标表某个唯一列集合（`snapshot.unique_column_sets`）时生成 `MANY_TO_ONE`，证据 `FOREIGN_KEY` + `PRIMARY_KEY`/`UNIQUE_KEY`；外键列本身在源表也唯一时生成 `ONE_TO_ONE`。
- 引用列不唯一的外键（MySQL 允许引用非唯一索引）生成 `status: CANDIDATE`、证据仅 `FOREIGN_KEY`、并加注释"目标列不唯一，需人工确认基数"。
- 生成的边 `status` 一律为 `CANDIDATE`，`join_role` 取 `<FROM表去前缀>_<TO表去前缀>` 的大写形式，`confirmed_by` 为空。
- CLI 把候选写到 `semantic_definitions/<model>/joins.candidates.yaml`（不覆盖 `joins.yaml`）。确认人把候选挪进 `joins.yaml`、补全 `join_role`/`confirmed_by`/`confirmed_at`、改为 `VERIFIED`，然后提 PR。外键以外的关系（本合成库中的大多数）由确认人直接手写。

### 4.4 J4 加载与校验

```python
@dataclass(frozen=True)
class LoadIssue:
    edge_id: str | None
    code: str
    message: str

class JoinGraphLoadError(AppError): issues: tuple[LoadIssue, ...]

def load_join_definitions(path: Path) -> JoinDefinitions: ...    # 只做 YAML 结构校验（pydantic）
def build_join_graph(defs: JoinDefinitions, snapshot: SchemaSnapshot) -> JoinGraph: ...
```

`build_join_graph` 对**所有**边执行以下校验，任一 `VERIFIED` 边失败即抛 `JoinGraphLoadError`（列出全部问题）；`CANDIDATE`/`DEPRECATED` 边的问题只作为警告：

| 校验码 | 规则 |
| --- | --- |
| `UNKNOWN_TABLE` / `UNKNOWN_COLUMN` | 两端表与列在快照中存在 |
| `COLUMN_COUNT_MISMATCH` | 两端列数相同且 ≥ 1 |
| `TYPE_INCOMPATIBLE` | 对应列的数据类型属于同一族（整数族、定点/浮点族、字符串族、日期时间族） |
| `UNIQUENESS_NOT_PROVEN` | `to` 列集合是快照中的唯一列集合，或证据含 `HUMAN_CONFIRMED`；`ONE_TO_ONE` 两端都要满足（架构 6.2 修订：PK/UK 或人工确认是必要条件） |
| `EVIDENCE_MISMATCH` | 证据声明 `PRIMARY_KEY`/`UNIQUE_KEY`/`FOREIGN_KEY` 时，快照中确有对应约束 |
| `CONFIRMATION_MISSING` | `VERIFIED` 边必须有 `confirmed_by`、`confirmed_at` |
| `DUPLICATE_ID` / `DUPLICATE_EDGE` | 边 ID 唯一；同一列对不能定义两次 |
| `BRIDGE_INVALID` | `bridge_tables` 中的表恰有 2 条以它为 `from` 的 VERIFIED 边，且这两条边的 from 列的并集包含该表主键 |

`HUMAN_CONFIRMED` 的边在快照中没有唯一约束证明，校验通过但记录警告 `UNIQUENESS_HUMAN_ONLY`，`graph validate` 会列出这些边，提醒确认人对其负责。

### 4.5 JoinGraph 与 J8 按列匹配

```python
Direction = Literal["FORWARD", "REVERSE"]      # FORWARD = from → to

@dataclass(frozen=True)
class JoinStep:
    edge_id: str
    from_table: str          # 本步的出发表（按遍历方向）
    to_table: str            # 本步的到达表
    direction: Direction
    fans_out: bool           # REVERSE 且 relation_type = MANY_TO_ONE

class JoinGraph:
    version_id: int | None                      # 存储后填写
    def edge(self, edge_id: str) -> JoinEdge: ...                       # 只含 VERIFIED 边，不存在抛 KeyError
    def edges_between(self, a: str, b: str) -> list[JoinEdge]: ...
    def step(self, edge_id: str, from_table: str) -> JoinStep: ...      # 从 from_table 出发走这条边
    def find_edge_by_columns(self, left_table: str, left_columns: Sequence[str],
                             right_table: str, right_columns: Sequence[str]) -> JoinStep | None: ...
    def is_bridge(self, table: str) -> bool: ...
    def tables(self) -> frozenset[str]: ...
    def all_edges(self) -> tuple[JoinEdge, ...]: ...
    def without(self, stale: StaleSet) -> "JoinGraph": ...               # 去掉被标记的边、以及端点列/表被标记的边
```

- 内部用 `networkx.MultiDiGraph`（同一对表可有多条边，例如创建人与处理人），每条 VERIFIED 边加正反两个方向。
- `find_edge_by_columns`：列对按位置配对后作为无序集合匹配（`ON a.x = b.y AND a.z = b.w` 与书写顺序无关）；表名比较遵守快照的 `lower_case_table_names`，列名大小写不敏感。返回从 `left_table` 出发的 `JoinStep`。

### 4.6 J6 路径查找

```python
@dataclass(frozen=True)
class JoinTree:
    root: str
    steps: tuple[JoinStep, ...]      # 按从 root 出发的遍历顺序
    tables: frozenset[str]
    score: float

@dataclass(frozen=True)
class Found:
    trees: tuple[JoinTree, ...]      # 按分数降序，至少 1 个
@dataclass(frozen=True)
class Ambiguous:
    alternatives: tuple[JoinTree, ...]   # 2～3 个语义不同的候选
    differing_roles: tuple[str, ...]
@dataclass(frozen=True)
class NotFound:
    unreachable: tuple[str, ...]
PathResult = Found | Ambiguous | NotFound

def connect_tables(graph: JoinGraph, root: str, targets: Sequence[str], *,
                   role_hints: Collection[str] = (), max_steps: int = 6,
                   max_candidates: int = 5) -> PathResult: ...
def tree_from_edges(graph: JoinGraph, root: str, edge_ids: Sequence[str]) -> JoinTree: ...
```

`connect_tables` 算法（MVP 表规模 ≤ 100，枚举可接受）：

1. 对每个目标表，在 VERIFIED 子图上枚举从 `root` 出发、不重复经过表、长度 ≤ 4 的所有简单路径（不使用 `department_parent` 这类自环边，除非其 `join_role` 在 `role_hints` 中）。
2. 对各目标表的候选路径做组合（每表取前 3 条最短），合并为树（共享前缀合并）；树的总步数 > `max_steps` 的丢弃。
3. 打分（架构 6.3）：`score = w1·R + w2·S + w3·E − w4·L − w5·F`。MVP 中 R 恒为 1（只有 VERIFIED 边）；S = 命中 `role_hints` 的边数 / `role_hints` 数（无提示时为 0）；E = 有约束证据（FK/PK/UK）的边占比；L = 步数；F = 扇出步数。默认权重 `w = (1.0, 2.0, 0.5, 0.3, 1.0)`，放在 `scorer.py` 常量中，用验收集调优。
4. **语义歧义判定**（架构 6.6 修订：语义不同的路径不按分数选）：取最高分的树 T1；若存在另一棵树 T2，其步数与 T1 相同、到达相同的目标表集合、但边集合不同，且两者不同的边的 `join_role` 都不在 `role_hints` 中，则返回 `Ambiguous`（最多 3 个候选，`differing_roles` 为差异边的角色）。否则返回 `Found`。
5. 某目标表不可达 → `NotFound`。

`tree_from_edges`：按给定边 ID 顺序从 `root` 构造树（Spec 05 的维度路径使用），边不连续时抛 `ValueError`。

示例（合成库）：`connect_tables(root="t_work_order", targets=["t_employee"])` 返回 `Ambiguous`（`WORK_ORDER_CREATOR` / `WORK_ORDER_HANDLER`）；带 `role_hints={"WORK_ORDER_HANDLER"}` 返回 `Found`。`targets=["t_industry"]` 时，直达路径 1 步、经客户 2 步，步数不同，返回 `Found` 且首选直达；但维度层面仍由 Spec 05 的默认路径决定并标注（架构 6.6 修订）。

### 4.7 J7 扇出判定

```python
@dataclass(frozen=True)
class FanoutReport:
    fanout_steps: tuple[JoinStep, ...]
    chasm: bool               # 存在两条以上独立的扇出分支

def analyze_fanout(tree: JoinTree) -> FanoutReport: ...
```

- 扇出步 = `fans_out` 为真的步。
- `chasm`：存在两个扇出步，二者在树中不在同一条从 root 出发的链上（例如从工单同时扇出到处理记录和状态日志）。这种情况下任何聚合都会被交叉放大。
- 本函数只给出结构事实。如何处理（预聚合、对主键去重、拒绝）由 Spec 06（编译期）和 Spec 07（对兜底 SQL 的校验）决定。

### 4.8 J5 版本存储

**`join_graph_version`**（不可变，触发器禁止更新删除）：`id` BIGINT identity；`datasource_id`；`snapshot_id` FK（校验时使用的快照）；`model_name`（`semantic_dir` 的目录名）；`content_hash CHAR(64)`（规范化边定义的 sha256）；`edges JSONB`（全部边，含状态）；`created_by`；`created_at`；`trace_id`。唯一 `(datasource_id, snapshot_id, content_hash)`。迁移同时给 `metadata_release.join_graph_version_id` 加外键。

```python
def save_join_graph_version(session: Session, *, datasource_id: str, snapshot_id: int,
                            model_name: str, defs: JoinDefinitions, created_by: str) -> int: ...  # 相同内容返回已有 ID
def load_join_graph(session: Session, version_id: int, snapshot: SchemaSnapshot) -> JoinGraph: ...
```

把 Join Graph 版本挂进 release 的入口是 Spec 05 的 `build_candidate_release`（它同时处理语义层）。本 Spec 第 2 周单独交付时，CLI `graph publish-version` 只保存版本并打印 ID。

### 4.9 J9 CLI

`uv run python -m text2sql.graph <子命令>`（默认使用当前数据源最新快照与 `Settings.semantic_dir`）：

| 子命令 | 作用 |
| --- | --- |
| `candidates` | 由外键生成候选，写 `joins.candidates.yaml` |
| `validate` | 加载并校验 `joins.yaml`，打印问题与 `UNIQUENESS_HUMAN_ONLY` 警告；有错误时退出码 1（可放进 CI） |
| `path ROOT TARGET [TARGET…] [--role ROLE…]` | 打印 `Found`/`Ambiguous`/`NotFound` 及每步方向和扇出 |
| `check-acceptance` | 对验收集每题的 `required_tables`，以第一张表为 root 调用 `connect_tables`，`Ambiguous` 视为可连通，打印不可连通的题 |
| `publish-version --by NAME` | 保存 `join_graph_version` |

## 5. 验收标准

1. `graph validate` 对 `ticket_demo/joins.yaml` 在 `local_mysql57` 与 `local_mysql80` 的快照上都无错误。
2. 校验规则单元测试：4.4 表中每个校验码至少一个失败用例；特别地，`to` 列无唯一约束且无 `HUMAN_CONFIRMED` 的 `MANY_TO_ONE` 边被拒绝（`UNIQUENESS_NOT_PROVEN`）。
3. 合成库共有 3 个外键（`t_work_order.customer_id`、`t_employee.department_id`、`t_work_process.work_id`）：`joins.yaml` 为空时 `candidates` 恰好生成这 3 条候选，状态均为 `CANDIDATE`、类型均为 `MANY_TO_ONE`；对完整的 `joins.yaml` 生成 0 条。
4. `path t_work_order t_employee` 返回 `Ambiguous`，`differing_roles` 为创建人与处理人；加 `--role WORK_ORDER_HANDLER` 返回 `Found`。
5. 从 `t_work_order` 到 `t_work_process` 的步 `fans_out = true`；`t_work_process` 到 `t_work_order` 为 `false`；`t_satisfaction` 双向均为 `false`；同时包含 `t_work_process` 与 `t_work_order_status_log` 的树 `chasm = true`。
6. `find_edge_by_columns("t_sla_policy", ["priority", "tenant_id"], "t_work_order", ["priority", "tenant_id"])` 返回 `work_order_sla_policy` 的 REVERSE 步；列顺序打乱仍能匹配；不存在的列对返回 `None`。
7. `JoinGraph.without(stale)`：标记列 `t_work_order.handler_id` 后，`work_order_handler` 边不可用。
8. `check-acceptance` 对当时已标注的全部验收题无不可连通项（第 2 周交付标准）。
9. `save_join_graph_version` 对相同内容幂等；对 `join_graph_version` 的 UPDATE 被拒绝。
10. `ruff`、`mypy`、`pytest`、`pytest -m integration` 全部通过。

## 6. 新增依赖

- 运行时：`networkx`（架构第三章已选定）
- 开发：`types-networkx`（mypy strict 所需；若不可用则在 mypy 配置中对 `networkx` 单独 `ignore_missing_imports`）

## 7. 风险与说明

- **多数边依赖人工确认**：合成库刻意让大部分关联没有外键，模拟真实 MySQL 业务库（架构 4.1 注意事项）。`UNIQUENESS_HUMAN_ONLY` 的边一旦确认错，会导致计数放大且 SQL 能正常执行；确认人需要对照数据核实（MVP 不做自动采样校验，阶段 2 补"值校验"证据）。
- **规范方向的取舍**：只保留 `MANY_TO_ONE`/`ONE_TO_ONE` 两种类型，YAML 作者需要把"一对多"反过来写。好处是扇出只由遍历方向决定，Spec 06/07 的判定不需要再查类型表。
- **路径枚举的规模假设**：枚举算法按 1.3 待填的表规模（约 100 张以内，架构 8.5 阶段 A）设计；表数到数百张时需要换成带剪枝的 Steiner 树近似，届时另写 Spec。
- **歧义判定只看步数相同的候选**：步数不同但语义也不同的路径（如行业直达与经客户）不会被判为歧义，靠 Spec 05 维度的默认路径和口径标注兜住。
- **开放决策（需用户确认）**：
  1. Join 关系确认人（1.4）是谁；`confirmed_by` 填真实姓名还是账号。
  2. 是否接受"确认记录放在 Git（YAML + PR）"而不是数据库 + 页面。
