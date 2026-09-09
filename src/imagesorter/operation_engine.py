"""Journal-owned, serial file-set transactions without GUI or AI runtimes.

The service holds ProfileLock throughout execution and drain. Original source
claims are retained for explicit verified cleanup; startup never deletes them.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from . import file_safety as safe
from .metadata_io import write_metadata
from .model_assets import LABELS_SHA256, MODEL_SHA256
from .operation_contracts import (
    OperationResult,
    OperationState,
    create_undo_token,
    validate_undo_token,
)
from .operation_journal import OperationJournal


class OperationEngine:
    def __init__(self, journal: OperationJournal, *, trash_backend: Callable[[str], None] | None = None) -> None:
        self.journal = journal
        self.trash_backend = trash_backend
        self._lock = threading.RLock()

    @staticmethod
    def _result(request: dict[str, Any], state: str, *, destination: str | None = None,
                token: dict[str, Any] | None = None, error: str | None = None,
                warning: str | None = None) -> dict[str, Any]:
        return OperationResult(operation_id=request["operation_id"], action=request["action"],
            source_path=request["source_path"], destination_path=destination, state=state,
            undo_token=token, error=error, warning=warning,
            view_generation=request.get("task_options", {}).get("view_generation")).to_dict()

    def execute(self, request: dict[str, Any], *, cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Run once; identical requests replay their persisted terminal receipt."""
        with self._lock:
            entry, _created = self.journal.accept(request)
            request = entry["request"]
            if entry.get("result"):
                return entry["result"]
            if entry.get("manifest"):
                result = self._result(request, OperationState.RECOVERY_REQUIRED,
                    error="An interrupted transaction already owns this operation ID; review its preserved files")
                return self.journal.finish_result(result)
            manifest: dict[str, Any] = {"version": 2, "operation_id": request["operation_id"],
                "action": request["action"], "phase": "preflight", "files": [], "directories": [],
                "created_at": time.time(), "retention_days": 30}
            try:
                if cancelled and cancelled():
                    raise safe.OperationCancelled("Cancelled before execution")
                final, token, parent_token, consume_parent = self._perform(request, manifest, cancelled)
            except Exception as exc:
                rollback_errors = self._rollback(manifest)
                state = OperationState.CANCELLED if isinstance(exc, safe.OperationCancelled) else OperationState.FAILED
                if rollback_errors:
                    state = OperationState.RECOVERY_REQUIRED
                error = str(exc)
                if rollback_errors:
                    error += "; preserved recovery material: " + "; ".join(rollback_errors)
                if manifest["files"]:
                    manifest["phase"] = "recovery_required" if rollback_errors else "rolled_back"
                    try:
                        self._save(manifest)
                    except Exception as journal_exc:
                        error += f"; recovery journal update failed: {journal_exc}"
                        state = OperationState.RECOVERY_REQUIRED
                result = self._result(request, state, error=error)
                try:
                    return self.journal.finish_result(result)
                except Exception as journal_exc:
                    result["state"] = OperationState.RECOVERY_REQUIRED
                    result["error"] = f"{error}; terminal journal update failed: {journal_exc}"
                    return result

            result = self._result(request, OperationState.COMPLETED, destination=final, token=token)
            manifest["phase"] = "filesystem_committed"
            manifest["planned_result"] = result
            warnings: list[str] = []
            try:
                self._save(manifest)
                warnings = self._cleanup_stages(manifest)
                if warnings:
                    result["state"] = OperationState.COMPLETED_WITH_WARNING
                    result["warning"] = "; ".join(warnings)
                if manifest.get("resolves_operation"):
                    result["resolved_operation_id"] = manifest["resolves_operation"]
                return self.journal.finish_result(result, parent_token=parent_token, consume_parent=consume_parent,
                    resolve_operation=manifest.get("resolves_operation"), manifest=manifest)
            except Exception as exc:
                # The disk transition completed. Do not undo it or claim that
                # it failed merely because its durable receipt could not finish.
                result["state"] = OperationState.COMPLETED_WITH_WARNING
                result["undo_token"] = None
                result["warning"] = f"Filesystem operation completed, but journal update failed; preserved originals require review: {exc}"
                return result

    def cancel_pending(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self.journal.get_entry(operation_id)
            if entry is None or not entry.get("request"):
                raise ValueError("Unknown accepted operation")
            if entry.get("result"):
                return entry["result"]
            if entry.get("manifest"):
                raise ValueError("Running file work can only cancel at its safe checkpoint")
            return self.journal.finish_result(self._result(entry["request"], OperationState.CANCELLED,
                warning="Cancelled before execution"))

    def _save(self, manifest: dict[str, Any]) -> None:
        self.journal.save_manifest(manifest["operation_id"], manifest)

    def _directory(self, manifest: dict[str, Any], parent: str, role: str) -> str:
        short_id = hashlib.sha256(manifest["operation_id"].encode()).hexdigest()[:16]
        path = safe.private_directory(parent, f".imagesorter-{short_id}-{role}-")
        st = os.stat(path, follow_symlinks=False)
        manifest["directories"].append({"path": path, "dev": st.st_dev, "ino": st.st_ino, "role": role})
        # The caller persists this empty directory together with every planned
        # member name before putting any user bytes inside it.
        return path

    @staticmethod
    def _files(source: str, token: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        image = safe.verify(source, token["provenance"]) if token else safe.fingerprint(source)
        files = [{"role": "image", "source": source, "original_provenance": image}]
        sidecar = source + ".txt"
        if token:
            companions = token.get("companions") or []
            if not isinstance(companions, list) or len(companions) > 1:
                raise ValueError("Undo companion manifest is invalid")
            if companions:
                companion = companions[0]
                if not isinstance(companion, dict) or os.path.abspath(companion.get("current", "")) != sidecar:
                    raise ValueError("Undo companion current path is invalid")
                prov = safe.verify(sidecar, companion["provenance"])
                files.append({"role": "sidecar", "source": sidecar, "original_provenance": prov})
            # A sidecar added by someone else is never retroactively claimed.
        elif os.path.lexists(sidecar):
            files.append({"role": "sidecar", "source": sidecar,
                          "original_provenance": safe.fingerprint(sidecar)})
        return files

    @staticmethod
    def _candidate(folder: str, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        for index in range(100000):
            name = filename if index == 0 else f"{base}_{index}{ext}"
            candidate = os.path.join(folder, name)
            # Both names matter even when the source has no sidecar.
            if not os.path.lexists(candidate) and not os.path.lexists(candidate + ".txt"):
                return candidate
        raise FileExistsError("No available destination name")

    def _claims(self, manifest: dict[str, Any], *, cancelled: Callable[[], bool] | None = None) -> None:
        parents: dict[str, str] = {}
        for index, item in enumerate(manifest["files"]):
            parent = os.path.dirname(item["source"])
            if parent not in parents:
                parents[parent] = self._directory(manifest, parent, "recovery")
            item["claim"] = os.path.join(parents[parent], f"source-{index}")
            if not item.get("generated"):
                item["claim_started"] = True
        manifest["phase"] = "claiming_sources"
        self._save(manifest)
        for item in manifest["files"]:
            if item.get("generated"):
                continue
            if cancelled and cancelled():
                raise safe.OperationCancelled("Cancelled before publication")
            item["claim_provenance"] = safe.claim_source(item["source"], item["claim"], item["original_provenance"])
            item["claimed"] = True

    def _stage(self, manifest: dict[str, Any], folder: str, *, cancelled: Callable[[], bool] | None = None) -> str:
        directory = self._directory(manifest, folder, "stage")
        image_name = os.path.basename(manifest["files"][0]["source"])
        for item in manifest["files"]:
            if item.get("restore_excluded"):
                continue
            item["stage"] = os.path.join(directory, image_name + (".txt" if item["role"] == "sidecar" else ""))
        manifest["phase"] = "staging"
        self._save(manifest)
        for item in manifest["files"]:
            if item.get("generated") or item.get("restore_excluded"):
                continue
            source = item.get("restore_from") or (item.get("claim") if item.get("claimed") else item["source"])
            if source == item["source"] and item.get("claimed"):
                source = item["claim"]
            expected = item.get("restore_provenance") or item["original_provenance"]
            item["stage_provenance"] = safe.secure_copy(source, item["stage"], expected, cancelled)
        return directory

    def _publish(self, manifest: dict[str, Any], final: str) -> None:
        for item in manifest["files"]:
            if item.get("restore_excluded"):
                continue
            safe.verify(item["stage"], item["stage_provenance"])
            attributes = item.get("restore_provenance") or item["original_provenance"]
            item["attribute_target"] = {key: attributes[key] for key in ("mode", "mtime_ns", "xattrs") if key in attributes}
        manifest["phase"] = "preparing_attributes"
        self._save(manifest)
        for item in manifest["files"]:
            if item.get("restore_excluded"):
                continue
            item["stage_provenance"] = safe.apply_attributes(item["stage"], item["attribute_target"])
            item["destination"] = final + (".txt" if item["role"] == "sidecar" else "")
            item["publish_started"] = True
        manifest["phase"] = "publishing"
        self._save(manifest)
        for item in manifest["files"]:
            if item.get("restore_excluded"):
                continue
            stage = item["stage"]
            item["published_provenance"] = safe.publish_no_replace(stage, item["destination"], item["stage_provenance"])
            item["published"] = True
        for item in manifest["files"]:
            if not item.get("generated"):
                source = item["claim"] if item.get("claimed") else item["source"]
                safe.verify(source, item["original_provenance"])
            if not item.get("restore_excluded"):
                safe.verify(item["destination"], item["published_provenance"])
        if sum(not item.get("restore_excluded", False) for item in manifest["files"]) == 1 and os.path.lexists(final + ".txt"):
            raise safe.FileChangedError("An unrelated sidecar appeared during publication")

    def _plan_original_restore(self, parent: dict[str, Any], manifest: dict[str, Any]) -> None:
        """Undo includes enrichment: restore the exact pre-sort byte set."""
        originals = {part["role"]: part for part in (parent.get("manifest") or {}).get("files", [])}
        if "image" not in originals:
            raise ValueError("This legacy operation has no verified pre-sort file-set manifest")
        for item in manifest["files"]:
            original = originals.get(item["role"])
            if original is None:
                # A journaled enrichment-created sidecar did not exist before
                # sorting. Claim and retain it, but do not restore it at home.
                item["restore_excluded"] = True
                continue
            wanted = original["original_provenance"]
            candidates = [(original.get("claim"), wanted)]
            # A current unmodified copy is also sufficient after explicit
            # recovery-retention cleanup; a merely similar file never is.
            if all(item["original_provenance"].get(key) == wanted.get(key) for key in ("size", "sha256")):
                candidates.append((item["source"], item["original_provenance"]))
            found = False
            for path, expected in candidates:
                if path and os.path.lexists(path):
                    try:
                        safe.verify(path, expected)
                    except (OSError, ValueError):
                        continue
                    item["restore_from"], item["restore_provenance"] = path, expected
                    found = True
                    break
            if not found:
                # A committed child retained the before-enrichment snapshot.
                # Its own inode proof plus the parent's pre-sort hash permit
                # recovery if an old external fd edited the initial claim.
                for child in self.journal.get_all_entries(limit=1000000):
                    if child.get("parent_operation_id") != parent["operation_id"]:
                        continue
                    for candidate in (child.get("manifest") or {}).get("files", []):
                        expected, path = candidate.get("original_provenance") or {}, candidate.get("claim")
                        if candidate.get("role") != item["role"] or not path or any(expected.get(key) != wanted.get(key) for key in ("size", "sha256")):
                            continue
                        try:
                            safe.verify(path, expected)
                        except (OSError, ValueError):
                            continue
                        item["restore_from"], item["restore_provenance"] = path, expected
                        found = True
                        break
                    if found:
                        break
            if not found:
                raise safe.FileChangedError("The exact pre-sort original is unavailable; Undo preserved the current file set")

    @staticmethod
    def _token(request: dict[str, Any], manifest: dict[str, Any], final: str,
               *, prior: dict[str, Any] | None = None) -> dict[str, Any]:
        source = prior["original"] if prior else request["source_path"]
        companions = [{"original": source + ".txt", "current": final + ".txt",
            "provenance": item["published_provenance"]} for item in manifest["files"] if item["role"] == "sidecar"]
        token = create_undo_token(prior["token_id"] if prior else uuid.uuid4().hex,
            prior["action"] if prior else request["action"], source, final, time.time(),
            manifest["files"][0]["published_provenance"], companions)
        token["operation_id"] = prior.get("operation_id") if prior else request["operation_id"]
        token["revision"] = int(prior.get("revision", 0)) + 1 if prior else 1
        return token

    def _perform(self, request: dict[str, Any], manifest: dict[str, Any], cancelled: Callable[[], bool] | None
                 ) -> tuple[str | None, dict[str, Any] | None, tuple[str, dict[str, Any]] | None, str | None]:
        action = request["action"]
        source = request["source_path"]
        destination = request["destination_path"]
        if action == "recover":
            return self._recover(request, manifest)
        if action == "metadata":
            return self._metadata(request, manifest, cancelled)
        if action not in {"move", "copy", "trash", "undo_move", "undo_copy", "undo_trash"}:
            raise ValueError(f"Unsupported file operation: {action}")
        prior: dict[str, Any] | None = None
        parent_id: str | None = None
        if action.startswith("undo_"):
            supplied = validate_undo_token(request.get("undo_token"), undo_action=action, current_path=source)
            parent, prior = self.journal.authoritative_undo(supplied["token_id"])
            validate_undo_token(prior, undo_action=action, current_path=source)
            parent_id = parent["operation_id"]
            if destination and destination != prior["original"]:
                raise ValueError("Undo destination does not match the authoritative journal token")
        manifest["files"] = self._files(source, prior)
        total = sum(item["original_provenance"]["size"] for item in manifest["files"])
        if action == "undo_copy":
            manifest["mode"] = "delete_copy"
            safe.ensure_capacity(os.path.dirname(source), 0)
            self._claims(manifest, cancelled=cancelled)
            return source, None, None, parent_id
        if action == "trash":
            settings = request.get("task_options", {}).get("settings_snapshot") or {}
            destination = destination or settings.get("directories", {}).get("trash")
            if not destination:
                return self._system_trash(request, manifest, cancelled)
        restore = action in ("undo_move", "undo_trash")
        if restore:
            assert prior is not None
            self._plan_original_restore(parent, manifest)
            total = sum(item["restore_provenance"]["size"] for item in manifest["files"]
                        if not item.get("restore_excluded"))
            final = prior["original"]
            folder = os.path.dirname(final)
            if os.path.lexists(final) or os.path.lexists(final + ".txt"):
                raise FileExistsError(f"Cannot restore file set: destination path already exists: {final}")
        else:
            if not destination:
                raise ValueError("Destination folder required for move/copy operation")
            folder = os.path.abspath(destination)
            intended = os.path.join(folder, os.path.basename(source))
            if os.path.realpath(source) == os.path.realpath(intended):
                raise ValueError("Source and destination resolve to the same effective location")
            final = self._candidate(folder, os.path.basename(source))
        if not os.path.isdir(folder) or os.path.islink(folder):
            raise FileNotFoundError(f"Destination folder missing or invalid: {folder}")
        safe.ensure_capacity(folder, total)
        manifest["mode"] = "transfer"
        if action != "copy":
            self._claims(manifest, cancelled=cancelled)
        self._stage(manifest, folder, cancelled=cancelled)
        if cancelled and cancelled():
            raise safe.OperationCancelled("Cancelled before publication")
        self._publish(manifest, final)
        token = None if restore else self._token(request, manifest, final)
        return final, token, None, parent_id

    def _system_trash(self, request: dict[str, Any], manifest: dict[str, Any], cancelled: Callable[[], bool] | None
                      ) -> tuple[None, None, None, None]:
        manifest["mode"] = "system_trash"
        safe.ensure_capacity(os.path.dirname(request["source_path"]), sum(item["original_provenance"]["size"] for item in manifest["files"]))
        self._claims(manifest, cancelled=cancelled)
        directory = self._stage(manifest, os.path.dirname(request["source_path"]), cancelled=cancelled)
        if cancelled and cancelled():
            raise safe.OperationCancelled("Cancelled before system trash")
        for item in manifest["files"]:
            safe.verify(item["claim"], item["original_provenance"])
        manifest["trash_path"] = directory
        manifest["phase"] = "trash_submitting"
        self._save(manifest)
        backend = self.trash_backend
        if backend is None:
            from send2trash import send2trash
            backend = send2trash
        # A single directory groups both files. Retained source claims provide
        # recovery even if the platform trashes it then throws before receipt.
        backend(directory)
        if os.path.lexists(directory):
            raise OSError("System trash did not remove the staged file-set directory")
        manifest["phase"] = "system_trashed"
        self._save(manifest)
        return None, None, None, None

    def _metadata(self, request: dict[str, Any], manifest: dict[str, Any], cancelled: Callable[[], bool] | None
                  ) -> tuple[str, dict[str, Any], tuple[str, dict[str, Any]], None]:
        parent = self.journal.get_entry(request.get("parent_operation_id", ""))
        if not parent or not parent.get("undo_token"):
            raise ValueError("Metadata requires a committed parent operation")
        parent, prior = self.journal.authoritative_undo(parent["undo_token"]["token_id"])
        source = request["source_path"]
        if source != prior["current"] or request.get("expected_sha256") != prior["provenance"]["sha256"]:
            raise safe.FileChangedError("Metadata snapshot no longer matches its parent artifact")
        receipt = request.get("component_receipt")
        if not isinstance(receipt, dict):
            raise ValueError("Metadata requires a verified inference receipt")
        if receipt.get("model_sha256") != MODEL_SHA256 or receipt.get("labels_sha256") != LABELS_SHA256:
            raise ValueError("Metadata model or labels do not match the approved hashes")
        if not isinstance(receipt.get("tensor_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt["tensor_sha256"]):
            raise ValueError("Metadata tensor receipt is malformed")
        for key in ("component_version", "model_component_version"):
            if not isinstance(receipt.get(key), str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", receipt[key]):
                raise ValueError(f"Metadata {key} receipt is malformed")
        provider = receipt.get("provider")
        events = receipt.get("cuda_compute_events")
        if provider not in ("CPUExecutionProvider", "CUDAExecutionProvider") or type(events) is not int:
            raise ValueError("Metadata provider receipt is malformed")
        if (provider == "CUDAExecutionProvider" and events <= 0) or (provider == "CPUExecutionProvider" and events != 0):
            raise ValueError("Metadata GPU claim lacks actual profiled execution")
        if provider == "CUDAExecutionProvider" and receipt["component_version"] == "base":
            raise ValueError("The base CPU runtime cannot claim CUDA execution")
        tags = request.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("Metadata tags must be a list of strings")
        manifest["files"] = self._files(source, prior)
        if len(manifest["files"]) == 1 and os.path.lexists(source + ".txt"):
            raise safe.FileChangedError("An unrelated sidecar appeared after the primary transaction")
        manifest["mode"] = "metadata"
        manifest["component_receipt"] = copy.deepcopy(receipt)
        settings = (request.get("task_options", {}).get("settings_snapshot") or {}).get("metadata", {})
        write_exif = bool(request.get("write_exif", settings.get("write_exif", True)))
        write_sidecar = bool(request.get("write_sidecar", settings.get("write_sidecar", False)))
        safe.ensure_capacity(os.path.dirname(source), 3 * sum(item["original_provenance"]["size"] for item in manifest["files"]) + 1024**2)
        directory = self._stage(manifest, os.path.dirname(source), cancelled=cancelled)
        image_stage = manifest["files"][0]["stage"]
        write_metadata(image_stage, tags, write_exif, write_sidecar)
        if len(manifest["files"]) == 1 and os.path.lexists(image_stage + ".txt"):
            generated = safe.fingerprint(image_stage + ".txt")
            manifest["files"].append({"role": "sidecar", "source": source + ".txt", "generated": True,
                "original_provenance": generated, "stage": image_stage + ".txt", "stage_provenance": generated})
        for item in manifest["files"]:
            item["stage_provenance"] = safe.fingerprint(item["stage"])
        manifest["metadata_stage"] = directory
        self._save(manifest)
        if cancelled and cancelled():
            raise safe.OperationCancelled("Cancelled before metadata publication")
        self._claims(manifest)
        self._publish(manifest, source)
        token = self._token(request, manifest, source, prior=prior)
        return source, token, (parent["operation_id"], token), None

    def _recover(self, request: dict[str, Any], manifest: dict[str, Any]
                 ) -> tuple[str, None, None, None]:
        """Explicit rollback of one interrupted v2 manifest; never guess paths."""
        target = self.journal.get_entry(request.get("target_operation_id", ""))
        if (not target or target["state"] != OperationState.RECOVERY_REQUIRED or target.get("resolved_by")
                or not target.get("manifest") or target["manifest"].get("version") != 2):
            raise ValueError("Only an unresolved, recorded v2 transaction can be recovered")
        if request.get("recovery_action", "rollback") != "rollback" or request["source_path"] != target["source_path"]:
            raise ValueError("Recovery request does not match the recorded source and rollback action")
        if target["manifest"].get("mode") == "system_trash":
            raise ValueError("System trash recovery requires review of the native trash receipt")
        manifest["files"] = copy.deepcopy(target["manifest"]["files"])
        manifest["directories"] = copy.deepcopy(target["manifest"]["directories"])
        manifest["mode"] = target["manifest"].get("mode")
        manifest["resolves_operation"] = target["operation_id"]
        manifest["phase"] = "explicit_recovery"
        self._save(manifest)
        errors = self._rollback(manifest)
        if errors:
            raise safe.FileChangedError("Recovery preserved ambiguous files: " + "; ".join(errors))
        return target["source_path"], None, None, None

    def _rollback(self, manifest: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        # Do not discard a staged snapshot after its original changed. That
        # stage may now be the only surviving copy of the observed source bytes.
        # This also protects claims still being edited through an external fd.
        for item in manifest["files"]:
            if item.get("generated"):
                continue
            claim = item.get("claim")
            original = claim if claim and os.path.lexists(claim) else item["source"]
            try:
                safe.verify(original, item["original_provenance"])
            except Exception as exc:
                errors.append(f"Original changed; all snapshots retained for {original}: {exc}")
        if errors:
            return errors
        if manifest.get("phase") in ("trash_submitting", "system_trashed") and not os.path.lexists(manifest.get("trash_path", "")):
            errors.append("System trash outcome requires review")
        for item in reversed(manifest["files"]):
            destination = item.get("destination")
            expected = item.get("published_provenance") or item.get("stage_provenance")
            quarantine = item.get("cleanup_claim")
            if (item.get("publish_started") and destination and expected
                    and (os.path.lexists(destination) or (quarantine and os.path.lexists(quarantine)))):
                try:
                    if not quarantine:
                        directory = self._directory(manifest, os.path.dirname(destination), "rollback")
                        quarantine = item["cleanup_claim"] = os.path.join(directory, "published-output")
                        self._save(manifest)
                    safe.unlink_owned(destination, expected, quarantine=quarantine)
                except Exception as exc:
                    errors.append(f"Preserved destination {destination}: {exc}")
            claim = item.get("claim")
            if claim and os.path.lexists(claim):
                try:
                    safe.verify(claim, item["original_provenance"])
                    if os.path.lexists(item["source"]):
                        safe.verify(item["source"], item["original_provenance"])
                    else:
                        safe.publish_no_replace(claim, item["source"], item["original_provenance"])
                    item["restored"] = True
                except Exception as exc:
                    errors.append(f"Preserved source claim {claim}: {exc}")
        errors.extend(self._cleanup_stages(manifest))
        return errors

    def _cleanup_stages(self, manifest: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        for item in manifest["files"]:
            stage = item.get("stage")
            expected = item.get("stage_provenance")
            if stage and os.path.lexists(stage):
                if not expected:
                    try:
                        # A failed copy may leave an incomplete stage. Its
                        # private, exclusive directory and an intact original
                        # establish that these partial bytes are disposable.
                        directory = next(record for record in manifest["directories"]
                                         if record["role"] == "stage" and record["path"] == os.path.dirname(stage))
                        st = os.stat(directory["path"], follow_symlinks=False)
                        if (st.st_dev, st.st_ino) != (directory["dev"], directory["ino"]):
                            raise safe.FileChangedError("Staging directory identity changed")
                        source = item["claim"] if item.get("claimed") else item["source"]
                        safe.verify(source, item["original_provenance"])
                        expected = safe.fingerprint(stage)
                        item["stage_provenance"] = expected
                    except Exception as exc:
                        warnings.append(f"Incomplete stage retained for review: {stage}: {exc}")
                        continue
                try:
                    try:
                        safe.verify(stage, expected)
                    except safe.FileChangedError:
                        target = item.get("attribute_target")
                        if target is None:
                            raise
                        # An interrupted, recorded private attribute sequence
                        # may be part-way through old -> desired values. Never
                        # relax byte/inode ownership, or accept an unrelated
                        # attribute value merely because the directory is ours.
                        observed = safe.verify(stage, {key: expected[key] for key in ("size", "sha256", "dev", "ino")})
                        for key in ("mode", "mtime_ns"):
                            if observed.get(key) not in (expected.get(key), target.get(key)):
                                raise safe.FileChangedError("Unrecorded stage attribute change")
                        previous_attrs, target_attrs = expected.get("xattrs", {}), target.get("xattrs", {})
                        for name in set(previous_attrs) | set(target_attrs) | set(observed["xattrs"]):
                            if observed["xattrs"].get(name) not in (previous_attrs.get(name), target_attrs.get(name)):
                                raise safe.FileChangedError("Unrecorded stage extended attribute change")
                        expected = observed
                    safe.unlink_owned(stage, expected)
                    item["stage_cleaned"] = True
                except Exception as exc:
                    warnings.append(f"Stage retained for review: {stage}: {exc}")
        for directory in manifest["directories"]:
            if directory["role"] not in ("stage", "rollback") or not os.path.lexists(directory["path"]):
                continue
            try:
                st = os.stat(directory["path"], follow_symlinks=False)
                if (st.st_dev, st.st_ino) != (directory["dev"], directory["ino"]):
                    raise safe.FileChangedError("Stage directory identity changed")
                os.rmdir(directory["path"])
                safe.sync_directory(os.path.dirname(directory["path"]))
            except OSError as exc:
                warnings.append(f"Stage directory retained: {directory['path']}: {exc}")
        return warnings

    def recovery_inventory(self) -> list[dict[str, Any]]:
        inventory = []
        for entry in self.journal.get_all_entries(limit=1000000):
            if entry.get("resolved_by"):
                continue
            manifest = entry.get("manifest") or {}
            retained = []
            for item in manifest.get("files", []):
                claim = item.get("claim")
                if claim and os.path.lexists(claim):
                    retained.append({"path": claim, "bytes": item["original_provenance"]["size"],
                                     "original_path": item["source"], "role": "source_claim"})
                stage = item.get("stage")
                if stage and os.path.lexists(stage):
                    retained.append({"path": stage, "bytes": (item.get("stage_provenance") or {}).get("size", 0),
                                     "original_path": item["source"], "role": "stage"})
                quarantine = item.get("cleanup_claim")
                if quarantine and os.path.lexists(quarantine):
                    retained.append({"path": quarantine, "bytes": os.lstat(quarantine).st_size,
                                     "original_path": item.get("destination"), "role": "rollback_quarantine"})
            if retained or entry["state"] == OperationState.RECOVERY_REQUIRED:
                inventory.append({"operation_id": entry["operation_id"], "state": entry["state"],
                    "retained": retained, "created_at": entry["created_at"],
                    "cleanup_eligible_at": entry["created_at"] + 30 * 86400,
                    "manifest": manifest})
        return inventory

    def storage_status(self) -> dict[str, Any]:
        inventory = self.recovery_inventory()
        retained = sum(item["bytes"] for entry in inventory for item in entry["retained"])
        return {"retained_bytes": retained, "warning": retained >= 10 * 1024**3,
                "unresolved_operations": sum(entry["state"] == OperationState.RECOVERY_REQUIRED for entry in inventory),
                "inventory": inventory}

    def _verify_retention_survivor(self, entry: dict[str, Any], item: dict[str, Any]) -> None:
        """Follow recorded Undo/enrichment successors, never an inode guess."""
        if item.get("restore_excluded"):
            return  # Its recorded Undo deliberately removed enrichment-only content.
        parent = (self.journal.get_entry(entry.get("parent_operation_id"))
                  if entry.get("parent_operation_id") else entry) or entry
        undo_id = parent.get("undo_consumed_by")
        if undo_id:
            undone = self.journal.get_entry(undo_id)
            if not undone or undone["state"] not in (OperationState.COMPLETED, OperationState.COMPLETED_WITH_WARNING):
                raise safe.FileChangedError("The authoritative Undo receipt is unavailable")
            successor = next((part for part in (undone.get("manifest") or {}).get("files", [])
                              if part["role"] == item["role"]), None)
            if not successor:
                raise safe.FileChangedError("The authoritative Undo file-set successor is unavailable")
            if successor.get("destination"):
                safe.verify(successor["destination"], successor["published_provenance"])
            elif not successor.get("restore_excluded") and (undone.get("manifest") or {}).get("mode") != "delete_copy":
                raise safe.FileChangedError("The Undo file-set outcome is ambiguous")
            # A committed Undo-copy deliberately removed this copy; its exact
            # private claims are the recorded deletion outcome, not a missing
            # destination that should be guessed back into existence.
            return
        destination = item.get("destination")
        if destination and os.path.lexists(destination):
            token = parent.get("undo_token") or entry.get("undo_token") or {}
            authoritative = (token.get("provenance") if item["role"] == "image" else
                             next((part.get("provenance") for part in token.get("companions", [])
                                   if part.get("current") == destination), None))
            safe.verify(destination, authoritative or item["published_provenance"])
        elif (entry.get("manifest") or {}).get("mode") not in ("delete_copy", "system_trash"):
            safe.verify(item["source"], item["original_provenance"])

    def cleanup_retained(self, operation_id: str, *, now: float | None = None) -> int:
        """Explicit housekeeping only; unresolved/changed bytes never expire."""
        with self._lock:
            entry = self.journal.get_entry(operation_id)
            if not entry or (not entry.get("resolved_by") and entry["state"] not in (OperationState.COMPLETED, OperationState.COMPLETED_WITH_WARNING, OperationState.FAILED, OperationState.CANCELLED)):
                raise ValueError("Recovery-required material cannot be pruned")
            if (time.time() if now is None else now) < entry["created_at"] + 30 * 86400:
                raise ValueError("Retained originals are not yet eligible for cleanup")
            manifest = copy.deepcopy(entry.get("manifest") or {})
            # First validate the whole deletion set and the surviving file set.
            candidates = []
            for item in manifest.get("files", []):
                claim = item.get("claim")
                if not claim or not os.path.lexists(claim):
                    continue
                safe.verify(claim, item["original_provenance"])
                self._verify_retention_survivor(entry, item)
                candidates.append(item)
            stage_errors = self._cleanup_stages(manifest)
            if stage_errors:
                raise safe.FileChangedError("Preserved staged recovery material: " + "; ".join(stage_errors))
            for item in candidates:
                safe.unlink_owned(item["claim"], item["original_provenance"])
            for directory in manifest.get("directories", []):
                path = directory["path"]
                if directory["role"] == "recovery" and os.path.lexists(path):
                    st = os.stat(path, follow_symlinks=False)
                    if (st.st_dev, st.st_ino) == (directory["dev"], directory["ino"]):
                        try:
                            os.rmdir(path)
                            safe.sync_directory(os.path.dirname(path))
                        except OSError:
                            pass  # Extra/unrecognized material is never removed.
            return len(candidates)
