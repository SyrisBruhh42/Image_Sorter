from __future__ import annotations

import os
from unittest.mock import MagicMock

from PyQt6.QtCore import QCoreApplication

from imagesorter.operation_contracts import OperationState, TaskOptions
from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager


def test_add_task_returns_operation_id_and_emits_operation_result(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "test.jpg"
    src_file.write_text("image content")

    results: list[dict] = []
    signals_order: list[str] = []

    worker.signals.progress.connect(lambda msg: signals_order.append("progress"))
    worker.signals.operation_result.connect(
        lambda res: (signals_order.append("operation_result"), results.append(res))
    )

    custom_op_id = "custom_op_12345"
    op_id = worker.add_task(
        "move",
        str(src_file),
        str(dst_dir),
        operation_id=custom_op_id,
        task_options=TaskOptions(view_generation=42),
    )

    assert op_id == custom_op_id

    worker.stop()
    QCoreApplication.processEvents()

    assert len(results) == 1
    res = results[0]
    assert res["schema_version"] == 1
    assert res["operation_id"] == custom_op_id
    assert res["action"] == "move"
    assert res["source_path"] == str(src_file)
    assert res["destination_path"] == str(dst_dir / "test.jpg")
    assert res["state"] == OperationState.COMPLETED
    assert res["view_generation"] == 42
    assert res["undo_token"] is not None
    assert res["undo_token"]["version"] == 1
    assert res["undo_token"]["original"] == str(src_file)
    assert res["undo_token"]["current"] == str(dst_dir / "test.jpg")

    assert signals_order == ["progress", "operation_result"]


def test_undo_token_structure(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "photo.jpg"
    src_file.write_text("photo data")

    results = []
    worker.signals.operation_result.connect(lambda res: results.append(res))

    worker.add_task("copy", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(results) == 1
    token = results[0]["undo_token"]
    assert token["version"] == 1
    assert "token_id" in token
    assert token["action"] == "copy"
    assert token["original"] == str(src_file)
    assert token["current"] == str(dst_dir / "photo.jpg")
    assert "provenance" in token
    assert token["provenance"]["size"] == len("photo data")
    assert "sha256" in token["provenance"]


def test_completed_with_warning_on_metadata_failure(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    sm.set("ai_tagger", "enabled", True)

    worker = QueueWorker(sm)
    # Mock ai_tagger on worker to return tags but fail write_metadata
    mock_tagger = MagicMock()
    mock_tagger.get_tags.return_value = ["cat", "cute"]
    worker.ai_tagger = mock_tagger

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "cat.jpg"
    src_file.write_text("cat photo data")

    results = []
    worker.signals.operation_result.connect(lambda res: results.append(res))

    from unittest.mock import patch
    patcher = patch("imagesorter.queue_worker.write_metadata", side_effect=OSError("Disk full writing metadata"))
    patcher.start()

    try:
        worker.add_task("move", str(src_file), str(dst_dir))
        worker.stop()
        QCoreApplication.processEvents()
    finally:
        patcher.stop()

    assert len(results) == 1
    res = results[0]
    assert res["state"] == OperationState.COMPLETED_WITH_WARNING
    assert "Disk full writing metadata" in res["warning"]
    # Destination file exists despite metadata warning
    assert os.path.exists(dst_dir / "cat.jpg")
