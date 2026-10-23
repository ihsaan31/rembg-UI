from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryFile
from typing import Callable, Iterable
from zipfile import ZIP_STORED, ZipFile

from PIL import Image, ImageOps


PRINT_DPI = 300
PRINT_EXPORT_VERSION = 3


@dataclass(frozen=True)
class PrintSize:
    group: str
    label: str
    width: float
    height: float
    unit: str

    def pixels(self, landscape: bool = False) -> tuple[int, int]:
        units_per_inch = {"in": 1, "cm": 2.54, "mm": 25.4}[self.unit]
        dimensions = (
            round(self.width / units_per_inch * PRINT_DPI),
            round(self.height / units_per_inch * PRINT_DPI),
        )
        return dimensions[::-1] if landscape else dimensions


PRINT_SIZES = (
    PrintSize("ISO", "A1", 594, 841, "mm"),
    PrintSize("2x3", "24x36", 24, 36, "in"),
    PrintSize("3x4", "24x32", 24, 32, "in"),
    PrintSize("4x5", "24x30", 24, 30, "in"),
)


def safe_print_stem(filename: str) -> str:
    stem = Path(filename.replace("\\", "/")).stem
    return re.sub(r"[^\w.-]+", "_", stem).strip("._")[:100] or "image"


def prepare_print_source(image_bytes: bytes) -> tuple[bytes, bool]:
    """Apply EXIF orientation before upload and remember the source orientation."""
    with Image.open(BytesIO(image_bytes)) as image:
        oriented = ImageOps.exif_transpose(image)
        oriented.load()
        landscape = oriented.width > oriented.height
        output = BytesIO()
        has_alpha = oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info
        normalized = oriented.convert("RGBA" if has_alpha else "RGB")
        normalized.save(output, format="PNG")
        normalized.close()
        oriented.close()
    return output.getvalue(), landscape


def build_print_zip(
    upscaled_bytes: bytes,
    source_name: str,
    landscape: bool,
    on_progress: Callable[[int, int], None] | None = None,
    sizes: Iterable[PrintSize] | None = None,
) -> bytes:
    sizes = tuple(PRINT_SIZES if sizes is None else sizes)
    with Image.open(BytesIO(upscaled_bytes)) as image:
        oriented = ImageOps.exif_transpose(image)
        rgba = oriented.convert("RGBA")
        source = Image.new("RGB", rgba.size, "white")
        source.paste(rgba, mask=rgba.getchannel("A"))
        rgba.close()
        oriented.close()

    try:
        # JPEG is already compressed. Keep only one resized image in memory,
        # and assemble the archive on disk before returning the download bytes.
        with TemporaryFile() as archive_file:
            with ZipFile(archive_file, "w", compression=ZIP_STORED) as archive:
                for index, size in enumerate(sizes, start=1):
                    target = size.pixels(landscape)
                    resized = ImageOps.fit(
                        source, target, method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.5),
                    )
                    try:
                        width, height = (size.height, size.width) if landscape else (size.width, size.height)
                        label = size.label if size.group == "ISO" else f"{width:g}x{height:g}"
                        size_label = label if size.group == "ISO" else f"{label}{size.unit}"
                        name = f"{size.group}_{size_label}.jpg"
                        with archive.open(name, "w") as jpg_file:
                            resized.save(jpg_file, format="JPEG", quality=95, dpi=(PRINT_DPI, PRINT_DPI))
                    finally:
                        resized.close()
                    if on_progress is not None:
                        on_progress(index, len(sizes))
            archive_file.seek(0)
            return archive_file.read()
    finally:
        source.close()
