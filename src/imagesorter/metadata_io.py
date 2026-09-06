from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

import piexif

from .logger import logger


def sanitize_tags(tags: list[str]) -> list[str]:
    """
    Sanitizes metadata tags by removing non-printable characters and control characters,
    limiting each tag to 64 characters, and capping the list at 30 tags.

    Args:
        tags (list[str]): Raw input tags.

    Returns:
        list[str]: Cleaned, sanitized tag list.
    """
    sanitized: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        cleaned = "".join(ch for ch in tag if ch.isprintable() and ch not in ("\r", "\n", "\x00"))
        cleaned = cleaned.strip()
        if cleaned:
            sanitized.append(cleaned[:64])
        if len(sanitized) >= 30:
            break
    return sanitized


def merge_sidecar_content(existing_text: str, new_tags: list[str]) -> str:
    """
    Merges new sanitized tags into existing sidecar text while preserving preexisting notes/tags.

    Args:
        existing_text (str): Content previously present in sidecar file.
        new_tags (list[str]): New tags to append or merge.

    Returns:
        str: Consolidated sidecar string.
    """
    existing_text_trimmed = existing_text.strip()
    if not existing_text_trimmed:
        return ", ".join(new_tags)

    existing_parts = [p.strip() for p in existing_text_trimmed.split(",") if p.strip()]
    existing_set = {p.lower() for p in existing_parts}

    added_tags = [t for t in new_tags if t.lower() not in existing_set]
    if not added_tags:
        return existing_text_trimmed

    return existing_text_trimmed + ", " + ", ".join(added_tags)


def write_metadata(
    filepath: str,
    tags: list[str],
    write_exif: bool = True,
    write_sidecar: bool = False,
) -> None:
    """
    Writes metadata tags to EXIF (via atomic temp-file swap) or sidecar .txt file.
    Preserves original bytes, preexisting metadata, and sidecar text needed for rollback.

    Args:
        filepath (str): Target image file path.
        tags (list[str]): Tags to embed or record.
        write_exif (bool): Whether to embed tags in EXIF XPKeywords.
        write_sidecar (bool): Whether to create/update sidecar .txt file.

    Raises:
        ValueError: If path traversal is detected for sidecar creation.
        OSError: If writing metadata fails due to I/O error.
    """
    sanitized = sanitize_tags(tags)
    if not sanitized:
        return

    real_image_path = os.path.realpath(filepath)
    parent_dir = os.path.dirname(real_image_path) or "."

    # Write Sidecar atomically with path traversal check and note preservation
    if write_sidecar:
        sidecar_path = real_image_path + ".txt"
        real_sidecar = os.path.realpath(sidecar_path)

        if os.path.commonpath([parent_dir, real_sidecar]) != parent_dir:
            err_msg = f"Path traversal detected: {sidecar_path} escapes {parent_dir}"
            logger.error(err_msg)
            raise ValueError(err_msg)

        existing_text = ""
        if os.path.exists(sidecar_path):
            try:
                with open(sidecar_path, "r", encoding="utf-8", errors="replace") as f:
                    existing_text = f.read()
            except OSError as e:
                logger.warning(f"Could not read existing sidecar at {sidecar_path}: {e}")

        final_sidecar_text = merge_sidecar_content(existing_text, sanitized)

        temp_sidecar_path: str | None = None
        try:
            fd, temp_sidecar_path = tempfile.mkstemp(dir=parent_dir, prefix="sidecar_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(final_sidecar_text)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_sidecar_path, sidecar_path)
            logger.debug(f"Wrote sidecar metadata atomically to {sidecar_path}")
        except Exception as e:
            logger.error(f"Error writing sidecar for {filepath}: {e}")
            if temp_sidecar_path and os.path.exists(temp_sidecar_path):
                try:
                    os.remove(temp_sidecar_path)
                except OSError:
                    pass
            raise OSError(f"Failed to write sidecar metadata: {e}") from e

    # Write EXIF atomically via temp file swap
    if write_exif and filepath.lower().endswith((".jpg", ".jpeg")):
        temp_img_path: str | None = None
        try:
            tag_string = ";".join(sanitized)
            xp_keywords = (tag_string + "\x00").encode("utf-16le")

            exif_dict: dict[str, Any] | None = None
            try:
                exif_dict = piexif.load(filepath)
                if "0th" not in exif_dict:
                    exif_dict["0th"] = {}
                exif_dict["0th"][piexif.ImageIFD.XPKeywords] = xp_keywords
                exif_bytes = piexif.dump(exif_dict)
            except Exception as load_or_dump_err:
                logger.warning(
                    f"Malformed camera EXIF header in {filepath} ({load_or_dump_err}); "
                    "falling back to pristine 0th IFD while keeping image stream intact."
                )
                pristine_exif = {
                    "0th": {
                        piexif.ImageIFD.XPKeywords: xp_keywords
                    },
                    "Exif": {},
                    "GPS": {},
                    "Interop": {},
                    "1st": {},
                    "thumbnail": None,
                }
                exif_bytes = piexif.dump(pristine_exif)

            if len(exif_bytes) > 32768:
                logger.error(f"EXIF payload ({len(exif_bytes)} bytes) exceeds 32KB limit for {filepath}")
                raise ValueError("EXIF payload exceeds 32KB limit")

            fd, temp_img_path = tempfile.mkstemp(dir=parent_dir, prefix="exif_", suffix=".tmp")
            os.close(fd)

            # Copy original image to temp file
            with open(filepath, "rb") as src, open(temp_img_path, "wb") as dst:
                shutil.copyfileobj(src, dst, length=65536)
                dst.flush()
                os.fsync(dst.fileno())

            piexif.insert(exif_bytes, temp_img_path)

            # Preserve POSIX stat permissions & mtime
            try:
                shutil.copystat(filepath, temp_img_path)
            except OSError as cs_err:
                logger.warning(f"Could not copy file stat for {filepath}: {cs_err}")

            os.replace(temp_img_path, filepath)
            logger.debug(f"Wrote EXIF metadata atomically to {filepath}")
        except piexif.InvalidImageDataError as e:
            logger.warning(f"Invalid image data for EXIF injection in {filepath}: {e}")
            if temp_img_path and os.path.exists(temp_img_path):
                try:
                    os.remove(temp_img_path)
                except OSError:
                    pass
        except Exception as e:
            logger.error(f"Error writing EXIF for {filepath}: {e}")
            if temp_img_path and os.path.exists(temp_img_path):
                try:
                    os.remove(temp_img_path)
                except OSError:
                    pass
            raise OSError(f"Failed to write EXIF metadata: {e}") from e
