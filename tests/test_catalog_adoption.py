"""Exercise generated catalogue history, not only hand-written store fixtures."""
from __future__ import annotations

import json
import sys

import pytest

from imagesorter.component_manager import ComponentManager
from scripts import build_component_packs as builder
from tests.test_component_manager import CID, pack


def generate(monkeypatch, output, previous, descriptor):
    # The tiny inert archive is supplied by the fixture; only expensive pack
    # compilation is replaced. Catalogue generation/adoption runs unchanged.
    def build(component_id, destination, *args):
        assert component_id == CID
        job = destination / component_id
        job.mkdir()
        builder.write_json(job / "descriptor.json", descriptor)
        return descriptor

    monkeypatch.setattr(builder, "build", build)
    monkeypatch.setattr(builder.platform, "system", lambda: "Linux")
    monkeypatch.setattr(builder.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sys, "argv", ["build_component_packs", "--output", str(output),
        "--component", CID, "--version", descriptor["version"],
        "--release-tag", "components-fixture", "--previous-catalog", str(previous)])
    assert builder.main() == 0
    return output / "component_catalog.json"


def test_generated_history_supports_upgrade_and_rollback_after_adoption(tmp_path, monkeypatch):
    old_archive, old = pack(tmp_path, "1")
    old_catalog = tmp_path / "previous.json"
    old_catalog.write_text(json.dumps({"schema_version": 1, "components": [old]}))
    previous_bytes = old_catalog.read_bytes()
    store_root = tmp_path / "store"
    store = ComponentManager(store_root, old_catalog)
    original = store.install(CID, old_archive)
    new_archive, new = pack(tmp_path, "2", payload=b"new verified bytes")
    catalog = generate(monkeypatch, tmp_path / "build", old_catalog, new)
    assert json.loads(catalog.read_text())["components"] == [new, old]
    assert old_catalog.read_bytes() == previous_bytes
    # Reconstructing the manager simulates an application update adopting the
    # generated catalogue, including reopening after the new version activates.
    upgraded = ComponentManager(store_root, catalog)
    upgraded.install(CID, new_archive)
    reopened = ComponentManager(store_root, catalog)
    assert reopened.active_path(CID) != original
    reopened.rollback(CID)
    assert reopened.active_path(CID) == original
    assert (original / "data.bin").read_bytes() == b"verified bytes"
    reopened.verify(CID)


def test_generated_history_deduplicates_identical_version(tmp_path, monkeypatch):
    _, descriptor = pack(tmp_path)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"components": [descriptor]}))
    catalog = generate(monkeypatch, tmp_path / "build", previous, descriptor)
    assert json.loads(catalog.read_text())["components"] == [descriptor]


def test_conflicting_previous_version_never_publishes_catalog(tmp_path, monkeypatch):
    _, descriptor = pack(tmp_path)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"components": [{**descriptor, "sha256": "0" * 64}]}))
    before = previous.read_bytes()
    output = tmp_path / "build"
    with pytest.raises(RuntimeError, match="reused with a different identity"):
        generate(monkeypatch, output, previous, descriptor)
    assert not (output / "component_catalog.json").exists()
    assert previous.read_bytes() == before
