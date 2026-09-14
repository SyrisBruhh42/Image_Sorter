"""Explicit disposable-process capability check under the Linux leaf policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args()
    scratch = args.output.resolve()
    if not scratch.is_dir() or any(scratch.iterdir()):
        raise ValueError("Policy probe requires a new empty private output directory")
    os.chdir(scratch)
    for name in ("HOME", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
        os.environ[name] = str(scratch)
    os.environ["ONNXRUNTIME_DISABLE_TELEMETRY"] = "1"
    tempfile.tempdir = str(scratch)
    from imagesorter import reader_sandbox
    source = hashlib.sha256(Path(reader_sandbox.__file__).read_bytes()).hexdigest()
    try:
        policy = reader_sandbox.restrict_leaf_reader(scratch, gpu=args.gpu)
        from imagesorter.component_worker import probe
        result = {"passed": True, "policy": policy,
                  "probe": probe("provider.onnx-nvidia" if args.gpu else "core.cpu")}
    except Exception as exc:
        result = {"passed": False, "error": str(exc), "traceback": traceback.format_exc()}
    result.update(policy_source_sha256=source, complete=False,
                  scope="Contained capability probe, not final application qualification")
    (scratch / "probe.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(scratch), "result": result}), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
