from __future__ import annotations

from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication

from imagesorter.operation_contracts import OperationState
from imagesorter.operation_engine import OperationEngine
from imagesorter.operation_journal import OperationJournal
from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager


def _settle(worker: QueueWorker) -> None:
    worker.stop()
    QCoreApplication.processEvents()


def test_move_and_undo_preserve_sidecar_as_one_file_set(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()
    image = source_dir / "photo.jpg"
    sidecar = source_dir / "photo.jpg.txt"
    image.write_text("image bytes")
    sidecar.write_text("human note\nAI: old")

    worker = QueueWorker(SettingsManager(filepath=str(tmp_path / "settings.json")))
    results: list[dict] = []
    worker.signals.operation_result.connect(results.append)
    worker.add_task("move", str(image), str(destination_dir))
    _settle(worker)

    token = results[-1]["undo_token"]
    moved = destination_dir / "photo.jpg"
    moved_sidecar = destination_dir / "photo.jpg.txt"
    assert moved.exists() and moved_sidecar.exists()
    assert not image.exists() and not sidecar.exists()
    assert token["companions"][0]["current"] == str(moved_sidecar)

    worker.add_task("undo_move", str(moved), token)
    _settle(worker)

    assert image.read_text() == "image bytes"
    assert sidecar.read_text() == "human note\nAI: old"
    assert not moved.exists() and not moved_sidecar.exists()


def test_undo_copy_refuses_a_modified_sidecar(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()
    image = source_dir / "photo.jpg"
    sidecar = source_dir / "photo.jpg.txt"
    image.write_text("image bytes")
    sidecar.write_text("original note")

    worker = QueueWorker(SettingsManager(filepath=str(tmp_path / "settings.json")))
    results: list[dict] = []
    worker.signals.operation_result.connect(results.append)
    worker.add_task("copy", str(image), str(destination_dir))
    _settle(worker)
    token = results[-1]["undo_token"]
    copied = destination_dir / "photo.jpg"
    copied_sidecar = destination_dir / "photo.jpg.txt"
    copied_sidecar.write_text("edited after copy")

    worker.add_task("undo_copy", str(copied), token)
    _settle(worker)

    assert results[-1]["state"] == OperationState.FAILED
    assert copied.exists()
    assert copied_sidecar.read_text() == "edited after copy"


def test_undo_without_complete_token_is_non_destructive(tmp_path):
    copied = tmp_path / "copied.jpg"
    copied.write_text("valuable copy")
    worker = QueueWorker(SettingsManager(filepath=str(tmp_path / "settings.json")))
    results: list[dict] = []
    worker.signals.operation_result.connect(results.append)

    worker.add_task("undo_copy", str(copied))
    _settle(worker)

    assert results[-1]["state"] == OperationState.FAILED
    assert "Undo token" in results[-1]["error"]
    assert copied.read_text() == "valuable copy"


def test_journal_commit_failure_does_not_report_moved_file_as_failed(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()
    image = source_dir / "photo.jpg"
    image.write_text("image bytes")

    journal = OperationJournal(str(tmp_path / "journal.db"))
    engine = OperationEngine(journal)
    with patch.object(
        journal, "finish_result", side_effect=OSError("journal disk full")
    ):
        results = [engine.execute({"operation_id": "disk-full-commit", "action": "move",
                                   "source_path": str(image), "destination_path": str(destination_dir)})]

    assert results[-1]["state"] == OperationState.COMPLETED_WITH_WARNING
    assert "journal update failed" in results[-1]["warning"]
    assert not image.exists()
    assert (destination_dir / "photo.jpg").read_text() == "image bytes"


def test_sidecar_name_collision_advances_the_whole_file_set(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()
    image = source_dir / "photo.jpg"
    sidecar = source_dir / "photo.jpg.txt"
    image.write_text("new image")
    sidecar.write_text("new sidecar")
    (destination_dir / "photo.jpg.txt").write_text("unrelated existing sidecar")

    worker = QueueWorker(SettingsManager(filepath=str(tmp_path / "settings.json")))
    results: list[dict] = []
    worker.signals.operation_result.connect(results.append)
    worker.add_task("move", str(image), str(destination_dir))
    _settle(worker)

    assert results[-1]["destination_path"] == str(destination_dir / "photo_1.jpg")
    assert (destination_dir / "photo_1.jpg").read_text() == "new image"
    assert (destination_dir / "photo_1.jpg.txt").read_text() == "new sidecar"
    assert (destination_dir / "photo.jpg.txt").read_text() == "unrelated existing sidecar"
