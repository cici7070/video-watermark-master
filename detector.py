import cv2
import numpy as np

from .models import Box, Candidate


def detect_candidates(
    frames: list[np.ndarray], original_size: tuple[int, int], limit: int = 3
) -> list[Candidate]:
    candidate_limit = max(0, min(limit, 3))
    if len(frames) < 4 or candidate_limit == 0:
        return []

    gray = np.stack([cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in frames])
    edges = np.stack([cv2.Canny(image, 60, 160) > 0 for image in gray])
    persistence = edges.mean(axis=0)
    variation = gray.astype(np.float32).std(axis=0)
    stable_limit = max(8.0, float(np.percentile(variation, 45)))
    binary = ((persistence >= 0.55) & (variation <= stable_limit)).astype(np.uint8) * 255
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    minimum_component_area = max(8, round(binary.size * 0.0001))
    filtered = np.zeros_like(binary)
    for label in range(1, component_count):
        if stats[label, cv2.CC_STAT_AREA] >= minimum_component_area:
            filtered[labels == label] = 255

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
    binary = cv2.morphologyEx(filtered, cv2.MORPH_CLOSE, kernel, iterations=2)
    binary = cv2.dilate(binary, np.ones((5, 5), np.uint8), iterations=2)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    small_height, small_width = binary.shape
    scale_x = original_size[0] / small_width
    scale_y = original_size[1] / small_height
    results = []

    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area_ratio = (width * height) / (small_width * small_height)
        if not 0.0005 <= area_ratio <= 0.12 or width < 12 or height < 8:
            continue

        region = persistence[y : y + height, x : x + width]
        score = min(0.99, float(region.mean()) * (1.0 + min(area_ratio * 8, 0.3)))
        box = Box(
            round(x * scale_x),
            round(y * scale_y),
            round(width * scale_x),
            round(height * scale_y),
        )
        results.append(Candidate(box, score))

    return sorted(results, key=lambda item: item.confidence, reverse=True)[:candidate_limit]
