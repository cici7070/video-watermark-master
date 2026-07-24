from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import secrets
from threading import Event, Lock
import time
from typing import Callable

from .models import Box, RepairMode, VideoInfo
from .pipeline import process_video
from .workspace import JobWorkspace


class JobState(StrEnum):
    READY = "ready"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobRecord:
    job_id: str
    token: str = field(repr=False)
    workspace: JobWorkspace = field(repr=False)
    info: VideoInfo = field(repr=False)
    created_at: float = field(repr=False)
    state: JobState = JobState.READY
    progress: int = 0
    message: str = "等待確認浮水印範圍"
    output: Path | None = field(default=None, repr=False)
    warning: str | None = field(default=None, repr=False)
    error: str | None = field(default=None, repr=False)
    failure_category: str | None = field(default=None, repr=False)
    future: Future | None = field(default=None, repr=False)
    _expired: bool = field(default=False, repr=False)


class _ExpiredRecord(Exception):
    def __init__(self, cleanup: JobRecord | None):
        self.cleanup = cleanup


class JobManager:
    def __init__(
        self,
        root: Path,
        ttl_seconds: int = 1800,
        max_queue: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_queue = max_queue
        self.clock = clock
        self._jobs: dict[str, JobRecord] = {}
        self._cleanup_pending: dict[str, JobRecord] = {}
        self._lock = Lock()
        self._cleanup_lock = Lock()
        self._closed = False
        self._close_complete = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-job")

    def create(self, workspace: JobWorkspace, info: VideoInfo) -> JobRecord:
        record = JobRecord(
            job_id=secrets.token_urlsafe(18),
            token=secrets.token_urlsafe(32),
            workspace=workspace,
            info=info,
            created_at=self.clock(),
        )
        with self._lock:
            self._ensure_open_locked()
            self._jobs[record.job_id] = record
        return record

    def require(self, job_id: str, token: str) -> JobRecord:
        try:
            with self._lock:
                return self._require_locked(job_id, token)
        except _ExpiredRecord as expired:
            if expired.cleanup is not None:
                self._attempt_cleanup(expired.cleanup)
            raise KeyError(job_id) from None

    def submit(self, job_id: str, token: str, boxes: list[Box]) -> None:
        try:
            with self._lock:
                self._ensure_open_locked()
                record = self._require_locked(job_id, token)
                if record.state is not JobState.READY:
                    raise RuntimeError("這個工作已經開始，不能重複送出。")
                if not 1 <= len(boxes) <= 3:
                    raise ValueError("請選擇 1 至 3 個有效的浮水印範圍。")

                cleaned = []
                for box in boxes:
                    clamped = box.clamped(record.info.width, record.info.height)
                    if clamped.width > 0 and clamped.height > 0:
                        cleaned.append(clamped)
                if not 1 <= len(cleaned) <= 3:
                    raise ValueError("請選擇 1 至 3 個有效的浮水印範圍。")

                waiting = sum(
                    job.state is JobState.QUEUED and not job._expired
                    for job in self._jobs.values()
                )
                if waiting >= self.max_queue:
                    raise OverflowError("目前排隊人數較多，請稍後再試。")

                previous_message = record.message
                record.state = JobState.QUEUED
                record.message = "已加入處理佇列"
                record.failure_category = None
                try:
                    record.future = self._executor.submit(self._run, record, cleaned)
                except Exception:
                    record.state = JobState.READY
                    record.message = previous_message
                    record.future = None
                    record.failure_category = "scheduling_error"
                    raise RuntimeError(
                        "目前無法啟動影片處理，請稍後再試。"
                    ) from None
        except _ExpiredRecord as expired:
            if expired.cleanup is not None:
                self._attempt_cleanup(expired.cleanup)
            raise KeyError(job_id) from None

    def _run(self, record: JobRecord, boxes: list[Box]) -> None:
        with self._lock:
            if record._expired:
                self._jobs.pop(record.job_id, None)
                self._register_cleanup_locked(record)
                should_cleanup = True
            else:
                should_cleanup = False
                record.state = JobState.PROCESSING
                record.message = "正在準備影片"

        if should_cleanup:
            self._attempt_cleanup(record)
            return

        def progress(done: int, total: int, message: str) -> None:
            percent = 0 if total <= 0 else min(99, round(done / total * 100))
            with self._lock:
                record.progress = percent
                record.message = message

        try:
            result = process_video(
                record.info,
                boxes,
                RepairMode.FAST,
                record.workspace,
                progress,
            )
            with self._lock:
                record.output = result.path
                record.warning = result.warning
                record.progress = 100
                record.message = "影片處理完成"
                record.state = JobState.COMPLETED
                should_cleanup = self._finish_expired_locked(record)
        except Exception:
            with self._lock:
                record.error = "影片處理失敗，請換一支影片或縮小框選範圍。"
                record.failure_category = "processing_error"
                record.message = record.error
                record.state = JobState.FAILED
                should_cleanup = self._finish_expired_locked(record)

        if should_cleanup:
            self._attempt_cleanup(record)

    def snapshot(self, job_id: str, token: str) -> dict[str, object]:
        try:
            with self._lock:
                record = self._require_locked(job_id, token)
                return {
                    "state": record.state,
                    "progress": record.progress,
                    "message": record.message,
                    "warning": record.warning,
                    "error": record.error,
                    "failure_category": record.failure_category,
                }
        except _ExpiredRecord as expired:
            if expired.cleanup is not None:
                self._attempt_cleanup(expired.cleanup)
            raise KeyError(job_id) from None

    def cleanup_expired(self) -> int:
        now = self.clock()
        with self._lock:
            records: dict[str, JobRecord] = dict(self._cleanup_pending)
            for job_id, job in list(self._jobs.items()):
                expired, cleanup = self._expire_record_locked(job, now)
                if expired and cleanup is not None:
                    records[job_id] = cleanup

        return sum(self._attempt_cleanup(record) for record in records.values())

    def delete(self, job_id: str, token: str) -> None:
        try:
            with self._lock:
                record = self._require_locked(job_id, token)
                if record.state in {JobState.QUEUED, JobState.PROCESSING}:
                    raise RuntimeError("影片正在處理，完成後才能刪除。")
                self._jobs.pop(job_id, None)
                record._expired = True
                self._register_cleanup_locked(record)
        except _ExpiredRecord as expired:
            if expired.cleanup is not None:
                self._attempt_cleanup(expired.cleanup)
            raise KeyError(job_id) from None
        self._attempt_cleanup(record)

    def close(self) -> None:
        with self._lock:
            first_close = not self._closed
            self._closed = True

        if not first_close:
            self._close_complete.wait()
            self._retry_pending_cleanup()
            return

        try:
            self._executor.shutdown(wait=True, cancel_futures=False)

            with self._lock:
                records = dict(self._cleanup_pending)
                records.update(self._jobs)
                self._jobs.clear()
                for record in records.values():
                    record._expired = True
                    self._register_cleanup_locked(record)

            for record in records.values():
                self._attempt_cleanup(record)
        finally:
            self._close_complete.set()

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise RuntimeError("工作管理器已關閉。")

    def _require_locked(self, job_id: str, token: str) -> JobRecord:
        record = self._jobs.get(job_id)
        if record is None:
            raise KeyError(job_id)
        if not secrets.compare_digest(record.token, token):
            raise PermissionError(job_id)
        expired, cleanup = self._expire_record_locked(record, self.clock())
        if expired:
            raise _ExpiredRecord(cleanup)
        return record

    def _is_expired_locked(self, record: JobRecord, now: float) -> bool:
        return record._expired or now - record.created_at >= self.ttl_seconds

    def _expire_record_locked(
        self, record: JobRecord, now: float
    ) -> tuple[bool, JobRecord | None]:
        if not self._is_expired_locked(record, now):
            return False, None

        record._expired = True
        if record.state is JobState.PROCESSING:
            return True, None
        if record.state is JobState.QUEUED:
            if record.future is None or not record.future.cancel():
                return True, None

        self._jobs.pop(record.job_id, None)
        self._register_cleanup_locked(record)
        return True, record

    def _finish_expired_locked(self, record: JobRecord) -> bool:
        if not record._expired:
            return False
        self._jobs.pop(record.job_id, None)
        self._register_cleanup_locked(record)
        return True

    def _attempt_cleanup(self, record: JobRecord) -> bool:
        with self._cleanup_lock:
            try:
                record.workspace.cleanup()
                cleaned = not record.workspace.path.exists()
            except Exception:
                cleaned = False

        with self._lock:
            if cleaned:
                if self._cleanup_pending.get(record.job_id) is record:
                    self._cleanup_pending.pop(record.job_id, None)
            else:
                self._cleanup_pending[record.job_id] = record
        return cleaned

    def _register_cleanup_locked(self, record: JobRecord) -> None:
        self._cleanup_pending[record.job_id] = record

    def _retry_pending_cleanup(self) -> None:
        with self._lock:
            records = list(self._cleanup_pending.values())
        for record in records:
            self._attempt_cleanup(record)
