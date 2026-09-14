"""Adversarial component-store tests use tiny inert, explicitly trusted fixtures."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import threading
import time

import pytest

from imagesorter.component_manager import (
    ComponentBusy,
    ComponentError,
    ComponentManager,
)
from imagesorter.component_runtime import _inference_receipt, _run, _snapshot

CID = "ai.mobilenet-v2"


def pack(tmp_path, version="1", members=None, payload=b"verified bytes"):
    archive = tmp_path / f"pack-{version}.tar.gz"
    members = members or [("data.bin", payload, tarfile.REGTYPE)]
    with tarfile.open(archive, "w:gz") as output:
        for name, content, kind in members:
            item = tarfile.TarInfo(name)
            item.type, item.size, item.mode = kind, len(content), 0o600
            if kind == tarfile.SYMTYPE:
                item.linkname = "/etc/passwd"
                item.size = 0
            output.addfile(item, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    descriptor = {"id": CID, "version": version, "protocol_version": 1,
        "platform": "any", "archive_size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "installed_size": len(payload), "files": {"data.bin": {
            "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload), "mode": 0o600}}}
    return archive, descriptor


def manager(tmp_path, descriptors):
    root = tmp_path / "store"
    root.mkdir(mode=0o700, exist_ok=True)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema_version": 1, "components": descriptors}))
    return ComponentManager(root, catalog)


def test_no_network_from_construction_listing_or_empty_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("Unexpected network"))
    store = manager(tmp_path, [])
    assert len(store.status()) == 5
    assert store.recover_jobs() == []


def test_install_disable_reenable_verify_remove(tmp_path):
    archive, descriptor = pack(tmp_path)
    store = manager(tmp_path, [descriptor])
    installed = store.install(CID, archive)
    assert (installed / "data.bin").read_bytes() == b"verified bytes"
    assert (installed / "data.bin").stat().st_mode & 0o777 == 0o600
    store.verify(CID)
    store.enable(CID, False)
    assert store.active_path(CID) is None
    store.enable(CID)
    store.remove(CID)
    assert store.active_path(CID) is None
    assert not installed.exists()


def test_update_rollback_and_cancel_preserve_previous(tmp_path):
    archive1, one = pack(tmp_path, "1")
    store = manager(tmp_path, [one])
    original = store.install(CID, archive1)
    archive2, two = pack(tmp_path, "2", payload=b"new version")
    store = manager(tmp_path, [two, one])
    with pytest.raises(InterruptedError):
        store.install(CID, archive2, cancelled=lambda: True)
    assert store.active_path(CID) == original
    store.install(CID, archive2)
    store.rollback(CID)
    assert store.active_path(CID) == original
    assert (original / "data.bin").read_bytes() == b"verified bytes"


@pytest.mark.parametrize("members", [
    [("../outside", b"x", tarfile.REGTYPE)],
    [("/absolute", b"x", tarfile.REGTYPE)],
    [("data.bin", b"", tarfile.SYMTYPE)],
    [("data.bin", b"", tarfile.FIFOTYPE)],
    [("data.bin", b"verified bytes", tarfile.REGTYPE)] * 2,
    [("data.bin", b"oversized payload", tarfile.REGTYPE)],
    [("unexpected-executable", b"code", tarfile.REGTYPE)],
])
def test_hostile_archives_never_activate_or_touch_outside(tmp_path, members):
    sentinel = tmp_path / "outside"
    sentinel.write_bytes(b"unrelated")
    archive, descriptor = pack(tmp_path, members=members)
    store = manager(tmp_path, [descriptor])
    with pytest.raises(ComponentError):
        store.install(CID, archive)
    assert store.active_path(CID) is None
    assert sentinel.read_bytes() == b"unrelated"
    assert all(json.loads(path.read_text())["state"] == "failed" for path in store.jobs_dir.glob("*.json"))


def test_corruption_does_not_replace_active_version(tmp_path):
    archive, one = pack(tmp_path)
    store = manager(tmp_path, [one])
    original = store.install(CID, archive)
    archive2, two = pack(tmp_path, "2", payload=b"new version")
    archive2.write_bytes(b"not a valid archive")
    store = manager(tmp_path, [two, one])
    with pytest.raises(ComponentError):
        store.install(CID, archive2)
    assert store.active_path(CID) == original


def test_modified_local_manifest_cannot_authorize_changed_executable(tmp_path):
    archive, descriptor = pack(tmp_path)
    store = manager(tmp_path, [descriptor])
    installed = store.install(CID, archive)
    (installed / "data.bin").write_bytes(b"tampered")
    descriptor["files"]["data.bin"].update(size=8, sha256=hashlib.sha256(b"tampered").hexdigest())
    descriptor["installed_size"] = 8
    (installed / ".installed.json").write_text(json.dumps(descriptor))
    with pytest.raises(ComponentError, match="not authorized"):
        store.verify(CID)


@pytest.mark.skipif(os.name != "posix", reason="POSIX shared lease semantics")
def test_removal_refuses_active_version_lease(tmp_path):
    archive, descriptor = pack(tmp_path)
    store = manager(tmp_path, [descriptor])
    installed = store.install(CID, archive)
    with store.acquire(CID):
        with pytest.raises(ComponentBusy):
            store.remove(CID)
        assert installed.exists()
    store.remove(CID)
    assert not installed.exists()


def test_interrupted_job_recovery_is_idempotent_and_preserves_staging(tmp_path):
    store = manager(tmp_path, [])
    staged = store.root / ".install-interrupted"
    staged.mkdir()
    (staged / "unresolved").write_bytes(b"retain")
    job = store.jobs_dir / "interrupted.json"
    job.write_text(json.dumps({"id": "interrupted", "component_id": CID, "version": "1",
                               "state": "extracting", "staging": str(staged)}))
    assert store.recover_jobs()[0]["state"] == "recovery_required"
    assert store.recover_jobs()[0]["state"] == "recovery_required"
    assert next(row for row in store.status() if row["id"] == CID)["pending_jobs"][0]["staging"] == str(staged)
    assert (staged / "unresolved").read_bytes() == b"retain"


def test_symbolic_link_store_and_jobs_fail_closed(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"schema_version":1,"components":[]}')
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ComponentError):
        ComponentManager(link, catalog)
    (target / "jobs").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ComponentError):
        ComponentManager(target, catalog)


def test_snapshot_rejects_symlink_and_is_read_only(tmp_path):
    source = tmp_path / "image.jpg"
    source.write_bytes(b"fixture")
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    target = _snapshot(source, work)
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o400
    link = tmp_path / "link.jpg"
    link.symlink_to(source)
    with pytest.raises(ComponentError):
        _snapshot(link, work)


def test_inference_receipt_preserves_scores_and_rejects_nan(tmp_path):
    store = manager(tmp_path, [])
    result = _inference_receipt({"tags": [{"tag": "cat", "score": .75}]}, tmp_path, store, "GPU error")
    assert result["tags"] == ["cat"]
    assert result["fallback_reason"] == "GPU error"
    assert result["model_component_version"] == "explicit-verified"
    with pytest.raises(ComponentError):
        _inference_receipt({"tags": [{"tag": "cat", "score": float("nan")}]}, tmp_path, store, None)


@pytest.mark.skipif(os.name != "posix", reason="POSIX shared lease behavior")
def test_cross_thread_removal_is_prompt_while_unrelated_listing_works(tmp_path):
    archive, descriptor = pack(tmp_path)
    store = manager(tmp_path, [descriptor])
    store.install(CID, archive)
    outcomes = []
    def remove():
        try:
            store.remove(CID)
        except ComponentBusy:
            outcomes.append("busy")
    with store.acquire(CID):
        started = time.monotonic()
        thread = threading.Thread(target=remove)
        thread.start()
        thread.join(.5)
        assert not thread.is_alive()
        assert outcomes == ["busy"] and time.monotonic() - started < .5
        assert len(store.status()) == 5


def test_failed_completion_receipt_reconciles_committed_activation(tmp_path, monkeypatch):
    import imagesorter.component_manager as module
    archive, descriptor = pack(tmp_path)
    store = manager(tmp_path, [descriptor])
    write = module._write_json
    def fail_completion(path, value):
        if value.get("state") == "completed":
            raise OSError("Injected disk-full receipt failure")
        return write(path, value)
    monkeypatch.setattr(module, "_write_json", fail_completion)
    with pytest.raises(ComponentError, match="reconciliation"):
        store.install(CID, archive)
    assert store.active_path(CID) is not None
    job = next(store.jobs_dir.glob("*.json"))
    assert json.loads(job.read_text())["state"] == "activating"
    monkeypatch.setattr(module, "_write_json", write)
    assert store.recover_jobs()[0]["state"] == "completed"
    assert store.recover_jobs() == []


@pytest.mark.skipif(os.name != "posix", reason="FIFO special file race")
def test_snapshot_open_replacement_with_fifo_does_not_block(tmp_path, monkeypatch):
    import imagesorter.component_runtime as module
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    work = tmp_path / "reader"
    work.mkdir()
    original_open = os.open
    def replacement(path, flags, *args):
        if path == source:
            source.unlink()
            os.mkfifo(source)
        return original_open(path, flags, *args)
    monkeypatch.setattr(module.os, "open", replacement)
    started = time.monotonic()
    with pytest.raises(ComponentError, match="non-regular"):
        _snapshot(source, work)
    assert time.monotonic() - started < .5


def test_offline_legacy_import_recreates_exact_pack_and_preserves_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("Legacy import attempted network"))
    content = {"mobilenetv2.onnx": b"fixture-model", "labels.txt": b"fixture-labels\n",
               "licenses/notice.txt": b"fixture-license"}
    archive = tmp_path / "model.tar.gz"
    with archive.open("xb") as output, gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as package:
            for name, value in sorted(content.items()):
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(value), 0o600, 0
                package.addfile(member, io.BytesIO(value))
    descriptor = {"id": CID, "version": "legacy-test", "protocol_version": 1, "platform": "any",
        "archive_size": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "installed_size": sum(map(len, content.values())), "files": {name: {
            "sha256": hashlib.sha256(value).hexdigest(), "size": len(value), "mode": 0o600}
            for name, value in content.items()},
        "import_content": {"licenses/notice.txt": base64.b64encode(content["licenses/notice.txt"]).decode()}}
    source = tmp_path / "legacy"
    source.mkdir()
    for name in ("mobilenetv2.onnx", "labels.txt"):
        (source / name).write_bytes(content[name])
    store = manager(tmp_path, [descriptor])
    installed = store.import_legacy_model(source)
    for name, value in content.items():
        assert (installed / name).read_bytes() == value
    assert (source / "mobilenetv2.onnx").read_bytes() == content["mobilenetv2.onnx"]
    (source / "labels.txt").write_bytes(b"changed")
    with pytest.raises(ComponentError):
        store.import_legacy_model(source)
    assert store.active_path(CID) == installed


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable fixture and FIFO")
def test_local_archive_fifo_rejected_promptly(tmp_path):
    archive, descriptor = pack(tmp_path)
    archive.unlink()
    os.mkfifo(archive)
    store = manager(tmp_path, [descriptor])
    started = time.monotonic()
    with pytest.raises(ComponentError, match="regular"):
        store.install(CID, archive)
    assert time.monotonic() - started < .5


@pytest.mark.skipif(sys.platform != "linux", reason="Linux qualified executable probe fixture")
def test_noisy_probe_is_bounded_and_reaped(tmp_path):
    from imagesorter.reader_sandbox import landlock_abi
    if landlock_abi() < 3:
        pytest.skip("Host lacks qualified helper containment")
    store = manager(tmp_path, [])
    helper = tmp_path / "noisy"
    helper.write_text(f"#!{sys.executable}\nimport os, time\nos.write(1, b'x' * 70000)\ntime.sleep(60)\n")
    helper.chmod(0o700)
    started = time.monotonic()
    with pytest.raises(ComponentError, match="bounded"):
        store._probe(tmp_path, {"id": "viewer.animation-multipage", "version": "1", "entrypoint": helper.name,
                               "reader_policy_version": 1, "min_landlock_abi": 3})
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("action", ["decode_frame", "inspect"])
@pytest.mark.parametrize("patch", [
    {"component_version": "stale"}, {"request_id": "stale"}, {"generation": True},
    {"protocol_version": True}, {"ok": "yes"}, {"payload_length": True},
    {"width": True}, {"stride": 8}, {"payload_sha256": "0" * 64},
    {"frame": 1}, {"frame_count": 0}, {"duration_ms": -1}, {"loop_count": -1},
    {"original_size": [True, 1]},
])
def test_malformed_reader_replies_are_rejected(tmp_path, patch, action):
    payload = b"\xff\0\0\xff"
    response = {"protocol_version": 1, "request_id": "request", "generation": 0,
                "component_id": "viewer.animation-multipage", "component_version": "1",
                "ok": True, "payload_length": 4, "width": 1, "height": 1, "stride": 4,
                "pixel_format": "RGBA", "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "frame": 0, "frame_count": 1, "duration_ms": 0, "loop_count": 0, "original_size": [1, 1]}
    if action == "inspect":
        response["payload_length"], payload = 0, b""
        if "payload_sha256" in patch:
            patch = {"height": 0}  # Inspection has no pixel payload to hash.
    response.update(patch)
    raw = json.dumps(response).encode() + b"\n" + payload
    command = [sys.executable, "-c", "import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write(" + repr(raw) + ")"]
    with pytest.raises(ComponentError):
        _run(command, {"action": action, "component_id": "viewer.animation-multipage",
                     "component_version": "1", "request_id": "request", "frame": 0}, cwd=tmp_path, timeout=3)


def test_fast_successful_reader_cannot_overflow_stderr(tmp_path):
    response = {"protocol_version": 1, "request_id": "request", "component_id": "core.cpu",
                "component_version": "base", "ok": True, "payload_length": 0}
    command = [sys.executable, "-c", "import os, sys; sys.stdin.buffer.readline(); "
               "os.write(2, b'x' * 1048577); sys.stdout.buffer.write(" + repr(json.dumps(response).encode() + b"\n") + ")"]
    with pytest.raises(ComponentError, match="bounded"):
        _run(command, {"action": "infer", "request_id": "request", "component_id": "core.cpu"}, cwd=tmp_path, timeout=3)
