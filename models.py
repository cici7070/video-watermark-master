from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RepairMode(StrEnum):
    FAST = "快速模式"
    QUALITY = "高品質模式"


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    def normalized(self) -> "Box":
        x = self.x + min(self.width, 0)
        y = self.y + min(self.height, 0)
        return Box(x, y, abs(self.width), abs(self.height))

    def clamped(self, frame_width: int, frame_height: int) -> "Box":
        box = self.normalized()
        left, top = max(0, box.x), max(0, box.y)
        right = min(frame_width, box.x + box.width)
        bottom = min(frame_height, box.y + box.height)
        return Box(left, top, max(0, right - left), max(0, bottom - top))


@dataclass(frozen=True)
class Candidate:
    box: Box
    confidence: float


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    has_audio: bool
    container_formats: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ProcessResult:
    path: Path
    warning: str | None = None
