"""Explicit, selected-record recovery; engine authority remains in the service."""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


MAX_RECORDS_LIST_HEIGHT = 160


def eligible(record: dict) -> bool:
    """Check if an operation journal record is eligible for automatic durable rollback.

    Returns True if the record state is 'recovery_required', unresolved, uses manifest
    schema version 2, and is not a system_trash operation.
    """
    manifest = record.get("manifest") or {}
    return (record.get("state") == "recovery_required" and not record.get("resolved_by") and
            manifest.get("version") == 2 and manifest.get("mode") != "system_trash")


def explanation(record: dict) -> str:
    """Format a human-readable recovery explanation string from an operation journal record.

    Includes operation action type, original path, destination path, reasons for review,
    rollback behavior, and all recorded file set manifest paths.
    """
    action = str(record.get("action", "file operation")).replace("_", " ").capitalize()
    text = [f"Operation: {action}", f"Original image: {record.get('source_path', 'Not recorded')}",
            f"Intended destination: {record.get('destination_path') or 'Not recorded'}", "",
            "Why review is needed:", str(record.get("warning_message") or record.get("error_message") or
                                         "The operation was interrupted before its final outcome could be verified."), ""]
    if eligible(record):
        text.extend(["What a verified rollback will do:",
                     ("Restore the recorded original image and companion note where their saved identities still match. "
                     "Remove only proven outputs of this interrupted transaction. Changed or ambiguous files stay preserved; "
                     "the result will explain anything that could not be restored."), ""])
    else:
        text.extend(["Automatic rollback is unavailable for this record.",
                     ("The older record or system-trash outcome does not provide enough authority for a safe automatic change. "
                     "Review the recorded locations manually; this window will not alter them."), ""])
    text.append("Recorded file locations (a recorded path is not proof that the file still exists):")
    for item in (record.get("manifest") or {}).get("files", []):
        text.append("\n" + ("Companion note" if item.get("role") == "sidecar" else "Image") + ":")
        for key, label in (("source", "Original"), ("claim", "Preserved original"), ("stage", "Staged copy"),
                           ("destination", "Published destination"), ("cleanup_claim", "Preserved output")):
            if item.get(key):
                text.append(f"  {label}: {item[key]}")
    if not (record.get("manifest") or {}).get("files"):
        text.append("No complete file-set manifest is available. The technical record may contain older artifact paths.")
    return "\n".join(text)


class RecoveryDialog(QDialog):
    """Dialog for reviewing interrupted transactions and requesting durable recovery rollback."""

    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.setWindowTitle("Preserved operation recovery")
        self.setAccessibleName("Preserved operation recovery dialog")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        self.status = QLabel("Select one record. Rollback verifies recorded identities and preserves ambiguous files.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.records = QListWidget()
        self.records.setMaximumHeight(MAX_RECORDS_LIST_HEIGHT)
        self.records.setAccessibleName("Unresolved recovery records")
        layout.addWidget(self.records)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Recovery explanation and recorded file paths")
        layout.addWidget(self.details)
        self.technical = QCheckBox("Show technical record")
        self.technical.setAccessibleDescription("Show the complete original journal record for advanced review.")
        self.technical.toggled.connect(self.selection_changed)
        layout.addWidget(self.technical)
        self.rollback = QPushButton("Review and request rollback…")
        self.rollback.setObjectName("recovery_rollback")
        self.rollback.setAccessibleName("Request Rollback Button")
        self.rollback.setAccessibleDescription("Requests a rollback for the selected interrupted operation after verifying recorded file identities.")
        self.rollback.setToolTip("Request rollback for the selected recorded operation.")
        layout.addWidget(self.rollback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.records.currentRowChanged.connect(self.selection_changed)
        self.rollback.clicked.connect(self.request)
        viewer.worker.signals.recovery_summary.connect(self.refresh)
        viewer.worker.signals.operation_result.connect(self.result)
        self.refresh()
        viewer.worker.client.refresh_recovery()

    def refresh(self, _message=None) -> None:
        """Refresh the recovery list items from current viewer records."""
        selected = self.selected()
        selected_id = selected.get("operation_id") if selected else None
        self.rows = list(self.viewer._recovery_records)
        self.records.clear()
        for row in self.rows:
            suffix = "rollback available" if eligible(row) else "manual review only"
            label = f"{Path(row.get('source_path', '')).name or 'Unknown image'} — {str(row.get('action', 'operation')).replace('_', ' ')} — {suffix}"
            self.records.addItem(label)
            self.records.item(self.records.count() - 1).setToolTip(
                f"Original: {row.get('source_path', '')}\nOperation ID: {row['operation_id']}\nDestination: {row.get('destination_path') or 'Not recorded'}")
        index = next((i for i, row in enumerate(self.rows) if row["operation_id"] == selected_id), 0)
        self.records.setCurrentRow(index if self.rows else -1)
        self.selection_changed()

    def selected(self) -> dict | None:
        """Return the currently selected recovery record dictionary or None."""
        index = self.records.currentRow()
        rows = getattr(self, "rows", [])
        return rows[index] if 0 <= index < len(rows) else None

    def selection_changed(self, _index=None) -> None:
        """Update explanation text and rollback button enable state when row selection changes."""
        record = self.selected()
        detail = json.dumps(record, indent=2, default=str) if record and self.technical.isChecked() else explanation(record) if record else "No unresolved records reported."
        self.details.setPlainText(detail)
        self.rollback.setEnabled(bool(record and eligible(record) and not self.viewer.worker.client.pending))

    def request(self) -> None:
        """Request durable recovery rollback for the currently selected record."""
        record = self.selected()
        if record and self.viewer.request_recovery(record):
            self.status.setText("Rollback requested. Waiting for the durable result; do not change preserved files.")
            self.rollback.setEnabled(False)

    def result(self, result: dict) -> None:
        """Handle operation result signal for recovery action and update status message."""
        if result.get("action") == "recover":
            self.status.setText("Rollback completed; the original record and receipt remain in the journal." if
                                result.get("resolved_operation_id") else "Rollback did not resolve the record: " +
                                str(result.get("error") or result.get("warning") or result.get("state")))
        self.selection_changed()
