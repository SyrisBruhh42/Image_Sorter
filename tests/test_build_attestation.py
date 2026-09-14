"""Source/export/build boundaries must bind actual bytes, not just a Git label."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("build_support_test", Path(__file__).resolve().parents[1] / "build_support.py")
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def test_inventory_includes_rebuild_files_and_export_rejects_tampering(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    source = root / "src/imagesorter/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("# test source\n")
    (root / "recipe.py").write_text("# exact rebuild recipe\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], check=True)
    identity = support.source_identity(root)
    assert identity["dirty"] is False and "recipe.py" in identity["source_files"]
    export = tmp_path / "export"
    export.mkdir()
    for name in identity["source_files"]:
        target = export / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    (export / "_source_identity.json").write_text(json.dumps(identity))
    assert support.source_identity(export) == identity
    (export / "recipe.py").write_text("# replaced build recipe\n")
    with pytest.raises(ValueError, match="recipe.py"):
        support.source_identity(export)


def test_frozen_module_origin_and_bytes_must_match_attestation(tmp_path):
    source = tmp_path / "src/imagesorter/main.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    identity = {"source_files": {"src/imagesorter/main.py": hashlib.sha256(b"source").hexdigest()}}
    support.verify_frozen_modules(tmp_path, identity, [("imagesorter.main", str(source), "PYMODULE")])
    with pytest.raises(ValueError, match="another checkout"):
        support.verify_frozen_modules(tmp_path, identity, [("imagesorter.main", str(tmp_path / "foreign.py"), "PYMODULE")])
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs"):
        support.verify_frozen_modules(tmp_path, identity, [("imagesorter.main", str(source), "PYMODULE")])
