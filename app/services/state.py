import ast
import contextlib
import copy
import json
import os
import tempfile
import threading
import time
from abc import ABC, abstractmethod

from redis.exceptions import ResponseError
from itertools import islice

from app.config import config
from app.models import const
from app.utils import utils

try:  # Windows 上没有 fcntl，退化为仅进程内加锁
    import fcntl
except ImportError:  # pragma: no cover - 平台差异
    fcntl = None


_PATCH_EXISTING_TASK_SCRIPT = """
-- Match discovery/get_task ownership checks inside the same atomic operation.
-- Other services and our queue may share this database or reuse a deleted key.
if redis.call("TYPE", KEYS[1]).ok ~= "hash" then
    return 0
end
if redis.call("HGET", KEYS[1], "task_id") ~= KEYS[1] then
    return 0
end

for index = 1, #ARGV, 2 do
    redis.call("HSET", KEYS[1], ARGV[index], ARGV[index + 1])
end

return 1
"""


# Base class for state management
class BaseState(ABC):
    @abstractmethod
    def update_task(self, task_id: str, state: int, progress: int = 0, **kwargs):
        pass

    @abstractmethod
    def get_task(self, task_id: str):
        pass

    @abstractmethod
    def get_all_tasks(self, page: int, page_size: int):
        pass

    @abstractmethod
    def list_task_ids(self, scan_count: int = 100) -> list[str]:
        """获取一次遍历中的任务 ID，供启动恢复等全量操作使用。"""
        pass

    @abstractmethod
    def patch_task(self, task_id: str, **kwargs) -> bool:
        """只更新已有任务的指定字段；任务不存在时返回 False。"""
        pass


# Memory state management
class MemoryState(BaseState):
    def __init__(self):
        self._tasks = {}
        self._lock = threading.RLock()

    def get_all_tasks(self, page: int, page_size: int):
        start = (page - 1) * page_size
        end = start + page_size
        with self._lock:
            total = len(self._tasks)
            tasks = [
                copy.deepcopy(task)
                for task in islice(self._tasks.values(), start, end)
            ]
        return tasks, total

    def list_task_ids(self, scan_count: int = 100) -> list[str]:
        with self._lock:
            return list(self._tasks)

    def update_task(
        self,
        task_id: str,
        state: int = const.TASK_STATE_PROCESSING,
        progress: int = 0,
        **kwargs,
    ):
        progress = int(progress)
        if progress > 100:
            progress = 100

        with self._lock:
            self._tasks[task_id] = {
                # Keep fields from earlier pipeline stages, matching Redis
                # HSET updates. A progress-only update must not erase the
                # WebUI subject or diagnostic details already stored.
                **self._tasks.get(task_id, {}),
                "task_id": task_id,
                "state": state,
                "progress": progress,
                **copy.deepcopy(kwargs),
            }

    def get_task(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id, None)
            return copy.deepcopy(task) if task is not None else None

    def patch_task(self, task_id: str, **kwargs) -> bool:
        # 异步发布只应补充发布状态，不能覆盖已经保存的视频、字幕等结果。
        # 在同一把锁内完成存在性判断和字段合并，也可避免任务删除后
        # 被后台线程重建。
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.update(copy.deepcopy(kwargs))
            return True

    def delete_task(self, task_id: str):
        with self._lock:
            self._tasks.pop(task_id, None)


# File-backed state management
class FileState(BaseState):
    """
    以磁盘文件持久化任务状态，替代不跨重启的 ``MemoryState``。

    每个任务的状态写入 ``storage/tasks/<task_id>/state.json``，与任务产物同目录，
    随任务目录一同删除。写入采用「临时文件 + 原子替换」，并进行进程内加锁和
    （类 Unix 平台的）``flock`` 跨进程加锁，避免 API 与 WebUI 两个进程并发
    读改写同一任务时互相覆盖。

    ``created_at`` 在任务首次写入时补一个 Unix 秒时间戳，用于列表稳定排序
    （新的在前），同时供前端展示创建时间。
    """

    _STATE_FILENAME = "state.json"
    _LOCK_FILENAME = ".state.lock"

    def __init__(self, root: str | None = None):
        self._root = root or utils.storage_dir("tasks", create=True)
        self._lock = threading.RLock()
        self._lock_path = os.path.join(self._root, self._LOCK_FILENAME)

    @staticmethod
    def _is_safe_task_id(task_id: str) -> bool:
        # 任务 ID 来自 URL 路径，必须阻止其逃逸出 storage/tasks 目录。
        if not task_id or not isinstance(task_id, str):
            return False
        if task_id in (".", ".."):
            return False
        return "/" not in task_id and "\\" not in task_id and "\x00" not in task_id

    def _task_file(self, task_id: str) -> str:
        return os.path.join(self._root, task_id, self._STATE_FILENAME)

    @contextlib.contextmanager
    def _interprocess_lock(self):
        if fcntl is None:
            yield
            return
        os.makedirs(self._root, exist_ok=True)
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self, task_id: str):
        if not self._is_safe_task_id(task_id):
            return None
        try:
            with open(self._task_file(task_id), "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except (ValueError, OSError):
            # 半写入或损坏的文件不应让整个列表/查询失败。
            return None
        if not isinstance(data, dict) or data.get("task_id") != task_id:
            return None
        return data

    def _write(self, task_id: str, data: dict):
        task_dir = os.path.join(self._root, task_id)
        os.makedirs(task_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=task_dir, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                # default=str 兜底非 JSON 原生类型（如 Path），保证状态写入不中断流水线。
                json.dump(data, handle, ensure_ascii=False, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, os.path.join(task_dir, self._STATE_FILENAME))
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def _scan_task_ids(self) -> list[str]:
        try:
            entries = os.listdir(self._root)
        except FileNotFoundError:
            return []
        records: list[tuple[int, str]] = []
        for name in entries:
            data = self._read(name)
            if data is None:
                continue
            try:
                created = int(data.get("created_at"))
            except (TypeError, ValueError):
                created = 0
            records.append((created, name))
        # 创建时间降序；同一秒内按 ID 兜底，保证分页顺序确定。
        records.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [name for _, name in records]

    def get_all_tasks(self, page: int, page_size: int):
        start = (page - 1) * page_size
        end = start + page_size
        with self._lock:
            task_ids = self._scan_task_ids()
            page_ids = task_ids[start:end]
        tasks = []
        for task_id in page_ids:
            task = self.get_task(task_id)
            if task is not None:
                tasks.append(task)
        return tasks, len(task_ids)

    def list_task_ids(self, scan_count: int = 100) -> list[str]:
        with self._lock:
            return self._scan_task_ids()

    def update_task(
        self,
        task_id: str,
        state: int = const.TASK_STATE_PROCESSING,
        progress: int = 0,
        **kwargs,
    ):
        progress = int(progress)
        if progress > 100:
            progress = 100
        with self._lock, self._interprocess_lock():
            existing = self._read(task_id) or {}
            merged = {
                **existing,
                "task_id": task_id,
                "state": state,
                "progress": progress,
                **copy.deepcopy(kwargs),
            }
            merged.setdefault("created_at", int(time.time()))
            self._write(task_id, merged)

    def get_task(self, task_id: str):
        with self._lock:
            task = self._read(task_id)
        return copy.deepcopy(task) if task is not None else None

    def patch_task(self, task_id: str, **kwargs) -> bool:
        if not kwargs:
            return False
        with self._lock, self._interprocess_lock():
            task = self._read(task_id)
            if task is None:
                return False
            task.update(copy.deepcopy(kwargs))
            self._write(task_id, task)
            return True

    def delete_task(self, task_id: str):
        if not self._is_safe_task_id(task_id):
            return
        with self._lock, self._interprocess_lock():
            try:
                os.remove(self._task_file(task_id))
            except (FileNotFoundError, NotADirectoryError):
                pass


# Redis state management
class RedisState(BaseState):
    """
    Redis-backed task state.

    Trust boundary: Redis is expected to be private to this application. Task
    values are written by MoneyPrinterTurbo and converted back from strings for
    compatibility with existing state records. Do not expose this Redis database
    to untrusted writers without replacing deserialization with a stricter
    schema-based format.
    """

    def __init__(self, host="localhost", port=6379, db=0, password=None):
        import redis

        self._redis = redis.StrictRedis(host=host, port=port, db=db, password=password)

    def get_all_tasks(self, page: int, page_size: int):
        start = (page - 1) * page_size
        end = start + page_size
        # 每一页都以同一套确定性顺序切片，而不是依赖 SCAN 的返回顺序。
        # 这仍不是并发增删任务时的事务快照；无索引时优先保证静态任务集
        # 的分页正确性，并让 total 统计去重后的任务键。
        task_ids = self.list_task_ids(scan_count=page_size)
        tasks = []
        for task_id in task_ids[start:end]:
            task = self.get_task(task_id)
            if task is not None:
                tasks.append(task)
        return tasks, len(task_ids)

    def list_task_ids(self, scan_count: int = 100) -> list[str]:
        """Return only this application's task hashes from a possibly shared DB."""
        task_keys = set()
        cursor = 0
        while True:
            # Redis 数据库中除了任务 Hash，还可能存在 RedisTaskManager 使用的
            # List 队列。只扫描 Hash 可以避免对队列执行 HGETALL 时触发
            # WRONGTYPE。COUNT 是扫描工作量提示，不保证每批返回的数量。
            cursor, keys = self._redis.scan(
                cursor,
                count=scan_count,
                _type="HASH",
            )
            # Redis db 0 may also contain hashes belonging to other services.
            # Check the task marker in one pipelined round trip per SCAN batch;
            # exposing all HASH keys here can leak their fields through /tasks.
            candidates = [key for key in dict.fromkeys(keys) if key not in task_keys]
            if candidates:
                with self._redis.pipeline(transaction=False) as pipeline:
                    for key in candidates:
                        pipeline.hget(key, "task_id")
                    # A key can change type after SCAN. Isolate that row instead
                    # of letting one WRONGTYPE abort the whole task listing.
                    embedded_ids = pipeline.execute(raise_on_error=False)
                for key, embedded_id in zip(candidates, embedded_ids):
                    if isinstance(embedded_id, ResponseError):
                        if str(embedded_id).startswith("WRONGTYPE"):
                            continue
                        raise embedded_id
                    if embedded_id == key:
                        task_keys.add(key)
            if cursor == 0:
                break
        # 按任务键排序不依赖 Hash 扫描顺序；不额外维护索引，也不改变旧任务。
        return [key.decode("utf-8") for key in sorted(task_keys)]

    def update_task(
        self,
        task_id: str,
        state: int = const.TASK_STATE_PROCESSING,
        progress: int = 0,
        **kwargs,
    ):
        progress = int(progress)
        if progress > 100:
            progress = 100

        fields = {
            "task_id": task_id,
            "state": state,
            "progress": progress,
            **kwargs,
        }

        # One HSET writes the whole task state atomically. Separate commands
        # could expose a new state with the previous progress or result fields
        # to readers, and leave a partially updated record on network failure.
        self._redis.hset(
            task_id,
            mapping={
                field: self._serialize_field(field, value)
                for field, value in fields.items()
            },
        )

    def get_task(self, task_id: str):
        try:
            task_data = self._redis.hgetall(task_id)
        except ResponseError as exc:
            # Arbitrary task IDs can name the application's List queue or
            # another service's non-hash key. Those are not task records.
            if str(exc).startswith("WRONGTYPE"):
                return None
            raise
        # An API caller may ask for any Redis key by name. Require the same
        # marker as list_task_ids before returning a hash's contents.
        if not task_data or task_data.get(b"task_id") != task_id.encode("utf-8"):
            return None

        task = {
            key.decode("utf-8"): (
                value.decode("utf-8")
                if key == b"task_id"
                else self._convert_to_original_type(value)
            )
            for key, value in task_data.items()
        }
        return task

    def patch_task(self, task_id: str, **kwargs) -> bool:
        if not kwargs:
            return False

        arguments = []
        for field, value in kwargs.items():
            arguments.extend((field, self._serialize_field(field, value)))

        # EXISTS 和 HSET 如果分成两条命令，后台发布线程与删除请求并发时，
        # HSET 可能在删除后重新创建一条残缺任务。Lua 脚本由 Redis 原子执行，
        # 可以保证任务不存在时不写入，且不会改变现有字段之外的数据。
        updated = self._redis.eval(
            _PATCH_EXISTING_TASK_SCRIPT,
            1,
            task_id,
            *arguments,
        )
        return bool(updated)

    def delete_task(self, task_id: str):
        self._redis.delete(task_id)

    @staticmethod
    def _serialize_field(field, value):
        # Quote strings so literal_eval cannot turn a subject like "2026" or
        # an error like "None" into an integer/None. Keep the ownership marker
        # raw: task discovery compares its bytes directly against the Redis key.
        if isinstance(value, str) and field != "task_id":
            return repr(value)
        return str(value)

    @staticmethod
    def _convert_to_original_type(value):
        """
        Convert values written by this application back to common Python types.

        This compatibility parser assumes Redis is inside the application's
        trust boundary. If Redis can be written by untrusted clients, task state
        should move to a strict JSON/schema parser instead of open-ended literal
        conversion.
        """
        value_str = value.decode("utf-8")

        try:
            # try to convert byte string array to list
            return ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            pass

        if value_str.isdigit():
            return int(value_str)
        # Add more conversions here if needed
        return value_str


# Global state
_enable_redis = config.app.get("enable_redis", False)
_enable_file_state = config.app.get("enable_file_state", True)
_redis_host = config.app.get("redis_host", "localhost")
_redis_port = config.app.get("redis_port", 6379)
_redis_db = config.app.get("redis_db", 0)
_redis_password = config.app.get("redis_password", None)

# 优先级：Redis > 文件持久化 > 纯内存。默认用文件持久化，使任务列表跨重启保留；
# 仅在显式 enable_file_state=false 时回退到不持久化的 MemoryState。
if _enable_redis:
    state = RedisState(
        host=_redis_host, port=_redis_port, db=_redis_db, password=_redis_password
    )
elif _enable_file_state:
    state = FileState()
else:
    state = MemoryState()
