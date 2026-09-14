"""Distinguish attested Python site startup files from .pth model checkpoints.

An extension or basename is never an exemption. The caller first verifies the
installed build inventory; this module requires exact file/RECORD/METADATA bytes,
unique distribution ownership and bounded valid site-startup text. It executes
none of that text. Unattested, changed, binary or out-of-root .pth remains a model
attempt. Raw traces always remain available for independent review.
"""
from __future__ import annotations

import ast
import base64
import csv
import hashlib
import io
import os
import re
import stat
from email.parser import Parser
from pathlib import Path


def _bytes(path, maximum):
    if path != path.resolve(strict=True):
        raise ValueError("Startup evidence must not traverse symbolic links")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("Startup evidence is not a bounded regular file")
        data = handle.read(maximum + 1)
        after = os.fstat(handle.fileno())
        keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(data) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in keys):
            raise ValueError("Startup evidence changed while reading")
        return data


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _startup_text(data, directory):
    if not data or b"\0" in data or data.startswith(b"PK"):
        return False
    try:
        lines = data.decode("utf-8").splitlines()
        active = False
        for line in lines:
            if not line.strip() or line.startswith("#"):
                continue
            active = True
            if line.startswith(("import ", "import\t")):
                ast.parse(line)  # Parse only; never evaluate startup instructions.
            else:
                relative = Path(line.rstrip())
                target = (directory / relative).resolve(strict=True)
                if relative.is_absolute() or not target.is_dir() or not target.is_relative_to(directory):
                    return False
        return active
    except (UnicodeError, SyntaxError, OSError, ValueError):
        return False


def attested_startup_files(build):
    """Derive exact startup provenance from a previously verified build record."""
    root = Path(build["runtime_root"]).resolve(strict=True)
    inventory = {item["relative_path"]: item["sha256"] for item in build["runtime_files"]}
    accepted = {}
    for name, expected in sorted(inventory.items()):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid installed inventory path")
        path = root / relative
        if path.suffix != ".pth" or path.parent.name not in {"site-packages", "dist-packages"}:
            continue
        try:
            data = _bytes(path, 65536)
            if _digest(data) != expected or not _startup_text(data, path.parent):
                continue
            owners, claims = [], 0
            for record_path in sorted(path.parent.glob("*.dist-info/RECORD")):
                metadata_path = record_path.parent / "METADATA"
                record_name, metadata_name = (p.relative_to(root).as_posix() for p in (record_path, metadata_path))
                if record_name not in inventory or metadata_name not in inventory:
                    continue
                record_data, metadata_data = _bytes(record_path, 8 * 1024 * 1024), _bytes(metadata_path, 1024 * 1024)
                if _digest(record_data) != inventory[record_name] or _digest(metadata_data) != inventory[metadata_name]:
                    continue
                rows = [row for row in csv.reader(io.StringIO(record_data.decode("utf-8"))) if row and row[0] == path.name]
                claims += len(rows)
                encoded = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
                if len(rows) != 1 or len(rows[0]) != 3 or rows[0][1:] != [encoded, str(len(data))]:
                    continue
                metadata = Parser().parsestr(metadata_data.decode("utf-8"))
                if not metadata.get("Name") or not metadata.get("Version"):
                    continue
                owners.append({"path": str(path), "sha256": expected, "size": len(data),
                               "distribution": metadata["Name"], "version": metadata["Version"],
                               "record": {"path": str(record_path), "sha256": inventory[record_name]},
                               "metadata": {"path": str(metadata_path), "sha256": inventory[metadata_name]},
                               "record_entry": rows[0]})
            if len(owners) == 1 and claims == 1:
                accepted[str(path)] = owners[0]
        except (OSError, UnicodeError, ValueError, csv.Error):
            continue  # Unknown evidence remains a model attempt, never an exemption.
    return accepted


def is_model_open(line, startup_files=None):
    if not re.search(r"\b(open|openat|openat2)\(", line) or not re.search(r"\.(onnx|safetensors|pt|pth)(?:\"|')", line):
        return False
    match = re.search(r'"(?:\\.|[^"\\])*"', line)
    if not match:
        return True
    try:
        target = ast.literal_eval(match.group())
        evidence = (startup_files or {}).get(target)
        if not evidence or not target.endswith(".pth"):
            return True
        if _digest(_bytes(Path(target), 65536)) != evidence["sha256"]:
            return True
        for name, maximum in (("record", 8 * 1024 * 1024), ("metadata", 1024 * 1024)):
            item = evidence[name]
            if _digest(_bytes(Path(item["path"]), maximum)) != item["sha256"]:
                return True
        return False
    except (OSError, ValueError, SyntaxError, KeyError):
        return True
