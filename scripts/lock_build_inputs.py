"""Preserve exact binary build inputs from an already tested isolated environment.

This explicitly downloads wheels into a new evidence directory; it never changes
the invoking interpreter. The resulting lock can recreate that environment with
pip --require-hashes --no-index --find-links wheels -r requirements.lock.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700)
    wheels = output / "wheels"
    wheels.mkdir()
    installed = {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()
                 if dist.metadata["Name"].lower() != "imagesorter"}
    pins = [f"{name}=={version}" for name, version in sorted(installed.items())]
    with (output / "download.log").open("xb") as log:
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
                        "--dest", str(wheels), *pins], stdout=log, stderr=subprocess.STDOUT, check=True)
    requirements, records = [], []
    for path in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA") and name.count("/") == 1]
            if len(names) != 1:
                raise ValueError("Ambiguous wheel metadata")
            metadata = email.message_from_bytes(archive.read(names[0]))
        digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        name, version = metadata["Name"], metadata["Version"]
        if installed.get(name) != version:
            raise ValueError(f"Resolved wheel differs from tested environment: {name}")
        requirements.append(f"{name}=={version} --hash=sha256:{digest}")
        records.append({"name": name, "version": version, "file": path.name,
                        "sha256": digest, "size": path.stat().st_size})
    if len(records) != len(installed):
        raise ValueError("Build input inventory is incomplete")
    (output / "requirements.lock").write_text("\n".join(requirements) + "\n")
    (output / "build-inputs.json").write_text(json.dumps({"schema_version": 1,
        "python": sys.version, "platform": platform.platform(), "libc": platform.libc_ver(),
        "scope": "Exact complete tested environment, including build/test-only tools; not a runtime-only SBOM",
        "wheels": records}, indent=2) + "\n")
    print(json.dumps({"output": str(output), "wheels": len(records)}))


if __name__ == "__main__":
    main()
