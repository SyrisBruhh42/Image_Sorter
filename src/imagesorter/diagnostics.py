"""Opt-in structured readiness evidence, constrained to an explicit test profile."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_path = None
_record = {}

def configure(path):
    global _path, _record
    root = os.environ.get("IMAGESORTER_PROFILE_ROOT")
    candidate = Path(path).resolve()
    if not root or not candidate.is_relative_to(Path(root).resolve()):
        raise ValueError("Diagnostic receipt must be inside --profile-root")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    _path = candidate
    _record = {"schema_version": 1, "pid": os.getpid(), "profile_root": root,
               "started_monotonic": time.monotonic(), "events": []}

def record(event, **fields):
    if _path is None:
        return
    if event == "image_presented":
        _record["build_identity"] = fields.get("build_identity")
    _record["events"].append({"event": event, "monotonic": time.monotonic(), **fields})
    temporary = _path.with_suffix(".tmp")
    temporary.write_text(json.dumps(_record, indent=2), encoding="utf-8")
    os.replace(temporary, _path)
