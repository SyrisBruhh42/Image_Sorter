"""Exercise a hash-pinned installed viewer's bounded frame transport off-GUI.

These facts supplement, never replace, native playback timing and cancellation.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from qualify_components import generated_fixtures, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from imagesorter.component_manager import ComponentManager
    from imagesorter.component_runtime import decode_frames_component
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    manager = ComponentManager(output / "components", args.catalog)
    cid = "viewer.animation-multipage"
    manager.install(cid, args.archive)
    manager.verify(cid)
    fixtures = output / "fixtures"
    generated_fixtures(fixtures)
    cases = []
    for name in ("animation.gif", "animation.webp", "pages.tiff", "blend.apng"):
        path = fixtures / name
        before = sha(path)
        started = time.monotonic()
        try:
            batch = decode_frames_component(str(path), cid, [0, 1, 2], generation=91, manager=manager)
            colors = [tuple(pixels[:4]) for pixels, _metadata in batch]
            expected = [(255, 0, 0, 255), (0, 0, 255, 255), (0, 128, 0, 255)]
            if name.endswith("apng"):
                expected = [(255, 0, 0, 255), (127, 0, 128, 255), (127, 128, 0, 255)]
            if colors != expected or sha(path) != before:
                raise RuntimeError("Pixel oracle or immutable source check failed")
            if any(item["generation"] != 91 or item["frame"] != index or item["frame_count"] != 3
                   for index, (_pixels, item) in enumerate(batch)):
                raise RuntimeError("Frame batch identity mismatch")
            record = {"status": "passed", "frames": [item for _pixels, item in batch], "pixel_oracle": colors}
        except Exception as exc:
            record = {"status": "failed", "error": str(exc)}
        record.update(id=name, elapsed_seconds=time.monotonic() - started, fixture_sha256=before)
        cases.append(record)
        print(json.dumps(record), flush=True)
    (output / "frame-batch-evidence.json").write_text(json.dumps({"schema_version": 1,
        "complete": False, "scope": "Installed component batch transport, not native playback timing",
        "catalog_sha256": sha(args.catalog), "archive_sha256": sha(args.archive),
        "component": manager.catalog[cid], "cases": cases}, indent=2) + "\n")
    return 0 if all(item["status"] == "passed" for item in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
