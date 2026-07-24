"""影片資訊讀取、格式驗證與代表影格取樣。"""

from fractions import Fraction
from pathlib import Path
import json
import shutil
import subprocess

import cv2
import numpy as np

from .errors import UserFacingError
from .models import VideoInfo


ALLOWED_SUFFIXES = {".mp4", ".mov", ".avi"}


def find_binary(name: str) -> str:
    """回傳系統中可使用的 FFmpeg 工具路徑。"""
    found = shutil.which(name)
    if not found:
        raise UserFacingError(
            f"找不到 {name}，請先安裝 FFmpeg 並確認已加入系統 PATH。"
        )
    return found


def probe_video(path: Path) -> VideoInfo:
    """以 ffprobe 讀取影片的尺寸、影格率、長度與音訊資訊。"""
    command = [
        find_binary("ffprobe"),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise UserFacingError("無法讀取影片資訊，請確認檔案完整且格式正確。")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise UserFacingError("無法解析影片資訊，請更換影片檔案後再試。") from error

    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise UserFacingError("找不到影片畫面資料，請上傳含有畫面的影片檔。")

    try:
        fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
        duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
        frames = int(video.get("nb_frames") or round(duration * fps))
        width = int(video["width"])
        height = int(video["height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise UserFacingError("影片資訊不完整，請更換影片檔案後再試。") from error

    return VideoInfo(
        path=path,
        width=width,
        height=height,
        fps=fps,
        frame_count=frames,
        duration_seconds=duration,
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        container_formats=frozenset(
            name.strip().lower()
            for name in str(data.get("format", {}).get("format_name") or "").split(",")
            if name.strip()
        ),
    )


def validate_video(info: VideoInfo) -> None:
    """確認影片符合 MVP 支援的格式、長度與基本資訊。"""
    if info.path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise UserFacingError("目前僅支援 MP4、MOV 與 AVI 格式。")
    if info.duration_seconds <= 0:
        raise UserFacingError("影片長度無效，請確認檔案可以正常播放。")
    if info.duration_seconds > 60:
        raise UserFacingError("影片最長為 60 秒，請裁切後再上傳。")
    if info.width <= 0 or info.height <= 0 or info.fps <= 0:
        raise UserFacingError("影片資訊不完整，請更換影片檔案後再試。")


def sample_frames(
    info: VideoInfo, count: int = 12, max_width: int = 640
) -> list[np.ndarray]:
    """平均擷取代表影格，並縮小至適合後續分析的寬度。"""
    if count < 2:
        raise ValueError("代表影格數量至少需要 2 張。")

    capture = cv2.VideoCapture(str(info.path))
    frames: list[np.ndarray] = []
    try:
        for index in range(count):
            ratio = index / (count - 1)
            target_frame = round((info.frame_count - 1) * ratio)
            capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ok, frame = capture.read()
            if ok:
                scale = min(1.0, max_width / frame.shape[1])
                frames.append(
                    cv2.resize(
                        frame,
                        None,
                        fx=scale,
                        fy=scale,
                        interpolation=cv2.INTER_AREA,
                    )
                )
    finally:
        capture.release()

    if len(frames) < 4:
        raise UserFacingError("無法讀取足夠的影片畫面，請更換影片檔案後再試。")
    return frames
