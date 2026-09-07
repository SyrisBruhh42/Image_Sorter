from __future__ import annotations

import errno
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication

from imagesorter.operation_contracts import OperationState
from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager


def test_unchanged_file_undo(qtbot, tmp_path):
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

    # Perform move
    worker.add_task("move", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(results) == 1
    move_res = results[0]
    token = move_res["undo_token"]
    moved_path = move_res["destination_path"]

    # Perform undo
    results.clear()
    worker.add_task("undo_move", moved_path, token)
    worker.stop()
    QCoreApplication.processEvents()

    assert len(results) == 1
    undo_res = results[0]
    assert undo_res["state"] == OperationState.COMPLETED
    assert src_file.exists()
    assert src_file.read_text() == "photo data"


def test_tampered_destination_undo_refusal(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "photo.jpg"
    src_file.write_text("original content")

    results = []
    worker.signals.operation_result.connect(lambda res: results.append(res))

    worker.add_task("move", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    token = results[0]["undo_token"]
    moved_path = results[0]["destination_path"]

    # Tamper with file content at current destination
    with open(moved_path, "w") as f:
        f.write("TAMPERED CONTENT!")

    results.clear()
    errors = []
    worker.signals.error.connect(lambda f, err: errors.append((f, err)))

    worker.add_task("undo_move", moved_path, token)
    worker.stop()
    QCoreApplication.processEvents()

    assert len(results) == 1
    assert results[0]["state"] == OperationState.FAILED
    assert any(term in results[0]["error"].lower() for term in ["mismatch", "altered", "tampered"])
    assert len(errors) == 1


def test_conflicting_restoration_target_refusal(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "photo.jpg"
    src_file.write_text("moved image content")

    results = []
    worker.signals.operation_result.connect(lambda res: results.append(res))

    worker.add_task("move", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    token = results[0]["undo_token"]
    moved_path = results[0]["destination_path"]

    # Re-create a file at the original source location to simulate collision
    src_file.write_text("NEW UNRELATED FILE AT SRC")

    results.clear()
    worker.add_task("undo_move", moved_path, token)
    worker.stop()
    QCoreApplication.processEvents()

    assert len(results) == 1
    assert results[0]["state"] == OperationState.FAILED
    assert "already exists" in results[0]["error"]
    assert src_file.read_text() == "NEW UNRELATED FILE AT SRC"


def test_enospc_during_stream_copy_preserves_source(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "large_photo.jpg"
    src_file.write_text("valuable source image data")

    results = []
    worker.signals.operation_result.connect(lambda res: results.append(res))

    enospc_err = OSError(errno.ENOSPC, "No space left on device")
    with patch("shutil.copyfileobj", side_effect=enospc_err):
        worker.add_task("move", str(src_file), str(dst_dir))
        worker.stop()
        QCoreApplication.processEvents()

    assert len(results) == 1
    assert results[0]["state"] == OperationState.FAILED
    assert "No space left on device" in results[0]["error"]
    assert src_file.exists()
    assert src_file.read_text() == "valuable source image data"


def test_missing_restoration_directory(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "test.jpg"
    src_file.write_text("content")

    results = []
    worker.signals.operation_result.connect(lambda res: results.append(res))

    worker.add_task("move", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    token = results[0]["undo_token"]
    moved_path = results[0]["destination_path"]

    # Delete original directory src_dir
    import shutil
    shutil.rmtree(src_dir)

    results.clear()
    worker.add_task("undo_move", moved_path, token)
    worker.stop()
    QCoreApplication.processEvents()

    assert len(results) == 1
    assert results[0]["state"] == OperationState.FAILED
    assert "missing or invalid" in results[0]["error"]
