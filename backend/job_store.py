"""
job_store —— EPUB 翻译工具的持久任务状态。

`BackgroundService`（MyBooks 核心）的任务记录只保存在进程内存里，进程重启或页面刷新后
无法读回，所以本工具在自己的数据目录下维护一份 `job.json`，作为"当前/最近一个任务"的
唯一权威状态；宿主的后台任务面板（`self.api.tasks.*`）只是把同一份进度顺带展示出去，不
作为决策依据。

设计要点见 `doc/EPUB_TRANSLATOR_IMPLEMENTATION_REVIEW.md` 第 3.2/4.4 节：

- 路径由工具自己拼在 `<TOOL_DATA_ROOT>/<tool_id>/` 下（`job.json` 和 `jobs/<job_id>/`），
  不经过 `CoreAPI.storage`——那里目前只封装了单一 `config.json` 的读写。
- 写入用临时文件 + `os.replace` 保证原子性，避免进程中断写出半个 JSON。
- 用进程内 `threading.Lock` 做"同一时间只有一个任务"的原子检查+创建；当前 MyBooks 部署
  是单进程 Tornado（见评审文档 3.2 节核实结果），不需要跨进程锁。
- `job.json` 里绝不写入 API Key。
- 进程重启后，磁盘上如果还留着 `status=running` 的记录，只可能是上次进程被杀/重启时没
  来得及写完的"假运行中"——epub_translator 不提供任何检查点/恢复机制（见
  `translate()` 源码，整个翻译是一次不可中途续跑的阻塞调用），这种任务已经不可能再推进，
  所以在 `JobStore` 单例第一次初始化时（约等于"进程刚启动、还没人碰过这个工具"）就把它
  改判为 `STATUS_INTERRUPTED`，不留着挡后面的新任务。因为同样的原因（没有断点续跑），
  UI 上不提供"恢复"——恢复只能是整本重新发起翻译，跟直接点"开始翻译"没有区别，做成
  "恢复"按钮反而会让人误以为能接着上次的进度译。
- `translate()` 本身也没有取消/停止 API（同一份源码核实），但因为它是我们自己在后台
  线程里调用的、`on_progress` 回调也是我们自己写的（见 `translation.py`），所以取消是在
  应用层"借回调之力"做的协作式取消：`request_cancel()` 只置一个进程内 `threading.Event`，
  `on_progress` 每次被调用时检查它，一发现被置位就抛异常提前跳出 `translate()` 的内部
  循环——不是 epub_translator 提供的能力，取消生效的时机是"下一次进度回调"而非立即。
"""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from typing import Optional

from webserver.i18n import _

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"
STATUS_CANCELLED = "cancelled"

SOURCE_LIBRARY = "library"
SOURCE_UPLOAD = "upload"

OUTPUT_DOWNLOAD = "download"
OUTPUT_NEW_BOOK = "new_book"

# 完成后未下载的结果，超过这个时间由 status/翻译新任务开始前的检查顺带清理掉
RESULT_TTL_SECONDS = 24 * 3600

# 运行中任务如果长时间没有任何进度更新，视为已失联（例如进程被杀且没走到 finally）
STALE_RUNNING_SECONDS = 2 * 3600

_JOB_FILENAME = "job.json"
_JOBS_SUBDIR = "jobs"


class JobStore:
    """按工具数据目录缓存的单例；同一个工具在同一个进程里只应该有一份状态。"""

    _instances = {}
    _instances_lock = threading.Lock()

    def __new__(cls, data_dir: str):
        with cls._instances_lock:
            inst = cls._instances.get(data_dir)
            if inst is None:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instances[data_dir] = inst
            return inst

    def __init__(self, data_dir: str):
        if self._initialized:
            return
        self._data_dir = data_dir
        self._job_path = os.path.join(data_dir, _JOB_FILENAME)
        self._jobs_dir = os.path.join(data_dir, _JOBS_SUBDIR)
        self._lock = threading.Lock()
        # 进程内取消信号，不落盘：跟着当前这一个"运行中任务"走，try_start() 开新任务时重置。
        # 进程重启后天然清空——这也符合语义，反正上一个 running 任务本来就会被判成 interrupted。
        self._cancel_event = threading.Event()
        os.makedirs(self._jobs_dir, exist_ok=True)
        self._interrupt_stale_running_job()
        self._initialized = True

    def _interrupt_stale_running_job(self) -> None:
        """本方法只会在这个数据目录第一次被访问时跑一次（见 `_initialized` 短路），
        效果上等同于"进程重启后的第一次检查"。如果这时 job.json 还是 running，说明它
        是上一个进程遗留的、不可能再推进的任务，直接改判为 interrupted 并清掉临时文件，
        这样它就不会一直卡住 `is_running()`/`try_start()`，新任务可以正常覆盖它。"""
        job = self.read()
        if not job or job.get("status") != STATUS_RUNNING:
            return
        job_id = job.get("job_id")
        logging.warning(
            "[epub_translator] Job %s was still 'running' when the process (re)started; marking it interrupted",
            job_id,
        )
        job["status"] = STATUS_INTERRUPTED
        job["stage"] = "interrupted"
        job["error"] = _("服务重启，翻译任务已中断，请重新发起翻译")
        job["updated_at"] = time.time()
        self._write_atomic(job)
        if job_id:
            self.remove_job_dir(job_id)

    # 路径

    def job_dir(self, job_id: str) -> str:
        d = os.path.join(self._jobs_dir, job_id)
        os.makedirs(d, exist_ok=True)
        return d

    def remove_job_dir(self, job_id: str) -> None:
        d = os.path.join(self._jobs_dir, job_id)
        shutil.rmtree(d, ignore_errors=True)

    # 读写

    def read(self) -> Optional[dict]:
        if not os.path.exists(self._job_path):
            return None
        try:
            with open(self._job_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as err:
            logging.warning("[epub_translator] Failed to read job.json: %s", err)
            return None

    def _write_atomic(self, data: dict) -> None:
        fd, tmp_path = tempfile.mkstemp(prefix=".job-", dir=self._data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._job_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    def is_running(self) -> bool:
        job = self.read()
        return bool(job and job.get("status") == STATUS_RUNNING)

    def try_start(self, job: dict):
        """原子"检查是否有运行中任务 + 写入新任务"。

        返回 `(started, job)`：`started=False` 时 `job` 是已有的运行中任务（前端应直接
        展示它，而不是报错），`started=True` 时 `job` 就是刚写入的新任务。
        """
        with self._lock:
            current = self.read()
            if current and current.get("status") == STATUS_RUNNING:
                if not _is_stale(current):
                    return False, current
                logging.warning(
                    "[epub_translator] Previous job %s looks stale, treating as failed",
                    current.get("job_id"),
                )
                stale_job_id = current.get("job_id")
                if stale_job_id:
                    self.remove_job_dir(stale_job_id)
            now = time.time()
            job.setdefault("created_at", now)
            job["updated_at"] = now
            self._cancel_event.clear()
            self._write_atomic(job)
            return True, job

    def request_cancel(self, job_id: str) -> bool:
        """请求取消当前运行中的任务。只置一个进程内标志位，真正生效要等
        `translation.run()` 里的 `on_progress` 下一次被调用时读到它——见模块顶部说明。
        返回 False 时说明没有匹配的运行中任务可取消（已经结束/已经是别的任务）。"""
        with self._lock:
            current = self.read()
            if not current or current.get("job_id") != job_id:
                return False
            if current.get("status") != STATUS_RUNNING:
                return False
            self._cancel_event.set()
            current["stage"] = "cancelling"
            current["updated_at"] = time.time()
            self._write_atomic(current)
            return True

    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            current = self.read()
            if not current or current.get("job_id") != job_id:
                return
            current.update(fields)
            current["updated_at"] = time.time()
            self._write_atomic(current)

    def clear_if_expired_result(self) -> None:
        """把已完成超过 TTL 且未下载的 download 结果标记为过期并清理临时文件。"""
        with self._lock:
            current = self.read()
            if not current:
                return
            if current.get("status") not in (STATUS_COMPLETED, STATUS_FAILED):
                return
            if current.get("output_mode") != OUTPUT_DOWNLOAD:
                return
            if not current.get("result_ready"):
                return
            updated_at = current.get("updated_at") or 0
            if time.time() - updated_at < RESULT_TTL_SECONDS:
                return
            job_id = current.get("job_id")
            if job_id:
                self.remove_job_dir(job_id)
            current["result_ready"] = False
            current["result_expired"] = True
            current["updated_at"] = time.time()
            self._write_atomic(current)


def _is_stale(job: dict) -> bool:
    updated_at = job.get("updated_at") or job.get("created_at") or 0
    return (time.time() - updated_at) > STALE_RUNNING_SECONDS


def new_job_id() -> str:
    return "%d-%06d" % (int(time.time()), os.getpid() % 1000000)


def empty_tokens() -> dict:
    return {"input": 0, "input_cache": 0, "output": 0, "total": 0, "available": False}


def public_job(job: Optional[dict]) -> dict:
    """job.json 对前端的展示形态。API Key 本来就不在 job.json 里，这里仍显式挑选字段，
    防止未来往 job.json 里误加了敏感字段时被自动透出到前端。"""
    if not job:
        return {"status": "idle"}
    fields = (
        "job_id", "status", "stage", "progress", "source", "book_id", "book_title",
        "output_mode", "target_language", "tokens", "error", "new_book", "result_ready",
        "result_expired", "result_downloaded", "created_at", "updated_at",
    )
    return {k: job.get(k) for k in fields}
