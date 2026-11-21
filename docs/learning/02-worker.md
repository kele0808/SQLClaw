# 02 Worker：从队列领任务并执行

对应代码：`src/text2sql/jobs/worker.py`、`apps/worker/main.py`（Plan 01 Task 9）。
前置阅读：[01 基于 PostgreSQL 的任务队列](01-job-queue.md)，本文用到的 `claim` / `complete` / `fail` 都在那篇里。

整个文件按调用关系分四层：`main` → `run_worker`（循环）→ `run_once`（一个任务）→ `_HANDLERS` 里的处理函数。下面按文件顺序逐段讲。

---

## 1. 类型别名与注册表

```python
Handler = Callable[[dict[str, Any]], None]

_HANDLERS: dict[str, Handler] = {}
```

- `Callable[[参数类型...], 返回类型]` 描述一个函数的签名。`Handler` 表示"接收一个 `dict[str, Any]`、返回 `None` 的函数"。这只是类型标注，运行时没有作用，由 mypy 在检查阶段校验。
- `_HANDLERS` 是模块级字典，key 是 `jobs.kind`，value 是处理函数。名字以 `_` 开头，是 Python 的约定写法，表示"模块内部使用"。测试里会通过 `monkeypatch.setitem(worker._HANDLERS, ...)` 临时塞入假的处理函数。

## 2. `register`：带参数的装饰器

```python
def register(kind: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        if kind in _HANDLERS:
            raise ValueError(f"handler already registered for kind {kind!r}")
        _HANDLERS[kind] = handler
        return handler

    return decorator


@register("noop")
def noop(payload: dict[str, Any]) -> None:
    logger.info("noop_job", extra={"payload_keys": sorted(payload)})
```

`@register("noop")` 分两步执行：

1. `register("noop")` 先被调用，返回内部函数 `decorator`。`decorator` 通过闭包记住了 `kind = "noop"`。
2. 再执行 `noop = decorator(noop)`：把 `noop` 写进 `_HANDLERS["noop"]`，然后原样返回。所以 `noop` 本身没有被改变，仍然可以直接调用。

其他细节：

- 重复注册会抛出 `ValueError`。对应的测试是 `test_noop_is_registered_and_duplicates_rejected`。
- `{kind!r}` 是 f-string 里的 `repr()` 格式，字符串会带上引号输出，比如 `'noop'`。
- 注册发生在**模块被 import 的时候**。所以以后新增任务类型时，除了写 `@register(...)`，还要确保那个模块被 import 了。Plan 里写了：后续 spec 要在 `main()` 里加对应的 import。

```python
def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)
```

对字典调用 `sorted()`，得到的是排好序的 key 列表。`run_once` 把它传给 `claim`，只领取当前 worker 能处理的 kind。

## 3. `run_once`：处理一个任务

### 3.1 短事务领取

```python
def run_once(*, lease_s: int) -> bool:
    with session_scope() as session:
        job = claim(session, registered_kinds(), lease_s)
        if job is None:
            return False
        job_id, kind, trace_id = job.id, job.kind, job.trace_id
        payload = dict(job.payload)
```

- 参数里的 `*` 表示 `lease_s` 必须写成 `run_once(lease_s=300)`，不能按位置传。
- `session_scope()`（见 `db/system.py`）是一个上下文管理器：`with` 块正常结束就 commit，出了异常就 rollback，最后 close。
  - 这里 `return False` 发生在 `with` 块内部，也会触发正常退出，所以会 commit 一个空事务。
- 处理函数在 `with` 块**外面**执行，原因有两个：
  1. `claim` 已经把行改成 `running`，并写入了 `locked_until`。commit 之后，这个状态对其他 worker 可见，它们不会再领走这一行。事务不需要一直开着，否则执行慢任务期间会一直占着连接和行锁。
  2. PostgreSQL 的 `now()` 取的是事务开始的时间，每次 claim 都应该在一个新事务里执行。
- `job_id, kind, trace_id = ...` 和 `dict(job.payload)` 把需要的值在 session 关闭前复制成普通变量。之后代码只用这些变量，不再访问 ORM 对象 `job`，避免对象脱离 session 后再去触发数据库访问。`dict(...)` 复制了一份，处理函数修改 payload 也不会影响 ORM 对象。

### 3.2 恢复 trace_id

```python
    token = set_trace_id(trace_id or new_trace_id())
    started = time.perf_counter()
    log_extra: dict[str, Any] = {"job_id": job_id, "kind": kind}
```

- `tracing.py` 里的 `_trace_id` 是一个 `ContextVar`。`set_trace_id` 调用 `_trace_id.set(...)`，返回一个 `Token`，记录的是设置之前的旧值。
- `trace_id or new_trace_id()`：入队时没有 trace_id（比如直接用 SQL 插入的任务），就新生成一个。
- 设置之后，这个线程里所有日志都会被 `logging.py` 的 formatter 自动加上 `trace_id` 字段。对应的测试是集成测试 `test_worker_restores_enqueue_trace_id`。
- `time.perf_counter()` 是单调计时器，专门用来测耗时，不受系统时间被调整的影响。

### 3.3 分派与结果处理

```python
    try:
        handler = _HANDLERS.get(kind)
        if handler is None:
            logger.error("job_handler_missing", extra=log_extra)
            _record_failure(job_id, f"no handler registered for kind {kind!r}", retryable=False)
            return True
        try:
            handler(payload)
        except NonRetryableJobError as exc:
            logger.warning("job_failed_non_retryable", extra=log_extra, exc_info=True)
            _record_failure(job_id, _format_error(exc), retryable=False)
        except Exception as exc:
            logger.exception("job_failed", extra=log_extra)
            _record_failure(job_id, _format_error(exc), retryable=True)
        else:
            with session_scope() as session:
                complete(session, job_id)
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.info("job_succeeded", extra={**log_extra, "duration_ms": duration_ms})
    finally:
        reset_trace_id(token)
    return True
```

逐段看：

- `_HANDLERS.get(kind)`：key 不存在时返回 `None`，不会抛出 `KeyError`。正常情况下不会找不到，因为 `claim` 只领取已注册的 kind，这里是防御性检查。
- 内层 `try` 的三个分支：
  - **`except NonRetryableJobError`**：必须写在 `except Exception` 前面。`except` 从上往下匹配，`NonRetryableJobError` 是 `Exception` 的子类，顺序反了就永远进不了这个分支。
  - **`except Exception`**：其余所有异常都按可重试处理。`logger.exception(...)` 等价于 `logger.error(..., exc_info=True)`，会把完整堆栈写进日志。这里不捕获 `BaseException`，所以 `KeyboardInterrupt`、`SystemExit` 会继续往外抛，不会被吞掉。
  - **`else`**：只在 `try` 块里没有抛出异常时才执行，所以 `complete` 只会在处理函数成功之后调用。
- 每次写结果都用一个新的 `session_scope()`。`_record_failure` 内部也是这样：

  ```python
  def _record_failure(job_id: int, error: str, *, retryable: bool) -> None:
      with session_scope() as session:
          fail(session, job_id, error, retryable=retryable)
  ```

  `queue.py` 的函数本身不 commit，commit 由这里的 `session_scope` 负责。
- `_format_error` 生成 `"RuntimeError: boom"` 这种格式，`type(exc).__name__` 取的是异常类名。集成测试里断言的就是 `"NonRetryableJobError: datasource mismatch"`。
- `{**log_extra, "duration_ms": ...}`：`**` 把 `log_extra` 展开，再追加一个键，得到一个新字典，不会修改原来的 `log_extra`。
- 外层 `finally`：不管成功、失败，还是中途 `return True`，都会执行 `reset_trace_id(token)`，把 ContextVar 恢复成设置之前的值。
- 返回值：`False` 表示没领到任务，`True` 表示处理了一个任务（无论成功还是失败）。`run_worker` 根据它决定要不要等待。

## 4. `run_worker`：主循环

```python
def run_worker(stop_event: threading.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            processed = run_once(lease_s=settings.job_lease_s)
        except Exception:
            logger.exception("worker_iteration_failed")
            processed = False
        if not processed:
            stop_event.wait(settings.job_poll_interval_s)
```

- `settings.job_lease_s` 默认是 300 秒，`job_poll_interval_s` 默认是 1.0 秒，见 `common/config.py`。可以用环境变量 `T2S_JOB_LEASE_S` 和 `T2S_JOB_POLL_INTERVAL_S` 覆盖。
- `run_once` 里处理函数的异常已经在内部处理掉了。能逃到这一层的，是 `claim` 或 `complete` 本身的数据库异常，比如 PostgreSQL 重启。这里只记日志、不退出，下一轮会再试一次。
- `processed = False`：出错之后也走等待分支，避免数据库挂着的时候进入无间隔的死循环，把日志刷爆。
- `stop_event.wait(timeout)` 会阻塞，直到事件被 `set()` 或者超时，所以收到停止信号时能立刻醒来。换成 `time.sleep` 的话，最坏要多等一整个轮询间隔。
- 处理完一个任务（`processed` 为 `True`）就不等待，马上领下一个。有积压时，worker 会连续处理，直到队列空了。

## 5. 信号处理

```python
def install_signal_handlers(stop_event: threading.Event) -> None:
    def request_stop(signum: int, frame: FrameType | None) -> None:
        logger.info("worker_stop_requested", extra={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
```

- `signal.signal(信号, 处理函数)` 用来替换某个信号的默认行为。SIGINT（Ctrl-C）默认会抛出 `KeyboardInterrupt`；SIGTERM（`docker stop`、K8s 删除 Pod 时发出）默认会直接终止进程。
- 替换之后，两个信号都只做一件事：`stop_event.set()`。当前正在执行的 `run_once` 会正常跑完，包括写回 `complete` 或 `fail`；`while not stop_event.is_set()` 在下一轮检查时退出。
- 处理函数的签名 `(signum, frame)` 是 `signal` 模块规定的。`frame` 是收到信号时的栈帧，这里用不到。
- Python 只允许在主线程里注册信号处理函数，所以这件事放在 `main()` 里做。

手动验证时的日志（`signal: 2` 就是 SIGINT）：

```
{"msg": "worker_started", "kinds": ["noop"], ...}
{"msg": "worker_stop_requested", "signal": 2, ...}
{"msg": "worker_stopped", ...}
```

## 6. `main` 与两个入口

```python
def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    logger.info("worker_started", extra={"kinds": registered_kinds()})
    run_worker(stop_event)
    logger.info("worker_stopped")


if __name__ == "__main__":
    from text2sql.jobs.worker import main as package_main

    package_main()
```

- 直接执行 `python -m text2sql.jobs.worker` 时，这个文件是作为 `__main__` 模块运行的。Python 会把它和 `text2sql.jobs.worker` 当成**两个不同的模块对象**，各自有一份 `_HANDLERS`。
- 别的模块是通过 `from text2sql.jobs.worker import register` 注册处理函数的，注册进的是 `text2sql.jobs.worker` 那一份。所以这里要重新 import 包里的 `main` 来执行，保证用的是同一份注册表。
- `apps/worker/main.py` 是另一个入口：

  ```python
  from text2sql.jobs.worker import main

  if __name__ == "__main__":
      main()
  ```

  它天然导入的就是包里那份，所以没有上面的问题。

## 7. 测试是怎么写的

### 单元测试：`tests/unit/test_worker.py`

不连数据库，用 `monkeypatch.setattr` 把 worker 模块里的依赖换成假的：

```python
monkeypatch.setattr(worker, "session_scope", fake_scope)
monkeypatch.setattr(worker, "claim", fake_claim)
monkeypatch.setattr(worker, "complete", fake_complete)
monkeypatch.setattr(worker, "fail", fake_fail)
```

能这样替换，是因为 `worker.py` 用的是 `from ... import claim`，名字 `claim` 绑定在 `worker` 模块上。替换 `worker.claim`，就替换了 `run_once` 里实际调用的那个函数。假函数把调用参数记录到 `Calls` 里，测试再断言：成功时调用了 `complete`，各种失败分支调用 `fail` 时传的 `retryable` 是否正确。

### 集成测试：200 个任务、4 个线程

```python
def drain() -> None:
    while worker.run_once(lease_s=300):
        pass

threads = [threading.Thread(target=drain) for _ in range(4)]
...
assert executed == Counter({n: 1 for n in range(200)})
assert [tuple(row) for row in rows] == [("succeeded", 1, 200)]
```

- 4 个线程并发调用 `run_once`，直到返回 `False`，也就是队列空了。
- `counting_noop` 用 `Counter` 统计每个 `payload["n"]` 被执行了几次。因为多线程会同时修改它，所以用 `threading.Lock` 保护。
- 第一个断言：200 个任务每个都恰好执行了 1 次。依赖的是 `claim` 里的 `FOR UPDATE SKIP LOCKED`。
- 第二个断言：按 `(status, attempts)` 分组，只有一组 `("succeeded", 1, 200)`，说明没有任务被重复领取，也没有失败的。
- ContextVar 在每个线程里是独立的，所以 4 个线程各自 `set_trace_id`，不会互相覆盖。

## 8. 动手验证

```
# 终端 1
uv run python -m text2sql.jobs.worker

# 终端 2
docker compose -f deployment/compose.yaml exec pg psql -U t2s -d t2s_system
```

```sql
INSERT INTO jobs (kind) VALUES ('noop');
SELECT id, kind, status, attempts, last_error FROM jobs ORDER BY id DESC LIMIT 1;
```

- `payload`、`status`、`run_after` 都有 `server_default`，所以插入时只给 `kind` 就够了。
- 终端 1 会依次打出 `noop_job` 和 `job_succeeded`，查询结果是 `succeeded`。
- 把 kind 换成一个没有注册的值，比如 `'xxx'`：`claim` 只领取已注册的 kind，所以这条任务会一直停在 `queued`。

注意：worker 读的是 `.env` 里的 `T2S_SYSTEM_DB_URL`，需要指向 `localhost:5433/t2s_system`。
