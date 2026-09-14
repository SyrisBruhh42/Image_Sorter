"""Model provenance and removal leases must name the exact snapshot version."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from imagesorter import component_runtime
from imagesorter.component_manager import (
    ComponentBusy,
    ComponentError,
    ComponentManager,
)

CID = "ai.mobilenet-v2"
PAYLOADS = {"mobilenetv2.onnx": b"same pinned model", "labels.txt": b"same pinned labels"}


def _pack(directory: Path, version: str):
    archive = directory / f"model-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for name, content in PAYLOADS.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(content), 0o600
            output.addfile(member, io.BytesIO(content))
    descriptor = {
        "id": CID, "version": version, "protocol_version": 1, "platform": "any",
        "archive_size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "installed_size": sum(map(len, PAYLOADS.values())),
        "files": {name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                          "mode": 0o600} for name, content in PAYLOADS.items()},
    }
    return archive, descriptor


@pytest.fixture
def versions(tmp_path, monkeypatch):
    first_archive, first_descriptor = _pack(tmp_path, "1")
    second_archive, second_descriptor = _pack(tmp_path, "2")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema_version": 1,
                                   "components": [first_descriptor, second_descriptor]}))
    manager = ComponentManager(tmp_path / "store", catalog)
    first = manager.install(CID, first_archive)
    manager.catalog[CID] = second_descriptor
    second = manager.install(CID, second_archive)
    manager.rollback(CID)
    source = tmp_path / "image.png"
    source.write_bytes(b"read-only input fixture")
    # These tests exercise real store locking, verified trees, and snapshots;
    # inference computation is not relevant to the version-selection boundary.
    monkeypatch.setattr(component_runtime, "_run", lambda *_args, **_kwargs: ({"tags": []}, b""))
    return manager, first, second, source


def test_activation_switch_before_acquire_leases_requested_version(versions, monkeypatch):
    manager, first, second, source = versions
    acquire = manager.acquire_path
    snapshot = component_runtime._snapshot
    observed = []

    @contextlib.contextmanager
    def switching_acquire(component_id, selected_path):
        manager.rollback(CID)
        assert manager.active_path(CID) == second
        with acquire(component_id, selected_path) as leased:
            observed.append(("lease", leased[0]))
            yield leased

    def record_snapshot(path, directory):
        if path.name == "mobilenetv2.onnx":
            observed.append(("snapshot", path.parent))
        return snapshot(path, directory)

    monkeypatch.setattr(manager, "acquire_path", switching_acquire)
    monkeypatch.setattr(component_runtime, "_snapshot", record_snapshot)
    receipt = component_runtime.infer_component(str(source), str(first), hardware=False, manager=manager)
    assert observed == [("lease", first), ("snapshot", first)]
    assert receipt["model_component_version"] == "1"
    assert manager.active_path(CID) == second


def test_exact_model_cannot_be_removed_during_snapshot(versions, monkeypatch):
    manager, first, second, source = versions
    manager.rollback(CID)
    assert manager.active_path(CID) == second
    snapshot = component_runtime._snapshot
    attempted = []

    def competing_removal(path, directory):
        if path.name == "mobilenetv2.onnx":
            with pytest.raises(ComponentBusy):
                manager.remove(CID, first.name)
            attempted.append(path.parent)
        return snapshot(path, directory)

    monkeypatch.setattr(component_runtime, "_snapshot", competing_removal)
    receipt = component_runtime.infer_component(str(source), str(first), hardware=False, manager=manager)
    assert attempted == [first]
    assert receipt["model_component_version"] == first.name
    manager.remove(CID, first.name)
    assert not first.exists()
    assert second.is_dir()


@pytest.mark.parametrize("state", ["disabled", "removed", "recreated-unmanaged"])
def test_managed_model_never_falls_back_to_unleased_explicit_path(versions, monkeypatch, state):
    manager, first, _second, source = versions
    if state == "disabled":
        manager.enable(CID, False)
    else:
        manager.remove(CID, first.name)
        if state == "recreated-unmanaged":
            first.mkdir(mode=0o700)
            for name, content in PAYLOADS.items():
                (first / name).write_bytes(content)
    monkeypatch.setattr(component_runtime, "_run", lambda *_args, **_kwargs: pytest.fail("Unleased managed model reached inference"))
    with pytest.raises(ComponentError):
        component_runtime.infer_component(str(source), str(first), hardware=False, manager=manager)


@pytest.mark.skipif(os.name != "posix", reason="Managed symlink qualification uses POSIX symlinks")
def test_lexical_managed_symlink_cannot_escape_to_explicit_inference(versions, monkeypatch, tmp_path):
    manager, first, _second, source = versions
    external = tmp_path / "explicit-model"
    external.mkdir(mode=0o700)
    for name, content in PAYLOADS.items():
        (external / name).write_bytes(content)
    # Explicit external selection remains supported. A pathname inside the
    # managed store must not gain that exception by resolving outside it.
    receipt = component_runtime.infer_component(str(source), str(external), hardware=False, manager=manager)
    assert receipt["model_component_version"] == "explicit-verified"
    manager.remove(CID, first.name)
    first.symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(component_runtime, "_run", lambda *_args, **_kwargs: pytest.fail("Managed symlink reached unleased inference"))
    with pytest.raises(ComponentError, match="symbolic link"):
        component_runtime.infer_component(str(source), str(first), hardware=False, manager=manager)
    assert all((external / name).read_bytes() == content for name, content in PAYLOADS.items())
