"""Guest-only transaction probes. The host abruptly powers off the whole VM."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for member, expected_hash in json.loads((ROOT / "payload-inventory.json").read_text()).items():
    path = ROOT / member
    if not path.resolve().is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise RuntimeError(f"Guest payload member failed exact-byte verification: {member}")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from imagesorter import file_safety as safe
from imagesorter.model_assets import LABELS_SHA256, MODEL_SHA256
from imagesorter.operation_engine import OperationEngine
from imagesorter.operation_journal import OperationJournal
from imagesorter.profile_lock import ProfileLock


def save(path, value):
    with path.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    safe.sync_directory(str(path.parent))


def task(action, source, destination=None, **extras):
    return {"operation_id": "target", "action": action, "source_path": str(source),
            "destination_path": str(destination) if destination else None, **extras}


def snapshot(work):
    destination = Path("/mnt/imagesorter-qualification") / work.name if work.name.startswith("cross-") else work / "destination"
    return {role + "/" + str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for role, folder in (("source", work / "source"), ("destination", destination))
            for path in folder.rglob("*") if path.is_file()}


def main():
    mode, name = sys.argv[1:3]
    work = ROOT / "cases" / name
    if mode == "prepare":
        work.mkdir(parents=True, exist_ok=False)
        (work / "source").mkdir()
        (work / "destination").mkdir()
    os.environ["IMAGESORTER_PROFILE_ROOT"] = str(work / "profile")
    with ProfileLock(work / "journal.lock"):
        journal = OperationJournal(str(work / "journal.db"))
        engine = OperationEngine(journal)
        if mode == "prepare":
            source = work / "source" / "photo.jpg"
            source.write_bytes((ROOT / "fixture.jpg").read_bytes())
            Path(str(source) + ".txt").write_bytes(b"Original handwritten note\n")
            os.setxattr(source, "user.imagesorter.qualify", b"Original image attribute")
            os.setxattr(str(source) + ".txt", "user.imagesorter.qualify", b"Original sidecar attribute")
            save(work / "expected-originals.json", {"image": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sidecar": hashlib.sha256(Path(str(source) + ".txt").read_bytes()).hexdigest()})
            destination = work / "destination"
            if name.startswith("cross-"):
                destination = Path("/mnt/imagesorter-qualification") / name
                destination.mkdir()
                assert source.stat().st_dev != destination.stat().st_dev, "Cross-volume case is not on distinct filesystems"
            request = task("move", source, destination)
            if name.startswith(("undo-", "metadata-")):
                parent_request = task("copy" if name.startswith("undo-") else "move", source, work / "destination")
                parent_request["operation_id"] = "parent"
                parent = engine.execute(parent_request)
                assert parent["state"] == "completed", parent
                if name.startswith("undo-"):
                    request = task("undo_copy", parent["destination_path"], undo_token=parent["undo_token"])
                else:
                    request = task("metadata", parent["destination_path"], parent_operation_id="parent",
                        tags=["landscape"], write_exif=True, write_sidecar=True,
                        expected_sha256=parent["undo_token"]["provenance"]["sha256"],
                        component_receipt={"provider": "CPUExecutionProvider", "cuda_compute_events": 0,
                            "tensor_sha256": "a" * 64, "model_sha256": MODEL_SHA256,
                            "labels_sha256": LABELS_SHA256, "component_version": "base",
                            "model_component_version": "explicit-verified"})
            save(work / "request.json", request)
            os.sync()  # Fixture and dependency durability, BEFORE arming the cut.
            print(json.dumps({"prepared": name}), flush=True)
        elif mode == "run":
            request = json.loads((work / "request.json").read_text())
            boundary = name.split("-", 1)[1]

            def reached():
                # This emits no disk write and does not sync anything. The
                # host SIGKILLs QEMU, discarding this guest kernel and its RAM.
                print(json.dumps({"power_cut_ready": name, "guest_pid": os.getpid()}), flush=True)
                while True:
                    signal.pause()

            accept, persist = journal.accept, journal.save_manifest
            claim, publish, finish = safe.claim_source, safe.publish_no_replace, journal.finish_result
            attributes = safe.apply_attributes

            def accepted(value):
                result = accept(value)
                if boundary == "intent":
                    reached()
                return result

            def persisted(identifier, manifest):
                persist(identifier, manifest)
                if (boundary == "staged" and manifest["phase"] == "publishing") or (
                        boundary == "filesystem" and manifest["phase"] == "filesystem_committed"):
                    reached()

            def claimed(*args, **kwargs):
                result = claim(*args, **kwargs)
                if boundary == "claim":
                    reached()
                return result

            def published(*args, **kwargs):
                result = publish(*args, **kwargs)
                if boundary == "publish":
                    reached()
                return result

            def finished(*args, **kwargs):
                result = finish(*args, **kwargs)
                if boundary == "receipt":
                    reached()
                return result

            def applied(*args, **kwargs):
                result = attributes(*args, **kwargs)
                if boundary == "attributes":
                    reached()
                return result

            journal.accept, journal.save_manifest = accepted, persisted
            safe.claim_source, safe.publish_no_replace = claimed, published
            safe.apply_attributes = applied
            journal.finish_result = finished
            result = engine.execute(request)
            raise RuntimeError(f"Cut boundary was not reached: {result}")
        elif mode == "verify":
            before = snapshot(work)
            expected_hashes = (hashlib.sha256((ROOT / "fixture.jpg").read_bytes()).hexdigest(),
                               hashlib.sha256(b"Original handwritten note\n").hexdigest())
            assert set(json.loads((work / "expected-originals.json").read_text()).values()) == set(expected_hashes)
            assert all(value in before.values() for value in expected_hashes), "Original image/sidecar bytes lost"
            entry = journal.get_entry("target")
            assert entry is not None
            for item in (entry.get("manifest") or {}).get("files", []):
                if item.get("generated"):
                    continue
                found = False
                for path in (item.get("claim"), item.get("source")):
                    if path and Path(path).exists():
                        try:
                            safe.verify(path, item["original_provenance"])
                            found = True
                            break
                        except (OSError, ValueError):
                            pass
                assert found, f"Recorded original member lost: {item}"
            journal.reconcile_interrupted_operations(force=True)
            assert snapshot(work) == before, "Startup reconciliation changed user bytes"
            assert journal.reconcile_interrupted_operations(force=True) == []
            if name.endswith("attributes") and not journal.get_entry("target").get("resolved_by"):
                recovered = engine.execute({"operation_id": "explicit-rollback", "action": "recover",
                    "source_path": str(work / "source" / "photo.jpg"), "target_operation_id": "target",
                    "recovery_action": "rollback"})
                assert recovered["state"] == "completed", recovered
                assert (work / "source" / "photo.jpg").read_bytes() == (ROOT / "fixture.jpg").read_bytes()
                assert not list((work / "destination").iterdir())
                before = snapshot(work)
            result = journal.get_entry("target")["result"]
            expected = "completed" if name.endswith("receipt") else "cancelled" if name.endswith("intent") else "recovery_required"
            assert result and result["state"] == expected, result
            if expected == "completed":
                for item in (entry.get("manifest") or {}).get("files", []):
                    if item.get("destination"):
                        safe.verify(item["destination"], item["published_provenance"])
                if name.startswith("metadata-"):
                    assert journal.get_entry("parent")["undo_token"] == result["undo_token"]
            receipts = journal.replay_results()
            assert len([receipt for receipt in receipts if receipt["result"]["operation_id"] == "target"]) == 1
            audit = {"case": name, "state": result["state"], "files": before, "receipts": receipts}
            previous = work / "first-recovery.json"
            if previous.exists():
                assert json.loads(previous.read_text()) == audit, "Second restart changed bytes or durable receipts"
                audit["restart"] = 2
            else:
                save(previous, audit)
                audit["restart"] = 1
            print(json.dumps(audit, sort_keys=True), flush=True)
        else:
            raise ValueError(mode)
        journal.close()


if __name__ == "__main__":
    main()
