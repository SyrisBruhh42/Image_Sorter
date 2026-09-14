"""Recovery selection is explicit and refuses records without safe authority."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer
from imagesorter.ui_recovery import RecoveryDialog, eligible, explanation


def test_legacy_and_system_trash_are_manual_only(qtbot, tmp_path):
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[])
    qtbot.addWidget(viewer)
    legacy = {"operation_id": "legacy", "source_path": str(tmp_path / "untouched.jpg"), "state": "recovery_required"}
    assert not eligible(legacy)
    assert viewer.worker.add_recovery_task(legacy) is None
    system_trash = {**legacy, "manifest": {"version": 2, "mode": "system_trash"}}
    assert not eligible(system_trash)
    viewer._recovery_records = [legacy, system_trash]
    dialog = RecoveryDialog(viewer)
    qtbot.addWidget(dialog)
    assert not dialog.rollback.isEnabled()
    assert "manual review only" in dialog.records.item(0).text()
    assert not dialog.technical.isChecked()
    assert "Automatic rollback is unavailable" in dialog.details.toPlainText()
    dialog.technical.setChecked(True)
    assert '"operation_id": "legacy"' in dialog.details.toPlainText()
    assert not viewer.worker.client.pending


def test_declined_recovery_does_not_submit(qtbot, tmp_path, monkeypatch):
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[])
    qtbot.addWidget(viewer)
    record = {"operation_id": "v2", "source_path": str(tmp_path / "original.jpg"), "state": "recovery_required",
              "manifest": {"version": 2, "mode": "move"}}
    assert eligible(record)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert viewer.request_recovery(record) is None
    assert not viewer.worker.client.pending


def test_recovery_submission_preserves_view_generation(qtbot, tmp_path, monkeypatch):
    from imagesorter.operation_contracts import TaskOptions

    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[])
    qtbot.addWidget(viewer)
    submitted = []
    monkeypatch.setattr(viewer.worker.client, "submit", submitted.append)
    record = {"operation_id": "owned", "source_path": str(tmp_path / "original.jpg"),
              "state": "recovery_required", "manifest": {"version": 2, "mode": "move"}}
    operation_id = viewer.worker.add_recovery_task(record, task_options=TaskOptions(view_generation=42))
    assert submitted[0]["operation_id"] == operation_id
    assert submitted[0]["task_options"]["view_generation"] == 42
    assert submitted[0]["task_options"]["settings_snapshot"] == viewer.settings.snapshot()


def test_recovery_dialog_accessibility_and_escape_dismissal(qtbot, tmp_path):
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[])
    qtbot.addWidget(viewer)
    dialog = RecoveryDialog(viewer)
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.technical.accessibleName() == "Show technical record"
    assert dialog.technical.toolTip() == "Show raw JSON journal record for advanced debugging."
    assert dialog.rollback.accessibleName() == "Review and request rollback"
    assert dialog.rollback.toolTip() == "Review selected record details and request automatic transaction rollback."

    assert dialog.isVisible()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()


def test_recovery_utility_docstrings():
    assert eligible.__doc__ is not None and len(eligible.__doc__.strip()) > 0
    assert explanation.__doc__ is not None and len(explanation.__doc__.strip()) > 0
