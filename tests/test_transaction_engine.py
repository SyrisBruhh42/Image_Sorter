"""Adversarial checks exercise real files; faults occur at durable boundaries."""
from __future__ import annotations

import copy
import errno
import os
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path

import piexif
import pytest
from PIL import Image

from imagesorter import file_safety as safe
from imagesorter.metadata_io import MetadataRecoveryError, write_metadata
from imagesorter.model_assets import LABELS_SHA256, MODEL_SHA256
from imagesorter.operation_contracts import OperationState
from imagesorter.operation_engine import OperationEngine
from imagesorter.operation_journal import OperationJournal
from imagesorter.profile_lock import ProfileBusyError, ProfileLock


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))


@pytest.fixture
def environment(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "destination"
    source.mkdir()
    target.mkdir()
    image = source / "photo.jpg"
    image.write_bytes(b"original image bytes")
    journal = OperationJournal(str(tmp_path / "journal.db"))
    return image, target, journal, OperationEngine(journal)


def request(action, source, destination=None, **extra):
    if action == "metadata":
        extra.setdefault("component_receipt", {"provider": "CPUExecutionProvider", "cuda_compute_events": 0,
            "tensor_sha256": "a" * 64, "model_sha256": MODEL_SHA256, "labels_sha256": LABELS_SHA256,
            "model_component_version": "explicit-verified", "component_version": "base"})
    return {"operation_id": uuid.uuid4().hex, "action": action, "source_path": str(source),
            "destination_path": str(destination) if destination is not None else None, **extra}


@pytest.mark.parametrize("action", ["move", "copy", "trash"])
def test_orphan_sidecar_is_never_claimed_by_transfer_or_undo(environment, action):
    image, target, _journal, engine = environment
    orphan = target / "photo.jpg.txt"
    orphan.write_bytes(b"unrelated valuable note")
    result = engine.execute(request(action, image, target))
    assert result["state"] == OperationState.COMPLETED
    assert Path(result["destination_path"]).name == "photo_1.jpg"
    assert not result["undo_token"].get("companions")
    undone = engine.execute(request("undo_copy" if action == "copy" else "undo_move",
                                    result["destination_path"], undo_token=result["undo_token"]))
    assert undone["state"] == OperationState.COMPLETED
    assert orphan.read_bytes() == b"unrelated valuable note"


@pytest.mark.parametrize("action", ["move", "copy", "trash"])
def test_complete_file_sets_and_undo(environment, action):
    image, target, journal, engine = environment
    sidecar = Path(str(image) + ".txt")
    sidecar.write_bytes(b"human note\n")
    result = engine.execute(request(action, image, target))
    assert result["state"] == OperationState.COMPLETED
    current = Path(result["destination_path"])
    assert current.read_bytes() == b"original image bytes"
    assert Path(str(current) + ".txt").read_bytes() == b"human note\n"
    assert bool(image.exists()) is (action == "copy")
    undo = request("undo_copy" if action == "copy" else "undo_trash" if action == "trash" else "undo_move",
                   current, undo_token=result["undo_token"])
    undone = engine.execute(undo)
    assert undone["state"] == OperationState.COMPLETED
    assert image.read_bytes() == b"original image bytes"
    assert sidecar.read_bytes() == b"human note\n"
    assert not current.exists() and not Path(str(current) + ".txt").exists()
    assert journal.get_entry(result["operation_id"])["undo_consumed_by"] == undo["operation_id"]


def test_operation_acknowledgement_is_idempotent_and_replayable(environment):
    image, target, journal, engine = environment
    task = request("copy", image, target)
    entry, created = journal.accept(task)
    assert created and entry["state"] == "INTENT"
    first = engine.execute(task)
    assert engine.execute(task) == first
    assert len(list(target.glob("*.jpg"))) == 1
    receipts = journal.replay_results()
    assert len(receipts) == 1 and receipts[0]["result"] == first
    assert journal.replay_results(receipts[0]["sequence"]) == []
    conflict = dict(task, action="move")
    with pytest.raises(ValueError, match="different"):
        engine.execute(conflict)
    assert image.exists()


def test_cancellation_is_durable_before_any_files_change(environment):
    image, target, journal, engine = environment
    task = request("move", image, target)
    journal.accept(task)
    cancelled = engine.cancel_pending(task["operation_id"])
    assert cancelled["state"] == OperationState.CANCELLED
    assert engine.execute(task) == cancelled
    assert image.read_bytes() == b"original image bytes" and not list(target.iterdir())


def test_receipt_replay_paginates_without_duplicates_or_lost_results(environment):
    image, target, journal, engine = environment
    for _index in range(3):
        pending = request("copy", image, target)
        journal.accept(pending)
        engine.cancel_pending(pending["operation_id"])
    first = journal.replay_results(limit=2)
    second = journal.replay_results(first[-1]["sequence"], limit=2)
    assert len(first) == 2 and len(second) == 1
    assert first + second == journal.replay_results()
    assert journal.replay_results(second[-1]["sequence"], limit=2) == []
    for invalid in (0, -1, True, 1.5, "2", 10001):
        with pytest.raises(ValueError, match="page size"):
            journal.replay_results(limit=invalid)


def test_undo_uses_authoritative_original_even_when_client_token_is_modified(environment, tmp_path):
    image, target, _journal, engine = environment
    moved = engine.execute(request("move", image, target))
    forged = copy.deepcopy(moved["undo_token"])
    forged["original"] = str(tmp_path / "attacker_destination.jpg")
    forged["companions"] = [{"current": str(tmp_path / "unrelated.txt"), "original": "/wrong", "provenance": {}}]
    undone = engine.execute(request("undo_move", moved["destination_path"], undo_token=forged))
    assert undone["state"] == OperationState.COMPLETED
    assert image.read_bytes() == b"original image bytes"
    assert not Path(forged["original"]).exists()


def test_undo_refuses_unrecorded_or_same_bytes_replacement(environment):
    image, target, _journal, engine = environment
    copied = engine.execute(request("copy", image, target))
    current = Path(copied["destination_path"])
    replacement = target / "replacement"
    replacement.write_bytes(current.read_bytes())
    os.replace(replacement, current)
    undone = engine.execute(request("undo_copy", current, undo_token=copied["undo_token"]))
    assert undone["state"] == OperationState.FAILED
    assert current.read_bytes() == b"original image bytes"
    invented = copy.deepcopy(copied["undo_token"])
    invented["token_id"] = "not-recorded"
    undone = engine.execute(request("undo_copy", current, undo_token=invented))
    assert undone["state"] == OperationState.FAILED
    assert current.exists()


def test_destination_race_preserves_external_file_and_source(environment, monkeypatch):
    image, target, _journal, engine = environment
    original = safe.publish_no_replace
    raced = False

    def publish(stage, destination, expected):
        nonlocal raced
        if not raced and Path(destination).parent == target:
            raced = True
            Path(destination).write_bytes(b"external replacement")
        return original(stage, destination, expected)

    monkeypatch.setattr(safe, "publish_no_replace", publish)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.RECOVERY_REQUIRED
    assert image.read_bytes() == b"original image bytes"
    assert (target / "photo.jpg").read_bytes() == b"external replacement"


def test_sidecar_publish_failure_rolls_back_image_without_losing_pair(environment, monkeypatch):
    image, target, _journal, engine = environment
    sidecar = Path(str(image) + ".txt")
    sidecar.write_bytes(b"note")
    original = safe.publish_no_replace

    def publish(stage, destination, expected):
        if Path(destination).parent == target and destination.endswith(".txt"):
            raise OSError("Sidecar volume failure")
        return original(stage, destination, expected)

    monkeypatch.setattr(safe, "publish_no_replace", publish)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.FAILED
    assert image.read_bytes() == b"original image bytes" and sidecar.read_bytes() == b"note"
    assert not list(target.glob("*.jpg*"))


def test_same_size_source_edit_during_stream_is_detected(tmp_path, monkeypatch):
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    source.write_bytes(b"a" * 200000)
    expected = safe.fingerprint(str(source))
    read = os.read
    changed = False

    def raced_read(fd, size):
        nonlocal changed
        chunk = read(fd, size)
        if chunk and not changed:
            changed = True
            source.write_bytes(b"b" * 200000)
        return chunk

    monkeypatch.setattr(os, "read", raced_read)
    with pytest.raises(safe.FileChangedError):
        safe.secure_copy(str(source), str(stage), expected)
    assert source.read_bytes() == b"b" * 200000


def test_staging_never_exposes_private_source_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    source.write_bytes(b"secret" * 30000)
    source.chmod(0o600)
    expected = safe.fingerprint(str(source))
    write = os.write
    observed = []

    def observing_write(fd, data):
        observed.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return write(fd, data)

    monkeypatch.setattr(os, "write", observing_write)
    safe.secure_copy(str(source), str(stage), expected)
    assert observed and set(observed) == {0o600}


def test_publication_directory_is_synced_and_original_claim_retained(environment, monkeypatch):
    image, target, journal, engine = environment
    sync = safe.sync_directory
    events = []

    def recorded_sync(path):
        events.append(path)
        sync(path)

    monkeypatch.setattr(safe, "sync_directory", recorded_sync)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.COMPLETED
    assert str(target) in events and str(image.parent) in events
    manifest = journal.get_entry(result["operation_id"])["manifest"]
    claim = manifest["files"][0]["claim"]
    assert Path(claim).read_bytes() == b"original image bytes"


class SimulatedCrash(BaseException):
    pass


@pytest.mark.parametrize("boundary", ["claiming_sources", "staging", "publishing", "filesystem_committed"])
def test_recovery_never_deletes_claims_or_ambiguous_outputs(environment, monkeypatch, boundary):
    image, target, journal, engine = environment
    Path(str(image) + ".txt").write_bytes(b"note")
    save = journal.save_manifest
    claim = safe.claim_source
    hit = False

    def crash(operation_id, manifest):
        nonlocal hit
        save(operation_id, manifest)
        if manifest["phase"] == boundary and not hit:
            if boundary == "claiming_sources":
                return
            hit = True
            raise SimulatedCrash()

    monkeypatch.setattr(journal, "save_manifest", crash)
    if boundary == "claiming_sources":
        def crash_after_claim(source, destination, expected):
            nonlocal hit
            claim(source, destination, expected)
            hit = True
            raise SimulatedCrash()
        monkeypatch.setattr(safe, "claim_source", crash_after_claim)
    task = request("move", image, target)
    with pytest.raises(SimulatedCrash):
        engine.execute(task)
    assert hit
    before = {str(path): path.read_bytes() for folder in (image.parent, target)
              for path in folder.rglob("*") if path.is_file()}
    recovered = journal.reconcile_interrupted_operations(force=True)
    assert recovered[0]["reconciled_state"] == OperationState.RECOVERY_REQUIRED
    after = {str(path): path.read_bytes() for folder in (image.parent, target)
             for path in folder.rglob("*") if path.is_file()}
    assert before == after
    assert journal.reconcile_interrupted_operations(force=True) == []
    assert journal.replay_results()[-1]["result"]["state"] == OperationState.RECOVERY_REQUIRED


def test_live_owner_is_not_recovered_after_lease_expiry(environment):
    image, target, journal, _engine = environment
    task = request("move", image, target)
    journal.accept(task)
    with journal._get_connection() as conn:
        conn.execute("UPDATE operations SET lease_expires_at=0")
    assert journal.reconcile_interrupted_operations() == []


def test_profile_lock_blocks_second_owner_and_survives_release(tmp_path):
    path = tmp_path / "profile.lock"
    first = ProfileLock(path).acquire()
    inode = path.stat().st_ino
    try:
        with pytest.raises(ProfileBusyError):
            ProfileLock(path).acquire()
    finally:
        first.release()
    with ProfileLock(path):
        assert path.stat().st_ino == inode


def test_system_trash_submits_one_complete_directory_and_retains_sources(environment, tmp_path):
    image, _target, journal, _engine = environment
    Path(str(image) + ".txt").write_bytes(b"note")
    trashed = tmp_path / "simulated-system-trash"
    submitted = []

    def trash(path):
        submitted.append(path)
        os.rename(path, trashed)

    result = OperationEngine(journal, trash_backend=trash).execute(request("trash", image))
    assert result["state"] == OperationState.COMPLETED
    assert len(submitted) == 1 and result["undo_token"] is None
    assert (trashed / "photo.jpg").read_bytes() == b"original image bytes"
    assert (trashed / "photo.jpg.txt").read_bytes() == b"note"
    assert not image.exists() and not Path(str(image) + ".txt").exists()
    assert len(OperationEngine(journal).recovery_inventory()[0]["retained"]) == 2


def test_system_trash_failure_restores_source_pair(environment):
    image, _target, journal, _engine = environment
    Path(str(image) + ".txt").write_bytes(b"note")

    def failure(_path):
        raise OSError("Trash unavailable")

    result = OperationEngine(journal, trash_backend=failure).execute(request("trash", image))
    assert result["state"] == OperationState.FAILED
    assert image.read_bytes() == b"original image bytes"
    assert Path(str(image) + ".txt").read_bytes() == b"note"


def test_metadata_child_updates_authoritative_undo_and_preserves_original(environment):
    image, target, journal, engine = environment
    Image.new("RGB", (20, 20), "blue").save(image)
    original = image.read_bytes()
    parent = engine.execute(request("copy", image, target))
    child = engine.execute(request("metadata", parent["destination_path"], tags=["landscape"],
        parent_operation_id=parent["operation_id"], expected_sha256=parent["undo_token"]["provenance"]["sha256"],
        write_sidecar=True, write_exif=True))
    assert child["state"] == OperationState.COMPLETED
    assert image.read_bytes() == original
    assert child["undo_token"]["token_id"] == parent["undo_token"]["token_id"]
    assert child["undo_token"]["revision"] == 2
    assert journal.get_entry(parent["operation_id"])["undo_token"] == child["undo_token"]
    # The pre-enrichment client token only identifies the authoritative token.
    undone = engine.execute(request("undo_copy", parent["destination_path"], undo_token=parent["undo_token"]))
    assert undone["state"] == OperationState.COMPLETED
    assert not Path(parent["destination_path"]).exists()
    assert not Path(parent["destination_path"] + ".txt").exists()


@pytest.mark.parametrize("original_sidecar", [None, b"handwritten original sidecar\n"])
@pytest.mark.parametrize("action", ["move", "trash"])
def test_parent_undo_reverses_committed_enrichment_to_exact_original_bytes(environment, action, original_sidecar):
    image, target, _journal, engine = environment
    Image.new("RGB", (20, 20), "blue").save(image)
    original = image.read_bytes()
    sidecar = Path(str(image) + ".txt")
    if original_sidecar is not None:
        sidecar.write_bytes(original_sidecar)
    parent = engine.execute(request(action, image, target))
    child = engine.execute(request("metadata", parent["destination_path"], tags=["landscape"],
        parent_operation_id=parent["operation_id"], expected_sha256=parent["undo_token"]["provenance"]["sha256"],
        write_sidecar=True, write_exif=True))
    assert child["state"] == OperationState.COMPLETED
    assert Path(parent["destination_path"]).read_bytes() != original
    undone = engine.execute(request("undo_" + action, parent["destination_path"], undo_token=parent["undo_token"]))
    assert undone["state"] == OperationState.COMPLETED
    assert image.read_bytes() == original
    assert sidecar.read_bytes() == original_sidecar if original_sidecar is not None else not sidecar.exists()
    assert not Path(parent["destination_path"]).exists()
    assert not Path(parent["destination_path"] + ".txt").exists()


@pytest.mark.parametrize("unavailable", ["initial_claim_changed", "all_originals_cleaned", "current_edited"])
def test_enriched_undo_uses_only_verified_originals_and_preserves_edits(environment, unavailable):
    image, target, journal, engine = environment
    Image.new("RGB", (20, 20), "blue").save(image)
    original = image.read_bytes()
    parent = engine.execute(request("move", image, target))
    child = engine.execute(request("metadata", parent["destination_path"], tags=["landscape"],
        parent_operation_id=parent["operation_id"], expected_sha256=parent["undo_token"]["provenance"]["sha256"],
        write_sidecar=True, write_exif=True))
    current = Path(parent["destination_path"])
    if unavailable == "initial_claim_changed":
        claim = journal.get_entry(parent["operation_id"])["manifest"]["files"][0]["claim"]
        Path(claim).write_bytes(b"late external writer update")
    elif unavailable == "all_originals_cleaned":
        for result in (parent, child):
            engine.cleanup_retained(result["operation_id"], now=time.time() + 31 * 86400)
    else:
        current.write_bytes(b"external edited current image")
    before = current.read_bytes()
    undone = engine.execute(request("undo_move", current, undo_token=child["undo_token"]))
    if unavailable == "initial_claim_changed":
        assert undone["state"] == OperationState.COMPLETED
        assert image.read_bytes() == original
        assert Path(claim).read_bytes() == b"late external writer update"
    else:
        assert undone["state"] == OperationState.FAILED
        assert current.read_bytes() == before
        assert not image.exists()


@pytest.mark.parametrize("change", ["image", "sidecar", "undo"])
def test_late_metadata_refuses_changed_parent(environment, change):
    image, target, _journal, engine = environment
    parent = engine.execute(request("copy", image, target))
    current = Path(parent["destination_path"])
    if change == "image":
        current.write_bytes(b"external edit")
    elif change == "sidecar":
        Path(str(current) + ".txt").write_bytes(b"external note")
    else:
        engine.execute(request("undo_copy", current, undo_token=parent["undo_token"]))
    result = engine.execute(request("metadata", current, tags=["tag"],
        parent_operation_id=parent["operation_id"], expected_sha256=parent["undo_token"]["provenance"]["sha256"],
        write_exif=False, write_sidecar=True))
    assert result["state"] == OperationState.FAILED
    if change == "image":
        assert current.read_bytes() == b"external edit"
    if change == "sidecar":
        assert Path(str(current) + ".txt").read_bytes() == b"external note"


def test_exif_preserves_more_than_thirty_long_existing_keywords(tmp_path):
    image = tmp_path / "photo.jpg"
    Image.new("RGB", (10, 10), "red").save(image)
    existing = [f"keyword-{index}" for index in range(40)] + ["long" * 30]
    exif = piexif.load(str(image))
    exif["0th"][piexif.ImageIFD.XPKeywords] = (";".join(existing) + "\0").encode("utf-16le")
    piexif.insert(piexif.dump(exif), str(image))
    write_metadata(str(image), ["new"], True, False)
    actual = bytes(piexif.load(str(image))["0th"][piexif.ImageIFD.XPKeywords]).decode("utf-16le").rstrip("\0")
    assert actual.split(";") == existing + ["new"]


def test_failed_metadata_rollback_preserves_original_backup(tmp_path, monkeypatch):
    image = tmp_path / "photo.jpg"
    Image.new("RGB", (10, 10), "red").save(image)
    original = image.read_bytes()
    replace = os.replace

    def failed_restore(source, destination):
        if "exif-backup" in str(source):
            raise OSError("Backup restore denied")
        if str(source).endswith("sidecar-stage"):
            raise OSError("Sidecar publication failed after image commit")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", failed_restore)
    with pytest.raises(MetadataRecoveryError) as error:
        write_metadata(str(image), ["tag"], True, True)
    assert error.value.artifacts
    assert any(Path(path).read_bytes() == original for path in error.value.artifacts)


def test_journal_failure_after_disk_commit_keeps_truthful_result(environment, monkeypatch):
    image, target, journal, engine = environment

    def failure(*_args, **_kwargs):
        raise OSError("Journal disk full")

    monkeypatch.setattr(journal, "finish_result", failure)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.COMPLETED_WITH_WARNING
    assert result["undo_token"] is None
    assert not image.exists() and (target / "photo.jpg").read_bytes() == b"original image bytes"
    assert engine.recovery_inventory()[0]["retained"]


def test_retention_requires_explicit_age_and_verified_surviving_bytes(environment):
    image, target, _journal, engine = environment
    moved = engine.execute(request("move", image, target))
    with pytest.raises(ValueError, match="not yet eligible"):
        engine.cleanup_retained(moved["operation_id"])
    claim = Path(engine.recovery_inventory()[0]["retained"][0]["path"])
    Path(moved["destination_path"]).write_bytes(b"external edit")
    with pytest.raises(safe.FileChangedError):
        engine.cleanup_retained(moved["operation_id"], now=time.time() + 31 * 86400)
    assert claim.read_bytes() == b"original image bytes"


def test_capacity_failure_does_not_claim_or_delete_source(environment, monkeypatch):
    image, target, _journal, engine = environment

    def no_space(*_args):
        raise OSError("Insufficient free space")

    monkeypatch.setattr(safe, "ensure_capacity", no_space)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.FAILED
    assert image.read_bytes() == b"original image bytes"
    assert not list(target.iterdir())


def test_open_writer_cannot_make_rollback_delete_the_only_old_snapshot(environment, monkeypatch):
    image, target, _journal, engine = environment
    original_bytes = image.read_bytes()
    changed_bytes = b"x" * len(original_bytes)
    publish = safe.publish_no_replace
    with open(image, "r+b") as external_writer:
        def change_claim_after_publication(stage, destination, expected):
            result = publish(stage, destination, expected)
            if Path(destination).parent == target:
                external_writer.seek(0)
                external_writer.write(changed_bytes)
                external_writer.flush()
                os.fsync(external_writer.fileno())
            return result

        monkeypatch.setattr(safe, "publish_no_replace", change_claim_after_publication)
        result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.RECOVERY_REQUIRED
    retained_bytes = [path.read_bytes() for parent in (image.parent, target)
                      for path in parent.rglob("*") if path.is_file()]
    assert original_bytes in retained_bytes and changed_bytes in retained_bytes


def test_crash_during_copy_undo_preserves_both_file_set_members(environment, monkeypatch):
    image, target, journal, engine = environment
    Path(str(image) + ".txt").write_bytes(b"sidecar")
    parent = engine.execute(request("copy", image, target))
    claim = safe.claim_source

    def crash(source, destination, expected):
        claim(source, destination, expected)
        raise SimulatedCrash()

    monkeypatch.setattr(safe, "claim_source", crash)
    task = request("undo_copy", parent["destination_path"], undo_token=parent["undo_token"])
    with pytest.raises(SimulatedCrash):
        engine.execute(task)
    before = {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    journal.reconcile_interrupted_operations(force=True)
    assert before == {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    assert b"sidecar" in before.values() and b"original image bytes" in before.values()


def test_source_replacement_during_claim_is_preserved(environment, monkeypatch):
    image, target, journal, engine = environment
    rename = os.rename
    changed = False

    def race(source, destination):
        nonlocal changed
        if source == str(image) and not changed:
            changed = True
            replacement = image.parent / "replacement"
            replacement.write_bytes(b"new source belonging to user")
            os.replace(replacement, image)
        return rename(source, destination)

    monkeypatch.setattr(os, "rename", race)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.RECOVERY_REQUIRED
    claim = journal.get_entry(result["operation_id"])["manifest"]["files"][0]["claim"]
    assert Path(claim).read_bytes() == b"new source belonging to user"
    assert not list(target.glob("*.jpg"))


def test_explicit_retention_cleanup_removes_only_verified_old_claims(environment):
    image, target, _journal, engine = environment
    result = engine.execute(request("move", image, target))
    unrelated = image.parent / "unrelated"
    unrelated.write_bytes(b"preserve")
    removed = engine.cleanup_retained(result["operation_id"], now=time.time() + 31 * 86400)
    assert removed == 1 and not engine.recovery_inventory()
    assert Path(result["destination_path"]).read_bytes() == b"original image bytes"
    assert unrelated.read_bytes() == b"preserve"


@pytest.mark.parametrize("action", ["move", "copy"])
def test_retention_cleanup_follows_authoritative_undo_after_metadata(environment, action):
    image, target, _journal, engine = environment
    Image.new("RGB", (20, 20), "blue").save(image)
    parent = engine.execute(request(action, image, target))
    child = engine.execute(request("metadata", parent["destination_path"], tags=["landscape"],
        parent_operation_id=parent["operation_id"], expected_sha256=parent["undo_token"]["provenance"]["sha256"],
        write_sidecar=True, write_exif=True))
    undone = engine.execute(request("undo_" + action, parent["destination_path"], undo_token=child["undo_token"]))
    assert undone["state"] == OperationState.COMPLETED
    survivor = image.read_bytes()
    for result in (parent, child, undone):
        engine.cleanup_retained(result["operation_id"], now=time.time() + 31 * 86400)
    assert image.read_bytes() == survivor
    assert not engine.recovery_inventory()


def test_secure_copy_rejects_existing_and_symbolic_stage(tmp_path):
    source, stage, unrelated = (tmp_path / name for name in ("source", "stage", "unrelated"))
    source.write_bytes(b"source")
    unrelated.write_bytes(b"unrelated")
    stage.symlink_to(unrelated)
    with pytest.raises(FileExistsError):
        safe.secure_copy(str(source), str(stage), safe.fingerprint(str(source)))
    assert unrelated.read_bytes() == b"unrelated"


def test_unmodified_undo_still_works_after_explicit_retention_cleanup(environment):
    image, target, _journal, engine = environment
    parent = engine.execute(request("move", image, target))
    engine.cleanup_retained(parent["operation_id"], now=time.time() + 31 * 86400)
    result = engine.execute(request("undo_move", parent["destination_path"], undo_token=parent["undo_token"]))
    assert result["state"] == OperationState.COMPLETED
    assert image.read_bytes() == b"original image bytes"


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="Extended attributes are platform-specific")
def test_extended_attributes_survive_enrichment_and_exact_parent_undo(environment):
    image, target, _journal, engine = environment
    Image.new("RGB", (20, 20), "blue").save(image)
    original = image.read_bytes()
    sidecar = Path(str(image) + ".txt")
    sidecar.write_bytes(b"original note")
    os.setxattr(image, "user.human-label", b"valuable image label\x00binary")
    os.setxattr(sidecar, "user.human-label", b"valuable note label")
    parent = engine.execute(request("move", image, target))
    child = engine.execute(request("metadata", parent["destination_path"], tags=["landscape"],
        parent_operation_id=parent["operation_id"], expected_sha256=parent["undo_token"]["provenance"]["sha256"],
        write_sidecar=True, write_exif=True))
    assert child["state"] == OperationState.COMPLETED
    assert os.getxattr(parent["destination_path"], "user.human-label") == b"valuable image label\x00binary"
    assert os.getxattr(parent["destination_path"] + ".txt", "user.human-label") == b"valuable note label"
    result = engine.execute(request("undo_move", parent["destination_path"], undo_token=child["undo_token"]))
    assert result["state"] == OperationState.COMPLETED
    assert image.read_bytes() == original and sidecar.read_bytes() == b"original note"
    assert os.getxattr(image, "user.human-label") == b"valuable image label\x00binary"
    assert os.getxattr(sidecar, "user.human-label") == b"valuable note label"


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="Extended attributes are platform-specific")
def test_unsupported_destination_attributes_preserve_source_with_actionable_failure(environment, monkeypatch):
    image, target, _journal, engine = environment
    os.setxattr(image, "user.human-label", b"valuable label")

    def unsupported(_fd, _name, _value):
        raise OSError(errno.ENOTSUP, "Destination filesystem does not support attributes")

    monkeypatch.setattr(os, "setxattr", unsupported)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.FAILED
    assert "cannot preserve source extended attributes/ACLs" in result["error"]
    assert image.read_bytes() == b"original image bytes"
    assert os.getxattr(image, "user.human-label") == b"valuable label"
    assert not list(target.glob("*.jpg*"))


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="Extended attributes are platform-specific")
def test_attribute_only_external_edit_blocks_undo(environment):
    image, target, _journal, engine = environment
    os.setxattr(image, "user.human-label", b"before")
    result = engine.execute(request("copy", image, target))
    os.setxattr(result["destination_path"], "user.human-label", b"external edit")
    undone = engine.execute(request("undo_copy", result["destination_path"], undo_token=result["undo_token"]))
    assert undone["state"] == OperationState.FAILED
    assert os.getxattr(result["destination_path"], "user.human-label") == b"external edit"


def test_permission_only_external_edit_blocks_undo(environment):
    image, target, _journal, engine = environment
    result = engine.execute(request("copy", image, target))
    current = Path(result["destination_path"])
    current.chmod(0o400)
    undone = engine.execute(request("undo_copy", current, undo_token=result["undo_token"]))
    assert undone["state"] == OperationState.FAILED
    assert current.read_bytes() == b"original image bytes"
    assert stat.S_IMODE(current.stat().st_mode) == 0o400


def test_crash_after_attribute_preparation_can_explicitly_recover(environment, monkeypatch):
    image, target, journal, engine = environment
    image.chmod(0o640)
    before = safe.fingerprint(str(image))
    prepare = safe.apply_attributes

    def crash(path, attributes):
        prepare(path, attributes)
        raise SimulatedCrash()

    operation = request("move", image, target)
    with monkeypatch.context() as context:
        context.setattr(safe, "apply_attributes", crash)
        with pytest.raises(SimulatedCrash):
            engine.execute(operation)
    assert journal.get_entry(operation["operation_id"])["manifest"]["phase"] == "preparing_attributes"
    journal.reconcile_interrupted_operations(force=True)
    result = engine.execute(request("recover", image, target_operation_id=operation["operation_id"], recovery_action="rollback"))
    assert result["state"] == OperationState.COMPLETED
    safe.verify(str(image), before)
    assert not list(target.iterdir())


@pytest.mark.skipif(not shutil.which("setfacl"), reason="POSIX ACL qualification requires setfacl")
def test_exact_source_acl_and_no_inherited_destination_acl_on_move_and_undo(environment):
    image, target, _journal, engine = environment
    sidecar = Path(str(image) + ".txt")
    sidecar.write_bytes(b"note with no named ACL")
    subprocess.run(["setfacl", "-m", "u:65534:r--", str(image)], check=True)
    subprocess.run(["setfacl", "-m", "d:u:65534:r-x", str(target)], check=True)
    acl = os.getxattr(image, "system.posix_acl_access")
    original_mode = stat.S_IMODE(image.stat().st_mode)
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.COMPLETED
    assert os.getxattr(result["destination_path"], "system.posix_acl_access") == acl
    assert "system.posix_acl_access" not in os.listxattr(result["destination_path"] + ".txt")
    undone = engine.execute(request("undo_move", result["destination_path"], undo_token=result["undo_token"]))
    assert undone["state"] == OperationState.COMPLETED
    assert os.getxattr(image, "system.posix_acl_access") == acl
    assert stat.S_IMODE(image.stat().st_mode) == original_mode


def test_journal_files_have_private_permissions(environment):
    _image, _target, journal, _engine = environment
    assert stat.S_IMODE(Path(journal.db_path).stat().st_mode) == 0o600


def test_journal_refuses_hardlink_without_changing_original_file(tmp_path):
    original, database = tmp_path / "original", tmp_path / "journal.db"
    original.write_bytes(b"unrelated human document")
    original.chmod(0o640)
    os.link(original, database)
    with pytest.raises(ValueError, match="private regular"):
        OperationJournal(str(database))
    assert original.read_bytes() == b"unrelated human document"
    assert stat.S_IMODE(original.stat().st_mode) == 0o640


def test_public_rollback_claim_preserves_replacement_between_verification_and_removal(environment, monkeypatch):
    image, target, journal, engine = environment
    Path(str(image) + ".txt").write_bytes(b"original note")
    publish, rename = safe.publish_no_replace, os.rename
    destination = target / image.name
    replaced = False

    def fail_second_publication(stage, output, expected):
        if output == str(destination) + ".txt":
            raise OSError("Second publication failed")
        return publish(stage, output, expected)

    def replace_before_claim(source, claim):
        nonlocal replaced
        if source == str(destination) and not replaced:
            replaced = True
            external = target / "external-editor-save"
            external.write_bytes(b"unowned concurrent editor bytes")
            os.replace(external, destination)
        return rename(source, claim)

    monkeypatch.setattr(safe, "publish_no_replace", fail_second_publication)
    monkeypatch.setattr(os, "rename", replace_before_claim)
    result = engine.execute(request("move", image, target))
    assert replaced and result["state"] == OperationState.RECOVERY_REQUIRED
    assert image.read_bytes() == b"original image bytes"
    assert Path(str(image) + ".txt").read_bytes() == b"original note"
    assert destination.read_bytes() == b"unowned concurrent editor bytes"
    quarantine = journal.get_entry(result["operation_id"])["manifest"]["files"][0]["cleanup_claim"]
    assert Path(quarantine).read_bytes() == b"unowned concurrent editor bytes"
    assert any(item["role"] == "rollback_quarantine" for item in engine.recovery_inventory()[0]["retained"])


@pytest.mark.parametrize("field,value", [("model_sha256", "0" * 64), ("labels_sha256", "0" * 64),
    ("tensor_sha256", "wrong"), ("component_version", "../wrong"), ("provider", "unknown"),
    ("cuda_compute_events", 1)])
def test_metadata_refuses_unverified_inference_receipt(environment, field, value):
    image, target, _journal, engine = environment
    parent = engine.execute(request("copy", image, target))
    child = request("metadata", parent["destination_path"], tags=["tag"], write_exif=False,
                    write_sidecar=True, parent_operation_id=parent["operation_id"],
                    expected_sha256=parent["undo_token"]["provenance"]["sha256"])
    child["component_receipt"][field] = value
    result = engine.execute(child)
    assert result["state"] == OperationState.FAILED
    assert Path(parent["destination_path"]).read_bytes() == b"original image bytes"
    assert not Path(parent["destination_path"] + ".txt").exists()


def test_explicit_recovery_restores_crashed_move_and_preserves_receipts(environment, monkeypatch):
    image, target, journal, engine = environment
    Path(str(image) + ".txt").write_bytes(b"note")
    publish = safe.publish_no_replace

    def crash(stage, destination, expected):
        publish(stage, destination, expected)
        raise SimulatedCrash()

    task = request("move", image, target)
    with monkeypatch.context() as context:
        context.setattr(safe, "publish_no_replace", crash)
        with pytest.raises(SimulatedCrash):
            engine.execute(task)
    journal.reconcile_interrupted_operations(force=True)
    recovery = request("recover", image, target_operation_id=task["operation_id"], recovery_action="rollback")
    result = engine.execute(recovery)
    assert result["state"] == OperationState.COMPLETED
    assert result["resolved_operation_id"] == task["operation_id"]
    assert image.read_bytes() == b"original image bytes" and Path(str(image) + ".txt").read_bytes() == b"note"
    assert not list(target.glob("*.jpg*"))
    assert journal.get_entry(task["operation_id"])["resolved_by"] == recovery["operation_id"]
    assert engine.execute(recovery) == result


@pytest.mark.parametrize("failure", ["all_links", "source_links", "destination_links", "destination_sync",
                                     "profile_sync", "missing_xattr", "unsupported_xattr", "stage_attributes"])
def test_preflight_refuses_before_any_original_claim(environment, monkeypatch, failure):
    image, target, journal, engine = environment
    sidecar = Path(str(image) + ".txt")
    sidecar.write_bytes(b"original companion")
    os.setxattr(image, "user.preflight", b"valuable accessible metadata")
    before = {str(path): safe.fingerprint(str(path)) for path in (image, sidecar)}
    claims = []
    link, sync, attributes = os.link, safe.sync_directory, safe.apply_attributes
    with monkeypatch.context() as patch:
        patch.setattr(safe, "claim_source", lambda *args: claims.append(args))
        if failure.endswith("links"):
            def fail_link(source, destination, **options):
                parent = Path(destination).parent
                if failure == "all_links" or parent == (image.parent if failure == "source_links" else target):
                    raise OSError(errno.ENOTSUP, "Fixture missing hardlink prerequisite")
                return link(source, destination, **options)
            patch.setattr(os, "link", fail_link)
        elif failure.endswith("sync"):
            unavailable = target if failure == "destination_sync" else Path(journal.db_path).parent
            def fail_sync(path):
                if Path(path) == unavailable:
                    raise OSError(errno.EINVAL, "Fixture missing directory sync prerequisite")
                return sync(path)
            patch.setattr(safe, "sync_directory", fail_sync)
        elif failure == "missing_xattr":
            patch.delattr(os, "listxattr")
        elif failure == "unsupported_xattr":
            patch.setattr(os, "listxattr", lambda *_: (_ for _ in ()).throw(OSError(errno.ENOTSUP, "No observation")))
        else:
            def fail_attributes(path, expected):
                if Path(path).name == image.name:
                    raise OSError(errno.ENOTSUP, "Fixture source-specific attribute refusal")
                return attributes(path, expected)
            patch.setattr(safe, "apply_attributes", fail_attributes)
        result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.FAILED, result
    assert not claims and not (target / image.name).exists()
    assert before == {str(path): safe.fingerprint(str(path)) for path in (image, sidecar)}
    assert not list(image.parent.glob(".imagesorter-*-recovery-*"))


def test_preflight_copy_does_not_require_source_parent_mutation(environment, monkeypatch):
    image, target, _journal, engine = environment
    probe = safe.probe_directory
    observed = []
    def checked(parent, **options):
        observed.append(parent)
        assert Path(parent) != image.parent
        return probe(parent, **options)
    monkeypatch.setattr(safe, "probe_directory", checked)
    copied = engine.execute(request("copy", image, target))
    assert copied["state"] == OperationState.COMPLETED
    assert str(target) in observed and image.exists()


@pytest.mark.parametrize("action", ["undo_move", "undo_copy", "system_trash", "metadata"])
def test_preflight_covers_undo_trash_and_metadata_before_claim(environment, monkeypatch, action):
    image, target, _journal, engine = environment
    if action != "system_trash":
        parent = engine.execute(request("copy" if action != "undo_move" else "move", image, target))
        assert parent["state"] == OperationState.COMPLETED
        current = Path(parent["destination_path"])
        task = (request("metadata", current, tags=["tag"], parent_operation_id=parent["operation_id"],
                        expected_sha256=parent["undo_token"]["provenance"]["sha256"], write_exif=False, write_sidecar=True)
                if action == "metadata" else request(action, current, undo_token=parent["undo_token"]))
    else:
        current = image
        task = request("trash", image)
    original = safe.fingerprint(str(current))
    calls = []
    def refuse(*_args, **_kwargs):
        raise safe.PrerequisiteError("Unsupported fixture parent")
    monkeypatch.setattr(safe, "probe_directory", refuse)
    monkeypatch.setattr(safe, "claim_source", lambda *_: calls.append("claim"))
    engine.trash_backend = lambda *_: calls.append("trash")
    result = engine.execute(task)
    assert result["state"] == OperationState.FAILED and not calls
    assert safe.fingerprint(str(current)) == original


def test_preflight_recovery_refusal_cannot_enter_automatic_rollback(environment, monkeypatch):
    image, target, journal, engine = environment
    claim = safe.claim_source
    def crash(source, destination, expected):
        claim(source, destination, expected)
        raise SimulatedCrash()
    task = request("move", image, target)
    with monkeypatch.context() as patch:
        patch.setattr(safe, "claim_source", crash)
        with pytest.raises(SimulatedCrash):engine.execute(task)
    journal.reconcile_interrupted_operations(force=True)
    entry = journal.get_entry(task["operation_id"])
    before = {str(p): p.read_bytes() for d in (image.parent, target) for p in d.rglob("*") if p.is_file()}
    def refuse(*_args, **_kwargs):raise safe.PrerequisiteError("Unsupported fixture recovery parent")
    monkeypatch.setattr(safe, "probe_directory", refuse)
    result = engine.execute(request("recover", image, target_operation_id=task["operation_id"]))
    assert result["state"] == OperationState.FAILED
    assert before == {str(p): p.read_bytes() for d in (image.parent, target) for p in d.rglob("*") if p.is_file()}
    assert journal.get_entry(task["operation_id"]) == entry and not image.exists()


def test_preflight_retention_refusal_keeps_all_claims(environment, monkeypatch):
    image, target, journal, engine = environment
    moved = engine.execute(request("move", image, target))
    entry = journal.get_entry(moved["operation_id"])
    paths = [Path(item["claim"]) for item in entry["manifest"]["files"]]
    before = [safe.fingerprint(str(p)) for p in paths]
    def refuse(*_args, **_kwargs):raise safe.PrerequisiteError("Unsupported cleanup parent")
    monkeypatch.setattr(safe, "probe_directory", refuse)
    with pytest.raises(safe.PrerequisiteError):engine.cleanup_retained(moved["operation_id"], now=time.time() + 31 * 86400)
    assert before == [safe.fingerprint(str(p)) for p in paths]


def test_preflight_private_directory_permission_readback(tmp_path, monkeypatch):
    real, chmod = safe.tempfile.mkdtemp, os.chmod
    def unsafe(*args, **kwargs):
        path = real(*args, **kwargs)
        chmod(path, 0o777)
        return path
    monkeypatch.setattr(safe.tempfile, "mkdtemp", unsafe)
    monkeypatch.setattr(os, "chmod", lambda *_args, **_kwargs: None)
    with pytest.raises(safe.PrerequisiteError, match="permissions"):
        safe.private_directory(str(tmp_path), ".owned-test-")
    retained = list(tmp_path.glob(".owned-test-*"))
    assert len(retained) == 1 and stat.S_IMODE(retained[0].stat().st_mode) == 0o777


def test_preflight_probe_cleanup_retains_unknown_entry(tmp_path, monkeypatch):
    rmdir = os.rmdir
    foreign = []
    def refuse(path, *args, **kwargs):
        if Path(path).name.startswith(".imagesorter-prerequisite-"):
            extra = Path(path) / "foreign-note"
            extra.write_bytes(b"do not remove")
            foreign.append(extra)
        return rmdir(path, *args, **kwargs)
    monkeypatch.setattr(os, "rmdir", refuse)
    with pytest.raises(safe.PrerequisiteError, match="retained probe"):
        safe.probe_directory(str(tmp_path))
    assert len(foreign) == 1 and foreign[0].read_bytes() == b"do not remove"


def test_preflight_parent_identity_rechecked_before_claim(environment, monkeypatch):
    image, target, journal, engine = environment
    prepare = engine._prepare_attributes
    def changed(manifest):
        prepare(manifest)
        # Simulate a changed parent identity after preparation, before claims.
        manifest["prerequisites"][0]["ino"] += 1
    monkeypatch.setattr(engine, "_prepare_attributes", changed)
    claims = []
    monkeypatch.setattr(safe, "claim_source", lambda *_: claims.append(True))
    result = engine.execute(request("move", image, target))
    assert result["state"] == OperationState.FAILED and not claims and image.exists()
    assert not any(item.get("claimed") for item in journal.get_entry(result["operation_id"])["manifest"]["files"])


@pytest.mark.parametrize("failure", ["parent", "sidecar_attributes"])
def test_preflight_direct_metadata_refuses_without_replacing_originals(tmp_path, monkeypatch, failure):
    image = tmp_path / "photo.jpg"
    Image.new("RGB", (10, 10), "red").save(image)
    sidecar = Path(str(image) + ".txt");sidecar.write_bytes(b"human notes")
    before = {str(p): safe.fingerprint(str(p)) for p in (image, sidecar)}
    if failure == "parent":
        def refuse(*_args, **_kwargs):raise safe.PrerequisiteError("Unsupported metadata parent")
        monkeypatch.setattr(safe, "probe_directory", refuse)
    else:
        attributes = safe.apply_attributes
        def refuse(path, expected):
            if Path(path).name == "sidecar-stage":raise OSError("Attribute roundtrip unavailable")
            return attributes(path, expected)
        monkeypatch.setattr(safe, "apply_attributes", refuse)
    with pytest.raises(OSError):write_metadata(str(image), ["new"], True, True)
    assert before == {str(p): safe.fingerprint(str(p)) for p in (image, sidecar)}


def test_preflight_direct_metadata_preserves_observed_attributes(tmp_path):
    image = tmp_path / "photo.jpg";Image.new("RGB", (10, 10), "red").save(image)
    sidecar = Path(str(image) + ".txt");sidecar.write_bytes(b"human notes")
    for p in (image, sidecar):
        p.chmod(0o640);os.utime(p, ns=(1234567890123456789, 1234567890123456789))
        os.setxattr(p, "user.fixture", b"metadata retained")
    before = {str(p): safe.fingerprint(str(p)) for p in (image, sidecar)}
    write_metadata(str(image), ["new"], True, True)
    for p in (image, sidecar):
        after = safe.fingerprint(str(p))
        assert {k: after[k] for k in ("mode", "mtime_ns", "xattrs")} == {k: before[str(p)][k] for k in ("mode", "mtime_ns", "xattrs")}
    assert sidecar.read_text() == "human notes, new"


def test_preflight_journal_private_mode_readback(tmp_path, monkeypatch):
    path = tmp_path / "journal.db";path.write_bytes(b"");path.chmod(0o644)
    monkeypatch.setattr(os, "fchmod", lambda *_: None)
    with pytest.raises(safe.PrerequisiteError, match="journal private"):
        OperationJournal(str(path))
    assert path.read_bytes() == b"" and stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.parametrize("mode,synchronous", [("delete", 2), ("wal", 1), (None, 2)])
def test_preflight_journal_checks_actual_durability_mode(tmp_path, monkeypatch, mode, synchronous):
    from imagesorter import operation_journal
    calls = []
    class Connection:
        def execute(self, sql):
            calls.append(sql)
            self.value = (mode,) if "journal_mode" in sql else (synchronous,)
            return self
        def fetchone(self):return self.value
        def close(self):calls.append("closed")
    monkeypatch.setattr(operation_journal.sqlite3, "connect", lambda *_args, **_kwargs: Connection())
    journal = OperationJournal.__new__(OperationJournal);journal.db_path = str(tmp_path / "journal.db")
    with pytest.raises(safe.PrerequisiteError):journal._get_connection()
    assert calls[-1] == "closed"
    assert not (tmp_path / "journal.db").exists()


@pytest.mark.parametrize("failure", ["create", "file_sync", "rename", "occupied_link", "cleanup"])
def test_preflight_probe_primitive_failure_retains_only_disposable_bytes(tmp_path, monkeypatch, failure):
    source = tmp_path / "user-image";source.write_bytes(b"original private user bytes")
    before = safe.fingerprint(str(source))
    open_file, fsync, rename, link, unlink = os.open, os.fsync, os.rename, os.link, os.unlink
    if failure == "create":
        def blocked(path, *args, **kwargs):
            if Path(path).name == "probe":raise PermissionError("Fixture create unavailable")
            return open_file(path, *args, **kwargs)
        monkeypatch.setattr(os, "open", blocked)
    elif failure == "file_sync":
        def blocked(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):raise OSError(errno.EINVAL, "Fixture file sync unavailable")
            return fsync(fd)
        monkeypatch.setattr(os, "fsync", blocked)
    elif failure == "rename":
        def blocked(a, b, *args, **kwargs):
            if Path(a).name == "probe":raise OSError(errno.EXDEV, "Fixture rename unavailable")
            return rename(a, b, *args, **kwargs)
        monkeypatch.setattr(os, "rename", blocked)
    elif failure == "occupied_link":
        def blocked(a, b, *args, **kwargs):
            if os.path.lexists(b):return None  # A broken no-replace implementation.
            return link(a, b, *args, **kwargs)
        monkeypatch.setattr(os, "link", blocked)
    else:
        def blocked(path, *args, **kwargs):
            if Path(path).name == "publication-cleanup":raise PermissionError("Fixture cleanup unavailable")
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(os, "unlink", blocked)
    with pytest.raises(safe.PrerequisiteError):safe.probe_directory(str(tmp_path))
    assert safe.fingerprint(str(source)) == before
    remaining = [p for p in tmp_path.rglob("*") if p.is_file() and p != source]
    assert all(p.read_bytes() in (b"", b"Image Sorter disposable filesystem prerequisite\n") for p in remaining)


def test_native_attribute_descriptor_is_saved_before_restore(tmp_path, monkeypatch):
    # Deliberately synthetic descriptor: this tests journal propagation, not ABI.
    engine = OperationEngine.__new__(OperationEngine)
    descriptor = {"backend": "synthetic-darwin-propagation", "acl": "recorded"}
    original = {"mode": 0o640, "mtime_ns": 123, "xattrs": {}, "native_metadata": descriptor}
    manifest = {"files": [{"stage": str(tmp_path / "stage"), "stage_provenance": {}, "original_provenance": original}]}
    records = []
    monkeypatch.setattr(engine, "_save", lambda value: records.append(copy.deepcopy(value)))
    monkeypatch.setattr(engine, "_verify_parents", lambda value: None)
    monkeypatch.setattr(safe, "verify", lambda *args: {})
    def apply(path, expected):
        assert records[0]["phase"] == "preparing_attributes"
        assert records[0]["files"][0]["attribute_target"] == original
        assert expected["native_metadata"] == descriptor
        return expected
    monkeypatch.setattr(safe, "apply_attributes", apply)
    engine._prepare_attributes(manifest)
    assert manifest["files"][0]["stage_provenance"]["native_metadata"] == descriptor


@pytest.mark.parametrize("has_provenance", [False, True])
def test_native_incomplete_stage_survives_process_restart(tmp_path, has_provenance):
    import json
    directory = tmp_path / "stage"
    directory.mkdir(mode=0o700)
    stage = directory / "image"
    record = tmp_path / "recovery.json"
    # Child dies after its private stage write. Recovery has the durable intent,
    # either before copy returned or before native attribute restoration returned.
    code = '''import json, os, sys
from pathlib import Path
from imagesorter import file_safety as safe
stage, record, has_provenance = sys.argv[1:]
Path(stage).write_bytes(b"private partial native stage")
provenance = safe.fingerprint(stage)
provenance["native_metadata"] = {"backend": "synthetic-native-original"}
item = {"stage": stage, "original_provenance": provenance, "attribute_target": {"native_metadata": {"backend": "synthetic-native-target"}}}
if has_provenance == "True": item["stage_provenance"] = provenance
st = os.stat(Path(stage).parent)
manifest = {"files": [item], "directories": [{"role": "stage", "path": str(Path(stage).parent), "dev": st.st_dev, "ino": st.st_ino}]}
with open(record, "w") as f:
 json.dump(manifest, f); f.flush(); os.fsync(f.fileno())
os._exit(23)
'''
    import sys
    result = subprocess.run([sys.executable, "-B", "-c", code, str(stage), str(record), str(has_provenance)], timeout=30)
    assert result.returncode == 23
    engine = OperationEngine.__new__(OperationEngine)
    warnings = engine._cleanup_stages(json.loads(record.read_text()))
    assert warnings and stage.read_bytes() == b"private partial native stage"
    assert any("native" in warning.lower() for warning in warnings)
