"""Local, deterministic painting placement inside a four-corner frame."""
from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageOps

MAX_IMAGE_PIXELS = 24_000_000


def load_image(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds the 24 megapixel limit.")
        return ImageOps.exif_transpose(source).convert("RGBA")


def validate_points(points, size):
    if len(points) != 4:
        raise ValueError("Select all four corners first.")
    pts = np.asarray(points, dtype=float)
    if pts.shape != (4, 2) or not np.isfinite(pts).all():
        raise ValueError("Invalid corner coordinates.")
    width, height = size
    if (pts < 0).any() or (pts[:, 0] > width - 1).any() or (pts[:, 1] > height - 1).any():
        raise ValueError("Corners must be inside the background.")
    edges = np.roll(pts, -1, axis=0) - pts
    next_edges = np.roll(edges, -1, axis=0)
    crosses = edges[:, 0] * next_edges[:, 1] - edges[:, 1] * next_edges[:, 0]
    if not (crosses > 0).all():
        raise ValueError("Choose a convex area clockwise: top left, top right, bottom right, bottom left.")
    area = abs(np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - pts[:, 1] * np.roll(pts[:, 0], -1))) / 2
    if area < 64 or np.min(np.linalg.norm(edges, axis=1)) < 4:
        raise ValueError("Frame area is too small. Select a larger area.")
    return pts


def compose_mockup(background: bytes, painting: bytes, points) -> bytes:
    bg = load_image(background)
    pts = validate_points(points, bg.size)
    art = load_image(painting)
    white = Image.new("RGBA", art.size, "white")
    white.alpha_composite(art)
    lengths = np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)
    target_width = max(2, round((lengths[0] + lengths[2]) / 2))
    target_height = max(2, round((lengths[1] + lengths[3]) / 2))
    art = ImageOps.fit(white, (target_width, target_height), method=Image.Resampling.LANCZOS)

    # Pillow needs the inverse homography: output background -> input painting.
    destination = [(0, 0), (target_width - 1, 0), (target_width - 1, target_height - 1), (0, target_height - 1)]
    matrix, values = [], []
    for (x, y), (u, v) in zip(pts, destination):
        matrix.extend([[x, y, 1, 0, 0, 0, -u*x, -u*y], [0, 0, 0, x, y, 1, -v*x, -v*y]])
        values.extend([u, v])
    try:
        coefficients = np.linalg.solve(matrix, values)
    except np.linalg.LinAlgError as error:
        raise ValueError("Cannot map this frame. Select the corners again.") from error
    layer = art.transform(bg.size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)
    mask = Image.new("L", bg.size, 0)
    ImageDraw.Draw(mask).polygon([tuple(point) for point in pts], fill=255)
    # Keep the outer scene unchanged; the selected frame interior is replaced.
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", bg.size, 0), mask))
    result = Image.alpha_composite(bg, layer)
    output = BytesIO()
    result.save(output, format="PNG")
    return output.getvalue()
