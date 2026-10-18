"""Opt-in structured readiness evidence, constrained to an isolated profile."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

_path = None
_root = None
_record = {}

def configure(path):
    global _path, _root, _record
    from .paths import get_portable_root
    explicit = os.environ.get("IMAGESORTER_PROFILE_ROOT")
    root = Path(explicit).resolve() if explicit else get_portable_root()
    candidate = Path(path).resolve()
    if root is None or candidate == root or not candidate.is_relative_to(root):
        raise ValueError("Diagnostic receipt must be inside the selected explicit or portable profile")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    _path = candidate
    _root = root
    _record = {"schema_version": 1, "pid": os.getpid(), "profile_root": str(root),
               "started_monotonic": time.monotonic(), "events": []}

def record(event, **fields):
    if _path is None:
        return
    # Detect an already-redirected path; this is not a concurrent filesystem lock.
    if not _path.resolve().is_relative_to(_root):
        raise ValueError("Diagnostic receipt path escapes the selected profile")
    if event == "image_presented":
        _record["build_identity"] = fields.get("build_identity")
    _record["events"].append({"event": event, "monotonic": time.monotonic(), **fields})
    # Exclusive same-directory staging cannot follow a pre-existing .tmp link.
    descriptor, name = tempfile.mkstemp(prefix=".readiness-", suffix=".tmp", dir=_path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(_record, output, indent=2)
        os.replace(temporary, _path)
    finally:
        temporary.unlink(missing_ok=True)
