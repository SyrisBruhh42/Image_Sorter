from __future__ import annotations

import json

import pytest

from scripts.create_release_manifest import create_manifest


def test_manifest_hashes_only_release_files(tmp_path):
    artifact = tmp_path / "imagesorter.whl"
    artifact.write_bytes(b"wheel")
    (tmp_path / "directory").mkdir()

    manifest_path, checksum_path = create_manifest(tmp_path, "abc123")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_sha"] == "abc123"
    assert list(manifest["artifacts_sha256"]) == ["imagesorter.whl"]
    checksum = checksum_path.read_text(encoding="utf-8")
    assert "imagesorter.whl" in checksum
    assert "checksums.txt" not in checksum


def test_manifest_refuses_an_empty_dist(tmp_path):
    with pytest.raises(RuntimeError, match="No release artifacts"):
        create_manifest(tmp_path, "abc123")
