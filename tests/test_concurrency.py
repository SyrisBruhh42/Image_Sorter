import errno
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QTimer

from imagesorter.operation_engine import OperationEngine
from imagesorter.operation_journal import OperationJournal
from imagesorter.platform_capabilities import mutation_unavailable_reason
from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager


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

    def collect(result):
        if result["state"].startswith("completed"):
            finished_files.append(result["destination_path"])
            if result["undo_token"]:
                undo_tokens.append(result["undo_token"])
        else:
            errors.append((result["source_path"], result["error"]))

    worker.signals.operation_result.connect(collect)

    # Serial FULL-durability transactions replace the old 8-thread nondurable
    # throughput assumption. Keep all 200 data/Undo checks; GUI stays responsive.
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(bool(worker.client.pending)))
    timer.start(20)
    started = time.monotonic()
    if reason := mutation_unavailable_reason():
        # Experimental platforms must reject unsupported durability before
        # admitting work, rather than leave requests hanging or weaken sync.
        for fp in file_paths:
            with pytest.raises(RuntimeError, match="unavailable"):
                worker.add_task("move", fp, str(dst_dir))
        qtbot.waitUntil(lambda: len(ticks) >= 2, timeout=5000)
        timer.stop()
        worker.shutdown()
        assert not worker.client.pending and not worker._requests
        assert worker.client.process is None and worker.client.connection is None
        assert not finished_files and not undo_tokens and not errors
        assert not list(dst_dir.iterdir())
        for i, filepath in enumerate(file_paths):
            assert Path(filepath).read_text() == f"dummy content {i}"
        assert "Durable" in reason
        return
    for fp in file_paths:
        worker.add_task("move", fp, str(dst_dir))

    # Wait for all background tasks in thread pool to finish
    try:
        qtbot.waitUntil(lambda: len(finished_files) + len(errors) == num_files, timeout=60000)
    except Exception as exc:
        raise AssertionError(f"Terminal results: {len(finished_files)} completed, {len(errors)} failed; {len(worker.client.pending)} pending; errors={errors[:5]}") from exc
    worker.stop()
    print(f"Durable batch: {num_files} moves in {time.monotonic() - started:.3f}s")
    assert len(ticks) >= 2
    assert any(ticks)  # GUI events were delivered while accepted work was pending.

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

    def collect_restored(result):
        if result["action"] == "undo_move" and result["state"].startswith("completed"):
            restored_files.append(result["destination_path"])

    worker.signals.operation_result.connect(collect_restored)

    # Verify 100% undo rollback accuracy across all 200 moved files
    for token in undo_tokens:
        worker.add_task("undo_move", token["current"], token)

    qtbot.waitUntil(lambda: len(restored_files) == num_files, timeout=60000)
    worker.stop()
    timer.stop()

    # All files must be restored back to original source directory
    for i in range(num_files):
        orig_p = src_dir / f"img_{i:03d}.jpg"
        assert orig_p.exists()
        assert orig_p.read_text() == f"dummy content {i}"
        moved_p = dst_dir / f"img_{i:03d}.jpg"
        assert not moved_p.exists()


def test_queue_worker_enospc_disk_full(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    test_file = src_dir / "large_photo.jpg"
    test_file.write_text("photo data")

    engine = OperationEngine(OperationJournal(str(tmp_path / "journal.db")))
    errors = []
    # Exercise the engine's actual OS write, across the new process boundary.
    enospc_error = OSError(errno.ENOSPC, "No space left on device")
    with patch("imagesorter.file_safety.os.write", side_effect=enospc_error):
        result = engine.execute({"operation_id": "enospc", "action": "move",
                                 "source_path": str(test_file), "destination_path": str(dst_dir)})
    assert result["state"] == "failed"
    errors.append((result["source_path"], result["error"]))

    assert len(errors) == 1
    assert errors[0][0] == str(test_file)
    if reason := mutation_unavailable_reason():
        # Refusal precedes the injected write on an unsupported platform.
        assert errors[0][1] == reason
        assert not list(dst_dir.iterdir())
    else:
        assert "No space left on device" in errors[0][1]
    # Verify non-destructive integrity: original source file must remain intact
    assert test_file.exists()
    assert test_file.read_text() == "photo data"
