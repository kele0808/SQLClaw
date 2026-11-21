# 02 Worker：从队列领任务并执行

对应代码：`src/text2sql/jobs/worker.py`、`apps/worker/main.py`（Plan 01 Task 9）。前置阅读：[01 基于 PostgreSQL 的任务队列](01-job-queue.md)。

## 先打个比方

把服务想成一家打印店：

- 前台（API）收到客户的活，写成一张单子丢进待办箱（`jobs` 表），这就是 `enqueue`；
- 后面有几个店员（worker），每人都在重复同一件事：从箱子里拿一张单子，把活干完，在单子上写"完成"或"失败"；
- 箱子空了，店员歇几秒再来看；
- 下班铃响了（Ctrl-C 或停容器），店员先把手上这张干完再走。

`worker.py` 写的就是"店员"的工作规则。

## 骨架：其实就是一个循环

```python
while 没有收到下班信号:
    job = 从 jobs 表领一个任务()        # claim
    if 没领到:
        等几秒
        continue
    函数 = 根据 job.kind 找到对应的处理函数
    try:
        函数(job.payload)               # 真正干活
        标记成功()                      # complete
    except 出错:
        标记失败()                      # fail，由它决定是否重试
```

文件里的其余代码，都是让这个骨架更健壮。

## 1. 处理函数注册表：`_HANDLERS` 和 `@register`

```python
_HANDLERS: dict[str, Handler] = {}   # 通讯录：任务类型 -> 处理函数

@register("noop")
def noop(payload: dict[str, Any]) -> None: ...
```

- `Handler = Callable[[dict[str, Any]], None]` 是类型别名，意思是"接收一个字典参数、不返回值的函数"。
- `@register("noop")` 是**装饰器**：Python 定义完 `noop` 后，会自动执行 `noop = register("noop")(noop)`。`register` 内部把函数登记进 `_HANDLERS["noop"]`，然后原样返回这个函数。效果等价于手写一行 `_HANDLERS["noop"] = noop`。
- 同一个 kind 重复注册会抛 `ValueError`，防止两个模块抢同一个名字。
- 以后新增任务类型只需要写 `@register("xxx")` 加一个函数，worker 本身一行都不用改。

## 2. 处理一个任务：`run_once`

1. **短事务领任务**：在 `with session_scope()` 里调用 `claim`，把需要的字段（id、kind、trace_id、payload）复制出来，然后马上提交、关掉会话。没领到就返回 `False`。
   - 为什么要马上提交？处理函数可能跑很久，这期间如果一直开着事务，就会一直占着数据库连接和行锁。任务已经被标成 `running`，并且有 `locked_until` 租约，别人领不走，所以不需要一直锁着。
   - 也因为 PostgreSQL 的 `now()` 是事务开始的时间，每次 claim 都应该用一个新事务。
2. **恢复 trace_id**：`set_trace_id(trace_id or new_trace_id())`。处理函数打出的日志会带上入队时那个请求的追踪号，API 日志和 worker 日志能串起来。
3. **找处理函数**：在 `_HANDLERS` 里找不到对应的 kind，就记为不可重试的失败。
4. **执行**，按结果分三种情况：

   | 结果 | 处理 |
   | --- | --- |
   | 正常返回 | `complete`，记录 `job_succeeded` 和耗时 |
   | 抛出 `NonRetryableJobError` | `fail(..., retryable=False)`，直接失败 |
   | 抛出其他异常 | `fail(..., retryable=True)`，由队列按指数退避重试 |

   错误信息格式是 `异常类名: 消息`，比如 `RuntimeError: boom`。
5. **`finally` 还原 trace_id**，不影响下一个任务。

语法提示：`try / except / else / finally` 中，`else` 只在没有异常时执行，`finally` 无论如何都会执行。

## 3. 主循环：`run_worker`

```python
while not stop_event.is_set():           # 没听到下班铃
    processed = run_once(...)            # 干一单
    if not processed:                    # 箱子是空的
        stop_event.wait(轮询间隔)          # 歇一会儿；铃一响立刻醒
```

- 外面包了一层 `try/except`：某一圈出了意外（比如数据库短暂断开），只记录 `worker_iteration_failed`，然后继续循环，进程不会退出。
- 用 `stop_event.wait()` 而不是 `time.sleep()`：等待期间一收到停止信号就能立刻返回，不用等满整个间隔。
- 两个相关配置：`job_lease_s` 是租约时长，`job_poll_interval_s` 是空闲时的轮询间隔。

## 4. 优雅退出：`stop_event` 和信号

- `threading.Event()` 是一个线程安全的开关，初始是关的：
  - `set()`：打开开关；
  - `is_set()`：查看开关开没开；
  - `wait(秒)`：最多等这么久，开关一打开就立刻返回。
- `install_signal_handlers` 把 SIGINT（Ctrl-C）和 SIGTERM（`docker stop` 或 K8s 停 Pod）的默认行为"直接杀掉进程"，改成"把开关打开"。
- 主循环每一圈都检查开关，所以总是在两单活之间退出，不会把一单活干到一半扔下。

## 5. 启动入口：`main`

依次做这几件事：配置 JSON 日志、创建开关、注册信号处理、记录 `worker_started`（带上已注册的 kinds）、进入 `run_worker`，循环结束后记录 `worker_stopped`。

两种启动方式效果一样：

```
uv run python -m text2sql.jobs.worker
uv run python apps/worker/main.py
```

`worker.py` 末尾的 `if __name__ == "__main__":` 里又 import 了一次 `main`。原因是：用 `python -m` 运行时，这个文件会被当成 `__main__` 模块执行，和 `text2sql.jobs.worker` 是两份独立的模块对象，各自有一份 `_HANDLERS`。重新 import 一次，就能保证用的是包里那份注册表，别的模块通过 `text2sql.jobs.worker.register` 注册的处理函数也都能被看到。

## 测试在验证什么

- `tests/unit/test_worker.py`：把 `claim`、`complete`、`fail` 和 `session_scope` 都替换成假的，不连数据库，只测 worker 的分支逻辑：空闲、成功、不可重试、可重试、找不到处理函数、信号、主循环退出等。
- `tests/integration/test_worker.py` 连真实的 PostgreSQL：
  - **200 个任务、4 个线程**：模拟 4 个店员同时抢同一个箱子，检查每个任务恰好执行一次，最终全部是 `succeeded`；
  - 一直失败的任务，按重试次数重试后变成 `failed`；
  - 不可重试的任务，第一次就失败；
  - 处理函数里拿到的 trace_id 等于入队时的 trace_id。

## 自己动手试一下

终端 1：

```
uv run python -m text2sql.jobs.worker
```

终端 2：

```
docker compose -f deployment/compose.yaml exec pg psql -U t2s -d t2s_system
```

```sql
INSERT INTO jobs (kind) VALUES ('noop');
SELECT id, kind, status, attempts FROM jobs ORDER BY id DESC LIMIT 1;
```

终端 1 会打出 `noop_job` 和 `job_succeeded` 两条日志，表里这条任务的状态变成 `succeeded`。最后在终端 1 按 Ctrl-C，会看到 `worker_stop_requested` 和 `worker_stopped`。

注意：worker 默认读取 `.env` 里的 `T2S_SYSTEM_DB_URL`，要确认它指向 `localhost:5433` 的 `t2s_system`。
