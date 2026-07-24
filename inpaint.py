"""浮水印遮罩的影像修復策略。"""

from importlib import import_module

import cv2
import numpy as np
from PIL import Image

from .errors import UserFacingError
from .models import RepairMode


class FastInpainter:
    """使用 OpenCV Telea 演算法的快速修復。"""

    def repair(self, frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return cv2.inpaint(frame, mask, 5, cv2.INPAINT_TELEA)


class LamaInpainter:
    """按需載入 LaMa 的高品質修復器。"""

    def __init__(self) -> None:
        self._model = None

    def _load(self, force_cpu: bool = False):
        if self._model is None or force_cpu:
            try:
                lama_module = import_module("simple_lama_inpainting")
                torch = import_module("torch")
                device_name = "cpu" if force_cpu else "cuda" if torch.cuda.is_available() else "cpu"
                self._model = lama_module.SimpleLama(device=torch.device(device_name))
            except (ModuleNotFoundError, ImportError) as error:
                raise UserFacingError(
                    "找不到 LaMa 高品質修復所需套件，請安裝 AI 修復功能的選用依賴。"
                ) from error
        return self._model

    def repair(self, frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        points = cv2.findNonZero(mask)
        if points is None:
            return frame.copy()

        x, y, width, height = cv2.boundingRect(points)
        margin = max(32, round(max(width, height) * 0.5))
        left, top = max(0, x - margin), max(0, y - margin)
        right = min(frame.shape[1], x + width + margin)
        bottom = min(frame.shape[0], y + height + margin)
        crop = frame[top:bottom, left:right]
        crop_mask = mask[top:bottom, left:right]
        image = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        binary_mask = Image.fromarray(crop_mask).convert("L")

        try:
            repaired = self._load()(image, binary_mask)
        except UserFacingError:
            raise
        except RuntimeError as error:
            if not self._is_gpu_error(error):
                raise UserFacingError("高品質修復失敗，請稍後再試或改用快速修復。") from error
            repaired = self._repair_on_cpu(image, binary_mask)

        try:
            output = frame.copy()
            output[top:bottom, left:right] = cv2.cvtColor(
                np.asarray(repaired), cv2.COLOR_RGB2BGR
            )
            return output
        except (ValueError, cv2.error) as error:
            raise UserFacingError("高品質修復結果格式不正確，請改用快速修復。") from error

    def _repair_on_cpu(self, image: Image.Image, binary_mask: Image.Image):
        try:
            torch = import_module("torch")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            self._model = None
            return self._load(force_cpu=True)(image, binary_mask)
        except Exception as error:
            raise UserFacingError(
                "高品質修復的 GPU 執行失敗，改用 CPU 後仍無法完成，請改用快速修復。"
            ) from error

    @staticmethod
    def _is_gpu_error(error: RuntimeError) -> bool:
        message = str(error).lower()
        return "cuda" in message or "memory" in message


def create_inpainter(mode: RepairMode) -> FastInpainter | LamaInpainter:
    """依使用者所選模式建立對應修復器。"""
    return LamaInpainter() if mode == RepairMode.QUALITY else FastInpainter()
