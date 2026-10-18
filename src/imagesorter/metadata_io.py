from __future__ import annotations

import os
import tempfile
from typing import Any

import piexif

from . import file_safety as safe
from .file_safety import sync_directory
from .logger import logger
from .platform_capabilities import require_mutation_support


class MetadataRecoveryError(OSError):
    """Rollback was incomplete; these exact backup files must be retained."""

    def __init__(self, message: str, artifacts: list[str]) -> None:
        super().__init__(message)
        self.artifacts = artifacts


def sanitize_tags(tags: list[str]) -> list[str]:
    """Return unique printable tags with conservative count and length limits."""
    sanitized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            continue
        cleaned = "".join(
            ch for ch in tag if ch.isprintable() and ch not in ("\r", "\n", "\x00")
        ).strip()[:64]
        folded = cleaned.casefold()
        if cleaned and folded not in seen:
            sanitized.append(cleaned)
            seen.add(folded)
        if len(sanitized) >= 30:
            break
    return sanitized


def merge_sidecar_content(existing_text: str, new_tags: list[str]) -> str:
    """Append missing tags without rewriting the user's existing sidecar text."""
    sanitized = sanitize_tags(new_tags)
    if not existing_text:
        return ", ".join(sanitized)

    existing_parts = {
        part.strip().casefold()
        for part in existing_text.replace(";", ",").split(",")
        if part.strip()
    }
    additions = [tag for tag in sanitized if tag.casefold() not in existing_parts]
    if not additions:
        return existing_text
    separator = "" if existing_text.endswith((",", ";", " ", "\n", "\t")) else ", "
    return existing_text + separator + ", ".join(additions)


def _decode_xp_keywords(value: Any) -> list[str]:
    if not value:
        return []
    raw = bytes(value) if isinstance(value, (tuple, list)) else value
    if not isinstance(raw, bytes):
        raise ValueError("Existing EXIF keywords have an unsupported encoding")
    try:
        text = raw.decode("utf-16le").rstrip("\x00")
    except UnicodeDecodeError as exc:
        raise ValueError("Existing EXIF keywords cannot be decoded safely") from exc
    return [part.strip() for part in text.replace(",", ";").split(";") if part.strip()]


def _atomic_write_text(path: str, text: str, parent_dir: str) -> None:
    temp_path: str | None = None
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=parent_dir, prefix=".imagesorter-sidecar-", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        sync_directory(parent_dir)
    finally:
        if temp_path and os.path.lexists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _restore_sidecar(path: str, original: bytes | None, parent_dir: str, attributes: dict[str, Any] | None = None) -> None:
    if original is None:
        if os.path.lexists(path):
            os.remove(path)
            sync_directory(os.path.dirname(path))
        return
    temp_path: str | None = None
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=parent_dir, prefix=".imagesorter-sidecar-restore-", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        if attributes is not None:
            safe.apply_attributes(temp_path, attributes)
        os.replace(temp_path, path)
        sync_directory(os.path.dirname(path))
    finally:
        if temp_path and os.path.lexists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def write_metadata(
    filepath: str,
    tags: list[str],
    write_exif: bool = True,
    write_sidecar: bool = False,
) -> None:
    """Merge metadata transactionally, restoring prior bytes if any write fails."""
    sanitized = sanitize_tags(tags)
    if not sanitized:
        return
    if write_exif or write_sidecar:
        require_mutation_support()

    real_image_path = os.path.realpath(filepath)
    if not os.path.isfile(real_image_path) or os.path.islink(filepath):
        raise ValueError(f"Metadata target is not a regular file: {filepath}")
    parent_dir = os.path.dirname(real_image_path) or "."

    sidecar_path = real_image_path + ".txt"
    real_sidecar = os.path.realpath(sidecar_path)
    if os.path.commonpath([parent_dir, real_sidecar]) != parent_dir:
        raise ValueError(f"Path traversal detected: {sidecar_path} escapes {parent_dir}")
    if os.path.islink(sidecar_path):
        raise ValueError(f"Sidecar target is a symbolic link: {sidecar_path}")

    # The direct helper is also a mutation entry point. Prove the actual
    # parent before any backup/replacement, preserving unknown != empty.
    safe.probe_directory(parent_dir)
    image_attributes = safe.fingerprint(real_image_path)
    sidecar_attributes = safe.fingerprint(sidecar_path) if write_sidecar and os.path.lexists(sidecar_path) else None
    sidecar_original: bytes | None = None
    sidecar_text = ""
    if write_sidecar and os.path.exists(sidecar_path):
        try:
            with open(sidecar_path, "rb") as handle:
                sidecar_original = handle.read()
            sidecar_text = sidecar_original.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise OSError(
                f"Cannot safely read existing sidecar {sidecar_path}: {exc}"
            ) from exc

    exif_bytes: bytes | None = None
    if write_exif and real_image_path.lower().endswith((".jpg", ".jpeg")):
        try:
            exif_dict: dict[str, Any] = piexif.load(real_image_path)
            zero_ifd = exif_dict.setdefault("0th", {})
            existing = _decode_xp_keywords(
                zero_ifd.get(piexif.ImageIFD.XPKeywords)
            )
            # Count/length restrictions apply to model output, never existing
            # user metadata. Refuse oversized final EXIF without changing it.
            seen = {tag.casefold() for tag in existing}
            merged = existing + [tag for tag in sanitized if tag.casefold() not in seen]
            zero_ifd[piexif.ImageIFD.XPKeywords] = (
                ";".join(merged) + "\x00"
            ).encode("utf-16le")
            exif_bytes = piexif.dump(exif_dict)
        except Exception as exc:
            raise OSError(
                f"Existing EXIF cannot be parsed safely; image was not modified: {exc}"
            ) from exc
        if len(exif_bytes) > 32768:
            raise OSError("EXIF payload exceeds the 32 KiB safety limit")

    directory = safe.private_directory(parent_dir, ".imagesorter-metadata-")
    image_backup: str | None = None
    image_temp: str | None = None
    sidecar_backup: str | None = None
    sidecar_temp: str | None = None
    retain_backups = False
    image_replace_started = sidecar_replace_started = False
    try:
        if write_sidecar and sidecar_original is not None:
            assert sidecar_attributes is not None
            safe.verify(sidecar_path, sidecar_attributes)
            sidecar_backup = os.path.join(directory, "sidecar-backup")
            safe.secure_copy(sidecar_path, sidecar_backup, sidecar_attributes)
            safe.apply_attributes(sidecar_backup, sidecar_attributes)
        if exif_bytes is not None:
            image_backup = os.path.join(directory, "exif-backup")
            safe.secure_copy(real_image_path, image_backup, image_attributes)
            safe.apply_attributes(image_backup, image_attributes)
            image_temp = os.path.join(directory, "exif-stage")
            safe.secure_copy(real_image_path, image_temp, image_attributes)
            piexif.insert(exif_bytes, image_temp)
            safe.apply_attributes(image_temp, image_attributes)
        if write_sidecar:
            sidecar_temp = os.path.join(directory, "sidecar-stage")
            merged_sidecar = merge_sidecar_content(sidecar_text, sanitized)
            _atomic_write_text(sidecar_temp, merged_sidecar, directory)
            safe.apply_attributes(sidecar_temp, sidecar_attributes or {"mode": 0o600, "xattrs": {}})
        # Every member's actual staged attributes must round-trip before any
        # original is replaced. Recheck source identity after all preparation.
        safe.verify(real_image_path, image_attributes)
        if write_sidecar:
            if sidecar_attributes is not None:
                safe.verify(sidecar_path, sidecar_attributes)
            elif os.path.lexists(sidecar_path):
                raise safe.FileChangedError("An unrelated sidecar appeared during metadata preparation")
        sync_directory(directory)
        if image_temp is not None:
            image_replace_started = True
            os.replace(image_temp, real_image_path)
            sync_directory(parent_dir)
            image_temp = None
        if sidecar_temp is not None:
            sidecar_replace_started = True
            os.replace(sidecar_temp, sidecar_path)
            sync_directory(parent_dir)
            sidecar_temp = None
    except Exception as exc:
        restore_errors: list[str] = []
        if image_replace_started and image_backup and os.path.exists(image_backup):
            try:
                os.replace(image_backup, real_image_path)
                sync_directory(parent_dir)
                image_backup = None
            except OSError as restore_exc:
                restore_errors.append(f"image restore failed: {restore_exc}")
        if sidecar_replace_started:
            try:
                _restore_sidecar(sidecar_path, sidecar_original, directory, sidecar_attributes)
            except OSError as restore_exc:
                restore_errors.append(f"sidecar restore failed: {restore_exc}")
        detail = f" ({'; '.join(restore_errors)})" if restore_errors else ""
        if restore_errors:
            retain_backups = True
            retained = [path for path in (image_backup, sidecar_backup) if path and os.path.lexists(path)]
            raise MetadataRecoveryError(f"Metadata transaction failed: {exc}{detail}; retained backups: {retained}", retained) from exc
        raise OSError(f"Metadata transaction failed: {exc}{detail}") from exc
    finally:
        for artifact in ((image_temp, sidecar_temp) if retain_backups else (image_temp, sidecar_temp, image_backup, sidecar_backup)):
            if artifact and os.path.lexists(artifact):
                try:
                    os.remove(artifact)
                except OSError:
                    logger.warning(f"Could not remove metadata artifact {artifact}")
        try:
            os.rmdir(directory)  # Unexpected or retained entries keep it intact.
            sync_directory(parent_dir)
        except OSError:
            pass
