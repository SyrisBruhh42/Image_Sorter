"""High-performance image decoding engine with QImageReader autoTransform and security bounds.

Provides safe, thread-agnostic QImage decoding, EXIF orientation handling,
color space conversion to sRGB, and memory-bounded preview downscaling.
"""

from __future__ import annotations

import os
from typing import Any

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColorSpace, QImage, QImageReader

from .logger import logger

# Maximum allowed uncompressed buffer allocation (500 MB default)
DEFAULT_MAX_ALLOCATION_BYTES: int = 500 * 1024 * 1024

# Explicitly unsupported RAW / container formats requiring native plugins
EXPLICIT_UNSUPPORTED_FORMATS: set[str] = {
    "heic",
    "heif",
    "cr2",
    "nef",
    "arw",
    "dng",
    "orf",
    "rw2",
    "pef",
    "raf",
    "srw",
}


def decode_image(
    filepath: str | os.PathLike[str],
    *,
    target_size: tuple[int, int] | None = None,
    max_allocation_bytes: int = DEFAULT_MAX_ALLOCATION_BYTES,
) -> tuple[QImage | None, str | None]:
    """Decodes an image file safely into a QImage.

    Uses QImageReader with setAutoTransform(True) for EXIF orientation corrections,
    verifies dimensions and estimated uncompressed memory usage prior to allocation,
    converts non-sRGB embedded color profiles to sRGB, and optionally downscales
    to target_size preserving aspect ratio.

    Args:
        filepath: Path to the image file.
        target_size: Optional (width, height) bounding box for downscaling previews.
        max_allocation_bytes: Maximum allowed uncompressed byte size (width * height * 4).

    Returns:
        Tuple of (QImage or None, error_message or None).
    """
    path_str: str = os.fspath(filepath)

    if not os.path.exists(path_str):
        return None, f"File does not exist: {path_str}"

    if not os.path.isfile(path_str):
        return None, f"Path is not a regular file: {path_str}"

    try:
        if os.path.getsize(path_str) == 0:
            return None, f"Image file is empty (0 bytes): {path_str}"
    except OSError as e:
        return None, f"Failed to access file {path_str}: {e}"

    ext: str = os.path.splitext(path_str)[1].lower().lstrip(".")
    if ext in EXPLICIT_UNSUPPORTED_FORMATS:
        return (
            None,
            f"Format '{ext.upper()}' is not supported by installed image plugins.",
        )

    try:
        reader = QImageReader(path_str)
        reader.setAutoTransform(True)

        if not reader.canRead():
            err_msg: str = reader.errorString() or "Unsupported image format or corrupt header"
            return None, f"Cannot read image file '{path_str}': {err_msg}"

        raw_size: QSize = reader.size()
        if not raw_size.isValid() or raw_size.width() <= 0 or raw_size.height() <= 0:
            return None, f"Invalid or unreadable image dimensions for '{path_str}'"

        estimated_bytes: int = raw_size.width() * raw_size.height() * 4
        if estimated_bytes > max_allocation_bytes:
            err_desc: str = (
                f"Image dimensions ({raw_size.width()}x{raw_size.height()}) exceed maximum "
                f"allocation threshold ({estimated_bytes} > {max_allocation_bytes} bytes)"
            )
            return None, err_desc

        qimg: QImage = reader.read()
        if qimg.isNull():
            err_msg = reader.errorString() or "Decode returned null image"
            return None, f"Failed to decode image '{path_str}': {err_msg}"

        # Handle embedded color profile conversion to sRGB
        if qimg.colorSpace().isValid():
            srgb_cs = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
            if qimg.colorSpace() != srgb_cs:
                try:
                    qimg.convertToColorSpace(srgb_cs)
                except Exception as cs_err:
                    logger.warning(
                        f"Color space conversion to sRGB failed for '{path_str}': {cs_err}"
                    )

        # Downscale if target_size is specified and image is larger than target_size
        if target_size is not None:
            tw, th = target_size
            if tw > 0 and th > 0:
                if qimg.width() > tw or qimg.height() > th:
                    qimg = qimg.scaled(
                        tw,
                        th,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )

        return qimg, None

    except Exception as exc:
        logger.error(f"Unexpected error decoding image '{path_str}': {exc}", exc_info=True)
        return None, f"Unexpected error decoding image: {exc}"


def inspect_image_header(
    filepath: str | os.PathLike[str],
) -> dict[str, Any]:
    """Inspects an image header without performing full pixel decoding.

    Args:
        filepath: Path to the image file.

    Returns:
        Dictionary containing metadata such as 'valid', 'format', 'width', 'height', 'error'.
    """
    path_str: str = os.fspath(filepath)
    if not os.path.exists(path_str):
        return {"valid": False, "error": f"File does not exist: {path_str}"}

    ext: str = os.path.splitext(path_str)[1].lower().lstrip(".")
    if ext in EXPLICIT_UNSUPPORTED_FORMATS:
        return {
            "valid": False,
            "error": f"Format '{ext.upper()}' is not supported by installed plugins.",
        }

    reader = QImageReader(path_str)
    reader.setAutoTransform(True)

    if not reader.canRead():
        return {
            "valid": False,
            "error": reader.errorString() or "Unsupported image format or corrupt header",
        }

    size: QSize = reader.size()
    fmt_raw = reader.format()
    fmt_bytes: bytes = fmt_raw.data() if hasattr(fmt_raw, "data") else bytes(fmt_raw)
    fmt_str: str = fmt_bytes.decode("ascii", errors="ignore").lower()

    return {
        "valid": True,
        "format": fmt_str,
        "width": size.width(),
        "height": size.height(),
        "error": None,
    }
