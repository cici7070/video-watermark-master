import cv2
import numpy as np

from .models import Box


def build_mask(size: tuple[int, int], boxes: list[Box], padding: int = 4) -> np.ndarray:
    width, height = size
    mask = np.zeros((height, width), dtype=np.uint8)
    for raw in boxes:
        box = Box(
            raw.x - padding,
            raw.y - padding,
            raw.width + padding * 2,
            raw.height + padding * 2,
        ).clamped(width, height)
        if box.width and box.height:
            cv2.rectangle(
                mask,
                (box.x, box.y),
                (box.x + box.width - 1, box.y + box.height - 1),
                255,
                -1,
            )
    return mask


def draw_boxes(frame: np.ndarray, boxes: list[Box]) -> np.ndarray:
    preview = frame.copy()
    for index, box in enumerate(boxes, 1):
        cv2.rectangle(
            preview,
            (box.x, box.y),
            (box.x + box.width, box.y + box.height),
            (92, 184, 152),
            3,
        )
        cv2.putText(
            preview,
            str(index),
            (box.x + 5, max(24, box.y + 24)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (92, 184, 152),
            2,
        )
    return preview
