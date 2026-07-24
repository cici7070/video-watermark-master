"""影片逐幀修復、音訊回填與輸出驗證流程。"""

from datetime import datetime
from pathlib import Path
import shutil
import subprocess
from typing import Callable

import cv2

from .errors import UserFacingError
from .inpaint import create_inpainter
from .masks import build_mask
from .models import Box, ProcessResult, RepairMode, VideoInfo
from .video import find_binary, probe_video
from .workspace import JobWorkspace


ProgressCallback = Callable[[int, int, str], None]
_MIN_FREE_SPACE_BYTES = 512 * 1024 * 1024


def _merge_audio(source: Path, silent: Path, output: Path, has_audio: bool) -> str | None:
    """以 FFmpeg 產出相容 MP4，並在需要時保留原始音訊。"""
    command = [find_binary("ffmpeg"), "-y", "-i", str(silent)]
    if has_audio:
        command += [
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    command += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        shutil.copy2(silent, output)
        return "音訊合併失敗，已保留無聲修復版本。"
    return None


def _ensure_free_space(info: VideoInfo, workspace: JobWorkspace) -> None:
    required = max(_MIN_FREE_SPACE_BYTES, info.path.stat().st_size * 3)
    if shutil.disk_usage(workspace.path).free < required:
        raise UserFacingError(
            "磁碟空間不足，請至少保留 512 MB 可用空間後再試一次。"
        )


def _remove_if_exists(path: Path) -> None:
    path.unlink(missing_ok=True)


def process_video(
    info: VideoInfo,
    boxes: list[Box],
    mode: RepairMode,
    workspace: JobWorkspace,
    progress: ProgressCallback | None = None,
) -> ProcessResult:
    """修復每一影格的浮水印，回填音訊並確認輸出尺寸正確。"""
    if not boxes:
        raise UserFacingError("請至少框選一個需要移除的浮水印區域。")

    _ensure_free_space(info, workspace)
    silent = workspace.path / "silent.mp4"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = workspace.path / f"watermark_removed_{stamp}.mp4"
    mask = build_mask((info.width, info.height), boxes)
    inpainter = create_inpainter(mode)
    capture = cv2.VideoCapture(str(info.path))
    writer = cv2.VideoWriter(
        str(silent),
        cv2.VideoWriter_fourcc(*"mp4v"),
        info.fps,
        (info.width, info.height),
    )

    if not capture.isOpened() or not writer.isOpened():
        capture.release()
        writer.release()
        _remove_if_exists(silent)
        raise UserFacingError("無法建立影片處理器，請確認影片檔案是否可正常開啟。")

    done = 0
    try:
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                writer.write(inpainter.repair(frame, mask))
                done += 1
                if progress and (
                    done == 1 or done % 10 == 0 or done == info.frame_count
                ):
                    progress(done, info.frame_count, "正在逐幀修復影片…")
        finally:
            capture.release()
            writer.release()

        if done == 0:
            raise UserFacingError("影片沒有可處理的影格，請改用其他影片檔案。")

        warning = _merge_audio(info.path, silent, output, info.has_audio)
        result = probe_video(output)
        if result.width != info.width or result.height != info.height:
            raise UserFacingError("輸出影片尺寸與原始影片不一致，請重新處理。")
    except Exception:
        _remove_if_exists(silent)
        _remove_if_exists(output)
        raise

    _remove_if_exists(silent)
    return ProcessResult(output, warning)
