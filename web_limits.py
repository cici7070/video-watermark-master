from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from threading import Lock

from .errors import UserFacingError
from .models import VideoInfo
from .video import ALLOWED_SUFFIXES, validate_video


@dataclass(frozen=True)
class WebLimits:
    max_bytes: int = 200 * 1024 * 1024
    max_width: int = 1920
    max_height: int = 1080
    max_duration_seconds: float = 60.0


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int = 0
    reservation_id: str | None = None


@dataclass(frozen=True)
class _RateLimitEvent:
    occurred_at: datetime
    reservation_id: str


class WebValidationError(UserFacingError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_public_video(
    info: VideoInfo, file_size: int, limits: WebLimits
) -> None:
    if info.path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise WebValidationError(
            "UNSUPPORTED_VIDEO", "目前僅支援 MP4、MOV、AVI 格式。"
        )
    if file_size > limits.max_bytes:
        raise WebValidationError(
            "FILE_TOO_LARGE", "影片不可超過 200 MB，請壓縮後再上傳。"
        )
    if info.duration_seconds > limits.max_duration_seconds:
        raise WebValidationError(
            "VIDEO_TOO_LONG", "影片最長為 60 秒，請裁切後再上傳。"
        )
    if info.width > limits.max_width or info.height > limits.max_height:
        raise WebValidationError(
            "RESOLUTION_TOO_HIGH",
            "公開網頁版最高支援 1920×1080，請先縮小影片。",
        )
    allowed_containers = {
        ".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        ".mov": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        ".avi": {"avi"},
    }
    if info.container_formats and not (
        info.container_formats & allowed_containers[info.path.suffix.lower()]
    ):
        raise WebValidationError(
            "UNSUPPORTED_VIDEO",
            "影片內容與副檔名不符，請轉成 MP4、MOV 或 AVI 後再試。",
        )
    try:
        validate_video(info)
    except UserFacingError as exception:
        raise WebValidationError("UNSUPPORTED_VIDEO", str(exception)) from exception


class RollingRateLimiter:
    def __init__(self, limit: int = 3, window: timedelta = timedelta(hours=24)):
        self.limit = limit
        self.window = window
        self._events: dict[str, deque[_RateLimitEvent]] = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, ip: str, now: datetime) -> deque[_RateLimitEvent]:
        events = self._events[ip]
        boundary = now - self.window
        while events and events[0].occurred_at <= boundary:
            events.popleft()
        return events

    def check(self, ip: str, now: datetime | None = None) -> RateLimitResult:
        current = now or datetime.now(UTC)
        with self._lock:
            events = self._prune(ip, current)
            if len(events) < self.limit:
                return RateLimitResult(True, self.limit - len(events))
            retry_at = events[0].occurred_at + self.window
            retry_after = max(1, int((retry_at - current).total_seconds()))
            return RateLimitResult(False, 0, retry_after)

    def consume(self, ip: str, now: datetime | None = None) -> RateLimitResult:
        current = now or datetime.now(UTC)
        with self._lock:
            events = self._prune(ip, current)
            if len(events) >= self.limit:
                retry_at = events[0].occurred_at + self.window
                return RateLimitResult(
                    False, 0, max(1, int((retry_at - current).total_seconds()))
                )
            reservation_id = secrets.token_urlsafe(18)
            events.append(_RateLimitEvent(current, reservation_id))
            return RateLimitResult(
                True,
                self.limit - len(events),
                reservation_id=reservation_id,
            )

    def rollback(self, ip: str, reservation_id: str | None) -> bool:
        """只撤銷指定的額度保留，避免併發失敗誤刪其他工作額度。"""
        if not reservation_id:
            return False
        with self._lock:
            events = self._events.get(ip)
            if not events:
                return False
            for event in events:
                if secrets.compare_digest(event.reservation_id, reservation_id):
                    events.remove(event)
                    if not events:
                        self._events.pop(ip, None)
                    return True
        return False
