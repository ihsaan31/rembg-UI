"""Conservative detection of a single blank, light frame interior."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from painting_mockup import load_image, validate_points


@dataclass
class FrameDetection:
    points: list[list[float]] = field(default_factory=list)
    reason: str = "No clear blank frame found. Mark the corners manually."


def _ordered(points):
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    return np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0)


def _appearance(gray, saturation, points):
    # A small rectified view samples both the interior and the surrounding frame.
    target = np.float32([[8, 8], [71, 8], [71, 71], [8, 71]])
    transform = cv2.getPerspectiveTransform(points, target)
    exterior = cv2.perspectiveTransform(np.float32([[[0, 0], [79, 0], [79, 79], [0, 79]]]), np.linalg.inv(transform))[0]
    height, width = gray.shape
    if not np.isfinite(exterior).all() or (exterior < 0).any() or (exterior[:, 0] > width - 1).any() or (exterior[:, 1] > height - 1).any():
        return None
    patch = cv2.warpPerspective(gray, transform, (80, 80))
    sat = cv2.warpPerspective(saturation, transform, (80, 80))
    inside = patch[12:68, 12:68]
    white_fraction = np.mean((inside >= 145) & (sat[12:68, 12:68] <= 70))
    # Compare each edge with its immediate outside. A pale wall alone should not qualify.
    contrasts = np.array([
        patch[10:14, 16:64].mean() - patch[3:7, 16:64].mean(),
        patch[16:64, 66:70].mean() - patch[16:64, 73:77].mean(),
        patch[66:70, 16:64].mean() - patch[73:77, 16:64].mean(),
        patch[16:64, 10:14].mean() - patch[16:64, 3:7].mean(),
    ])
    if white_fraction < 0.9 or np.count_nonzero(contrasts > 10) < 3 or contrasts.min() < 3:
        return None
    # Texture is a useful discriminator against bright pictures and divided windows.
    texture = np.mean(cv2.Canny(inside, 35, 90) > 0)
    if texture > 0.07:
        return None
    return float(0.55 * min(contrasts.mean() / 70, 1) + 0.3 * white_fraction + 0.15 * (1 - texture / 0.07))


def detect_frame(data: bytes) -> FrameDetection:
    image = load_image(data)
    original_size = image.size
    image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    rgba = np.asarray(image)
    rgb = np.asarray(image.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    candidates = []
    for threshold in (145, 170, 195, 220, 240):
        mask = np.uint8((gray >= threshold) & (hsv[:, :, 1] <= 70) & (rgba[:, :, 3] >= 250)) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if not 0.015 * width * height <= area <= 0.8 * width * height:
                continue
            polygon = cv2.approxPolyDP(contour, 0.015 * cv2.arcLength(contour, True), True)
            if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                continue
            points = _ordered(polygon)
            if (points < 3).any() or (points[:, 0] >= width - 3).any() or (points[:, 1] >= height - 3).any():
                continue
            if abs(cv2.contourArea(points) - area) / area > 0.08:
                continue
            try:
                validate_points(points, (width, height))
            except ValueError:
                continue
            score = _appearance(gray, hsv[:, :, 1], points)
            if score is None:
                continue
            # Different thresholds often produce the same frame; keep its best match.
            duplicate = next((i for i, (_, other) in enumerate(candidates) if np.mean(np.linalg.norm(other - points, axis=1)) < max(5, np.sqrt(area) * 0.025)), None)
            if duplicate is None:
                candidates.append((score, points))
            elif score > candidates[duplicate][0]:
                candidates[duplicate] = (score, points)
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return FrameDetection()
    if len(candidates) > 1 and candidates[1][0] >= candidates[0][0] * 0.85:
        return FrameDetection(reason="Several similar bright areas found. Choose the frame corners manually.")
    points = candidates[0][1].astype(float)
    points *= np.array([(original_size[0] - 1) / max(width - 1, 1), (original_size[1] - 1) / max(height - 1, 1)])
    validate_points(points, original_size)
    return FrameDetection(points=points.tolist(), reason="Blank frame detected. Check the outline before generating.")
