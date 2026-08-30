import errno
from unittest.mock import patch

from imagesorter.settings_manager import SettingsManager
from imagesorter.queue_worker import QueueWorker


def test_queue_worker_concurrent_moves_and_undo(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    sm.set("advanced", "worker_threads", 8)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    num_files = 200
    file_paths = []
    for i in range(num_files):
        p = src_dir / f"img_{i:03d}.jpg"
        p.write_text(f"dummy content {i}")
        file_paths.append(str(p))

    worker = QueueWorker(sm)
    undo_tokens = []
    finished_files = []
    errors = []

    worker.signals.undo_record.connect(lambda token: undo_tokens.append(token))
    worker.signals.finished.connect(lambda f: finished_files.append(f))
    worker.signals.error.connect(lambda f, err: errors.append((f, err)))

    # Concurrent dispatch of 200 moves across 8 worker threads
    for fp in file_paths:
        worker.add_task("move", fp, str(dst_dir))

    # Wait for all background tasks in thread pool to finish
    qtbot.waitUntil(lambda: len(finished_files) == num_files, timeout=10000)
    worker.stop()

    assert len(errors) == 0, f"Encountered unexpected worker errors: {errors}"
    assert len(undo_tokens) == num_files
    assert len(finished_files) == num_files

    # Verify all files moved cleanly to destination without TOCTOU collisions or data races
    for i in range(num_files):
        orig_p = src_dir / f"img_{i:03d}.jpg"
        assert not orig_p.exists()
        moved_p = dst_dir / f"img_{i:03d}.jpg"
        assert moved_p.exists()
        assert moved_p.read_text() == f"dummy content {i}"

    restored_files = []
    worker.signals.finished.connect(lambda f: restored_files.append(f))

    # Verify 100% undo rollback accuracy across all 200 moved files
    for token in undo_tokens:
        worker.add_task("undo_move", token["current"], token["original"])

    qtbot.waitUntil(lambda: len(restored_files) == num_files, timeout=10000)
    worker.stop()

    # All files must be restored back to original source directory
    for i in range(num_files):
        orig_p = src_dir / f"img_{i:03d}.jpg"
        assert orig_p.exists()
        assert orig_p.read_text() == f"dummy content {i}"
        moved_p = dst_dir / f"img_{i:03d}.jpg"
        assert not moved_p.exists()


def test_queue_worker_enospc_disk_full(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    test_file = src_dir / "large_photo.jpg"
    test_file.write_text("photo data")

    worker = QueueWorker(sm)
    errors = []
    worker.signals.error.connect(lambda f, err: errors.append((f, err)))

    # Simulate ENOSPC disk full error during shutil.move
    enospc_error = OSError(errno.ENOSPC, "No space left on device")
    with patch("shutil.move", side_effect=enospc_error):
        worker.add_task("move", str(test_file), str(dst_dir))
        qtbot.waitUntil(lambda: len(errors) == 1, timeout=5000)
        worker.stop()

    assert len(errors) == 1
    assert errors[0][0] == str(test_file)
    assert "No space left on device" in errors[0][1]
    # Verify non-destructive integrity: original source file must remain intact
    assert test_file.exists()
    assert test_file.read_text() == "photo data"
