from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

from imagesorter import __version__


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_manifest(dist_dir: Path, source_sha: str) -> tuple[Path, Path]:
    patterns = ("*.whl", "*.AppImage", "*.tar.gz", "*.zip")
    artifacts = sorted(
        {path for pattern in patterns for path in dist_dir.glob(pattern) if path.is_file()},
        key=lambda path: path.name,
    )
    if not artifacts:
        raise RuntimeError(f"No release artifacts found in {dist_dir}")
    hashes = {path.name: sha256(path) for path in artifacts}
    manifest = {
        "schema_version": 1,
        "app_version": __version__,
        "source_sha": source_sha,
        "build_environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "architecture": platform.machine(),
        },
        "platform_status": {
            "ubuntu_24_04_x86_64": "primary",
            "windows": "experimental",
            "macos": "experimental",
        },
        "artifacts_sha256": hashes,
    }
    manifest_path = dist_dir / "release_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksum_path = dist_dir / "checksums.txt"
    checksum_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
    )
    return manifest_path, checksum_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args(argv)
    try:
        manifest, checksums = create_manifest(args.dist, args.source_sha)
    except Exception as exc:
        print(f"Manifest generation failed: {exc}", file=sys.stderr)
        return 1
    print(manifest)
    print(checksums)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
