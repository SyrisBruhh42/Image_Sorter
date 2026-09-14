"""Optional component controls; all slow work runs in a disposable process."""
from __future__ import annotations

import json
import os
import signal

from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .component_manager import DOWNLOAD_UNAVAILABLE, ComponentManager
from .component_runtime import component_job_command


class ComponentsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Optional components")
        self.process = None
        self.pending_output = b""
        self.component_status = {}
        layout = QVBoxLayout(self)
        description = QLabel("Install only the capabilities you choose. The base sorter works offline without downloads.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Component", "Installed", "State", "Download", "Storage"])
        self.table.setAccessibleName("Component versions and compatibility")
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.component = QComboBox()
        self.component.setAccessibleName("Select component")
        row.addWidget(self.component)
        self.action = QComboBox()
        for label, value in (("Install / Update", "install"), ("Enable", "enable"), ("Disable", "disable"),
                             ("Verify", "verify"), ("Roll Back", "rollback"), ("Remove", "remove"),
                             ("Recover interrupted installations", "recover")):
            self.action.addItem(label, value)
        self.action.setAccessibleName("Component action")
        row.addWidget(self.action)
        self.run_button = QPushButton("Apply")
        self.run_button.setAccessibleName("Apply component action button")
        self.run_button.setAccessibleDescription("Applies the selected component action to the chosen component.")
        self.run_button.clicked.connect(self.apply)
        row.addWidget(self.run_button)
        self.import_button = QPushButton("Import pack…")
        self.import_button.setAccessibleName("Import component pack button")
        self.import_button.setAccessibleDescription("Opens file dialog to import a pinned component pack archive.")
        self.import_button.clicked.connect(self.import_pack)
        row.addWidget(self.import_button)
        self.legacy_button = QPushButton("Import existing model…")
        self.legacy_button.setAccessibleName("Import checksum-verified existing model and labels")
        self.legacy_button.clicked.connect(self.import_legacy)
        row.addWidget(self.legacy_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAccessibleName("Cancel component job button")
        self.cancel_button.setAccessibleDescription("Cancels active component operation safely.")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.download_label = QLabel()
        self.download_label.setWordWrap(True)
        self.download_label.setAccessibleName("Component download availability")
        layout.addWidget(self.download_label)
        self.status_label = QLabel("No network activity occurs until an available download is requested.")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Component operation status")
        layout.addWidget(self.status_label)
        self.provider_label = QLabel("Actual inference provider: no inference completed in this session.")
        self.provider_label.setWordWrap(True)
        self.provider_label.setAccessibleName("Actual inference provider and fallback")
        layout.addWidget(self.provider_label)
        self.component.currentTextChanged.connect(self.update_actions)
        self.action.currentIndexChanged.connect(self.update_actions)
        self.refresh()

    def update_actions(self, *_args):
        entry = self.component_status.get(self.component.currentText(), {})
        available = entry.get("download_available", False)
        self.download_label.setText("Verified download available." if available else DOWNLOAD_UNAVAILABLE)
        self.run_button.setEnabled(self.process is None and
                                   (self.action.currentData() != "install" or available))

    def refresh(self):
        try:
            rows = ComponentManager().status()
        except Exception as exc:
            self.status_label.setText(f"Component store requires attention: {exc}")
            return
        self.component_status = {entry["id"]: entry for entry in rows}
        selected = self.component.currentText()
        self.component.clear()
        self.table.setRowCount(len(rows))
        for index, entry in enumerate(rows):
            self.component.addItem(entry["id"])
            state = ("Enabled" if entry["enabled"] else "Disabled") if entry["active"] else "Not installed"
            if not entry["compatible"]:
                state = entry.get("compatibility_detail", "No qualified artifact for this platform")
            if entry.get("pending_jobs"):
                state += f'; {len(entry["pending_jobs"])} pending/recovery record(s)'
            values = [entry["id"], entry["active"] or "—", state,
                      (f'{entry["archive_size"] / 1048576:.1f} MiB' if entry.get("download_available") else "Unavailable"),
                      f'{entry["installed_size"] / 1048576:.1f} MiB']
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.component.setCurrentText(selected)
        self.table.resizeColumnsToContents()
        self.update_actions()
        parent = self.parent()
        while parent is not None:
            worker = getattr(parent, "worker", None)
            receipt = getattr(worker, "last_inference_receipt", None)
            if receipt:
                self.provider_label.setText(f'Actual provider: {receipt.get("provider", "unknown")}; '
                    f'CUDA compute events: {receipt.get("cuda_compute_events", 0)}. '
                    f'Fallback: {receipt.get("fallback_reason") or "none"}.')
                break
            parent = parent.parent()

    def apply(self):
        action = self.action.currentData()
        if action == "install" and not self.component_status.get(self.component.currentText(), {}).get("download_available"):
            self.download_label.setText(DOWNLOAD_UNAVAILABLE)
            return
        if action in {"install", "remove", "rollback"}:
            text = ("Download and activate the selected verified component?" if action == "install" else
                    "Apply this component change? Active file operations and recovery material are preserved.")
            if QMessageBox.question(self, "Optional component", text) != QMessageBox.StandardButton.Yes:
                return
        self.start([action, self.component.currentText()])

    def import_pack(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Import pinned component pack", "", "Component pack (*.tar.gz)")
        if filename:
            self.start(["install", self.component.currentText(), "--archive", filename])

    def import_legacy(self):
        directory = QFileDialog.getExistingDirectory(self, "Import existing pinned model and labels")
        if directory:
            self.start(["import-model", "ai.mobilenet-v2", "--archive", directory])

    def start(self, arguments):
        if self.process is not None:
            return
        self.pending_output = b""
        self.process = QProcess(self)
        if os.name == "posix" and hasattr(QProcess, "UnixProcessParameters"):
            parameters = QProcess.UnixProcessParameters()
            parameters.flags = QProcess.UnixProcessFlag.CreateNewSession
            self.process.setUnixProcessParameters(parameters)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        command = component_job_command(arguments)
        self.run_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.legacy_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.process.start(command[0], command[1:])

    def _read(self):
        if self.process is None:
            return
        self.pending_output += bytes(self.process.readAllStandardOutput())
        if len(self.pending_output) > 65536:
            self.pending_output = b""
            self.status_label.setText("Component job exceeded its bounded status channel; cancelled safely.")
            self.cancel()
            return
        while b"\n" in self.pending_output:
            line, self.pending_output = self.pending_output.split(b"\n", 1)
            try:
                value = json.loads(line)
                self.status_label.setText(value.get("error") or f'Component operation: {value.get("state", "working")}')
            except ValueError:
                self.status_label.setText(line.decode(errors="replace")[-1500:])

    def _error(self, _error):
        self.status_label.setText("Component process could not complete; verify the store before retrying.")
        if self.process is not None and self.process.state() == QProcess.ProcessState.NotRunning:
            self._finished(1, QProcess.ExitStatus.CrashExit)

    def _finished(self, code, _status):
        self._read()
        process, self.process = self.process, None
        if process:
            process.deleteLater()
        self.run_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.legacy_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if code == 0:
            self.status_label.setText("Component operation completed and verified.")
        self.refresh()
        if code == 0:
            parent = self.parent()
            while parent is not None:
                if hasattr(parent, "refresh_ai_model_status"):
                    if not parent._close_pending:
                        parent.refresh_ai_model_status()
                    break
                parent = parent.parent()

    def cancel(self):
        if self.process:
            process = self.process
            self._signal_group(process, signal.SIGTERM)
            QTimer.singleShot(1000, lambda: self._signal_group(process, getattr(signal, "SIGKILL", 9))
                             if self.process is process else None)

    @staticmethod
    def _signal_group(process, sig):
        pid = int(process.processId())
        if os.name == "posix" and pid > 0:
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, sig)
                    return
            except ProcessLookupError:
                return
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    def closeEvent(self, event):
        self.cancel()
        super().closeEvent(event)
