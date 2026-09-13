"""Public v1 operation facade backed by the independently draining journal owner."""
from __future__ import annotations

import time
import uuid
from collections import deque

from PyQt6.QtCore import QCoreApplication, QObject, QThread, pyqtSignal

from . import file_safety
from .mutation_client import MutationClient
from .operation_contracts import TaskOptions
from .reader_process import run_reader

_compute_provenance = file_safety.fingerprint
_verify_provenance = file_safety.verify


class WorkerSignals(QObject):
    progress = pyqtSignal(str)
    operation_result = pyqtSignal(dict)
    recovery_summary = pyqtSignal(dict)

class EnrichmentReader(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, parent_id, filepath, snapshot, parent=None):
        super().__init__(parent)
        self.parent_id, self.filepath, self.snapshot = parent_id, filepath, snapshot

    def run(self):
        try:
            result, _pixels = run_reader({"action": "infer", "filepath": self.filepath,
                "model_dir": None, "hardware": self.snapshot.get("advanced", {}).get("hardware_acceleration", True),
                "threshold": self.snapshot.get("ai_tagger", {}).get("threshold", 0.5)},
                cancelled=self.isInterruptionRequested, timeout=195)
            if not self.isInterruptionRequested():
                self.completed.emit({"parent_operation_id": self.parent_id, "filepath": self.filepath, **result})
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.completed.emit({"parent_operation_id": self.parent_id, "error": str(exc)})

class QueueWorker(QObject):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings = settings_manager
        self.signals = WorkerSignals()
        self.client = MutationClient(self)
        self.client.result.connect(self._result)
        self.client.status.connect(self.signals.progress.emit)
        self.client.recovery.connect(self.signals.recovery_summary.emit)
        self._requests = {}
        self._readers = {}
        self._enrichment_queue = deque()
        self._enrichment_tokens = {}
        self._cancelled_enrichments = set()
        self._closing = False

    def refresh_settings(self):
        """Each new operation already receives its own validated settings snapshot."""

    def add_task(self, task_type, filepath, dest_folder=None, undo_token=None, *, operation_id=None, task_options=None):
        if self._closing:
            raise RuntimeError("Image Sorter is closing")
        if len(self.client.pending) >= self.client.MAX_PENDING:
            self.signals.progress.emit("File-operation backlog is full; wait for accepted operations to finish")
            return None
        if isinstance(dest_folder, dict):
            undo_token = dest_folder
            dest_folder = undo_token.get("original")
        operation_id = operation_id or uuid.uuid4().hex
        if isinstance(task_options, TaskOptions):
            options = task_options.to_dict()
        else:
            options = dict(task_options or {})
        if options.get("settings_snapshot") is None:
            options["settings_snapshot"] = self.settings.snapshot()
        request = {"operation_id": operation_id, "action": task_type,
                   "source_path": filepath, "destination_path": dest_folder,
                   "undo_token": undo_token, "task_options": options}
        if task_type.startswith("undo") and undo_token:
            parent_id = self._enrichment_tokens.get(undo_token.get("token_id"))
            if parent_id:
                self._cancelled_enrichments.add(parent_id)
                self._enrichment_queue = deque(item for item in self._enrichment_queue if item[0] != parent_id)
            # Stop uncommitted enrichment for the artifact being undone.
            for parent_id, reader in self._readers.items():
                original = self._requests.get(parent_id, {})
                if original.get("destination_path") == filepath or reader.filepath == filepath:
                    reader.requestInterruption()
        self._requests[operation_id] = request
        self.client.submit(request)
        return operation_id

    def _result(self, result):
        if result.get("undo_token") and result.get("action") in ("copy", "move"):
            self._enrichment_tokens[result["undo_token"]["token_id"]] = result["operation_id"]
        self.signals.progress.emit(f"{result['action']}: {result['state']} — {result.get('destination_path') or result['source_path']}")
        self.signals.operation_result.emit(result)
        operation_id = result["operation_id"]
        request = self._requests.get(operation_id, {})
        snapshot = request.get("task_options", {}).get("settings_snapshot") or {}
        if (not self._closing and result.get("state") in ("completed", "completed_with_warning")
                and result.get("action") in ("move", "copy") and result.get("destination_path")
                and snapshot.get("ai_tagger", {}).get("enabled", False)
                and operation_id not in self._cancelled_enrichments):
            if len(self._readers) < 2:
                self._start_enrichment(operation_id, result["destination_path"], snapshot)
            elif len(self._enrichment_queue) < 256:
                self._enrichment_queue.append((operation_id, result["destination_path"], snapshot))
            else:
                self.signals.progress.emit("Image committed; optional tagging backlog is full and this image was not tagged.")
                self._requests.pop(operation_id, None)
        else:
            self._requests.pop(operation_id, None)

    def add_recovery_task(self, record, *, task_options=None):
        """Request one authoritative rollback; no filesystem recovery occurs here."""
        if self._closing or self.client.pending:
            self.signals.progress.emit("Wait for accepted file operations to settle before requesting recovery")
            return None
        manifest = record.get("manifest") or {}
        if (record.get("state") != "recovery_required" or record.get("resolved_by") or
                manifest.get("version") != 2 or manifest.get("mode") == "system_trash"):
            self.signals.progress.emit("Automatic rollback is unavailable; preserved files require manual review")
            return None
        operation_id = uuid.uuid4().hex
        options = task_options.to_dict() if isinstance(task_options, TaskOptions) else dict(task_options or {})
        if options.get("settings_snapshot") is None:
            options["settings_snapshot"] = self.settings.snapshot()
        request = {"operation_id": operation_id, "action": "recover", "source_path": record["source_path"],
                   "target_operation_id": record["operation_id"], "recovery_action": "rollback",
                   "task_options": options}
        self._requests[operation_id] = request
        self.client.submit(request)
        return operation_id

    def _start_enrichment(self, operation_id, filepath, snapshot):
        self.last_inference_failure = None
        reader = EnrichmentReader(operation_id, filepath, snapshot, self)
        self._readers[operation_id] = reader
        reader.completed.connect(self._enriched)
        reader.finished.connect(lambda op=operation_id: self._reader_finished(op))
        reader.start()

    def _reader_finished(self, parent_id):
        reader = self._readers.pop(parent_id, None)
        if reader:
            reader.deleteLater()
        self._requests.pop(parent_id, None)
        if self._enrichment_queue and not self._closing:
            self._start_enrichment(*self._enrichment_queue.popleft())

    def _enriched(self, result):
        if self._closing or result.get("parent_operation_id") in self._cancelled_enrichments:
            return
        if result.get("error"):
            self.last_inference_failure = {"parent_operation_id": result.get("parent_operation_id"), "error": str(result["error"])}
            from .diagnostics import record
            record("optional_inference_failed", **self.last_inference_failure)
            self.signals.progress.emit("Image operation completed; optional tagging failed: " + result["error"])
            return
        parent_id = result["parent_operation_id"]
        tags = result.get("tags", [])
        self.last_inference_reply = dict(result)
        receipt = {key: result[key] for key in (
            "provider", "cuda_compute_events", "tensor_sha256", "fallback_reason", "model_sha256",
            "labels_sha256", "model_component_version", "component_version", "component_id", "component_sha256",
            "reader_isolation", "tags", "tag_scores", "input_sha256") if key in result}
        self.last_inference_receipt = receipt
        fallback = receipt.get("fallback_reason")
        self.signals.progress.emit("Optional tagging provider: " + str(receipt.get("provider", "unreported")) +
                                   ("; fallback: " + str(fallback) if fallback else ""))
        if not tags:
            return
        operation_id = uuid.uuid4().hex
        request = {"operation_id": operation_id, "action": "metadata",
                   "source_path": result["filepath"], "parent_operation_id": parent_id,
                   "tags": tags, "expected_sha256": result["input_sha256"],
                   "component_receipt": receipt,
                   "task_options": self._requests[parent_id]["task_options"]}
        self._requests[operation_id] = request
        try:
            self.client.submit(request)
        except OverflowError:
            self._requests.pop(operation_id, None)
            self.signals.progress.emit("Image committed; optional metadata was not written because the operation backlog is full.")

    def stop(self, timeout_ms=30000):
        """Compatibility drain for callers/tests; the GUI close path uses shutdown()."""
        deadline = time.monotonic() + timeout_ms / 1000
        while self.client.pending and time.monotonic() < deadline:
            self.client.poll()
            QCoreApplication.processEvents()
            time.sleep(0.005)
        return not self.client.pending

    def shutdown(self):
        self._closing = True
        self._enrichment_queue.clear()
        for reader in self._readers.values():
            reader.requestInterruption()
        self.client.disconnect()

    def readers_running(self):
        return any(reader.isRunning() for reader in self._readers.values())
