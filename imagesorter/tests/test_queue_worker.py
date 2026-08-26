import os
import shutil
import time
import errno
import pytest
from src.queue_worker import QueueWorker
from src.settings_manager import SettingsManager

@pytest.fixture
def test_env(tmp_path):
    """Fixture providing isolated file testing environment."""
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    file_path = src_dir / "test.jpg"
    file_path.write_text("dummy")

    settings_file = tmp_path / "settings.json"
    settings = SettingsManager(filepath=str(settings_file))

    return {
        "src_dir": src_dir,
        "dest_dir": dest_dir,
        "file_path": file_path,
        "settings": settings
    }

def test_file_move(test_env, qtbot):
    """Test standard file move operation."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])
    dest_dir = str(test_env["dest_dir"])

    with qtbot.waitSignal(worker.signals.finished, timeout=1000):
        worker.add_task('move', file_path, dest_dir)

    assert not os.path.exists(file_path)
    assert os.path.exists(os.path.join(dest_dir, "test.jpg"))
    worker.stop()

def test_file_copy(test_env, qtbot):
    """Test standard file copy operation."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])
    dest_dir = str(test_env["dest_dir"])

    with qtbot.waitSignal(worker.signals.finished, timeout=1000):
        worker.add_task('copy', file_path, dest_dir)

    assert os.path.exists(file_path)
    assert os.path.exists(os.path.join(dest_dir, "test.jpg"))
    worker.stop()

def test_collision_handling(test_env, qtbot):
    """Test name suffixing on file collision."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])
    dest_dir = str(test_env["dest_dir"])

    # Create collision
    existing_file = test_env["dest_dir"] / "test.jpg"
    existing_file.write_text("existing")

    with qtbot.waitSignal(worker.signals.finished, timeout=1000) as blocker:
        worker.add_task('move', file_path, dest_dir)

    final_path = blocker.args[0]

    assert not os.path.exists(file_path)
    assert os.path.exists(final_path)
    assert final_path != str(existing_file)
    assert os.path.basename(final_path).startswith("test_")
    worker.stop()

def test_undo_stack_transaction(test_env, qtbot):
    """Test undoing a move operation."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])
    dest_dir = str(test_env["dest_dir"])

    # 1. Do Move
    undo_data = None
    def on_undo(data):
        nonlocal undo_data
        undo_data = data

    worker.signals.undo_record.connect(on_undo)

    with qtbot.waitSignal(worker.signals.finished, timeout=1000):
        worker.add_task('move', file_path, dest_dir)

    assert undo_data is not None
    assert undo_data['action'] == 'move'
    assert undo_data['original'] == file_path

    # 2. Do Undo Move
    current_path = undo_data['current']
    with qtbot.waitSignal(worker.signals.finished, timeout=1000):
         worker.add_task('undo_move', current_path, file_path)

    assert os.path.exists(file_path)
    assert not os.path.exists(current_path)
    worker.stop()

def test_adversarial_disk_full(test_env, qtbot, mocker):
    """Test simulating a disk full error during move."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])
    dest_dir = str(test_env["dest_dir"])

    # Mock shutil.move to raise disk full error
    mock_move = mocker.patch('shutil.move', side_effect=OSError(errno.ENOSPC, "No space left on device"))

    with qtbot.waitSignal(worker.signals.error, timeout=1000):
        worker.add_task('move', file_path, dest_dir)

    assert mock_move.called
    assert os.path.exists(file_path) # Original file should still be there
    worker.stop()

def test_adversarial_permission_error_retry(test_env, qtbot, mocker):
    """Test simulating a permission error with retry exponential backoff."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])
    dest_dir = str(test_env["dest_dir"])

    # Mock to fail twice then succeed
    mock_move = mocker.patch('shutil.move', side_effect=[PermissionError("Locked"), PermissionError("Locked"), None])

    # Mock time.sleep to run fast
    mocker.patch('time.sleep', return_value=None)

    with qtbot.waitSignal(worker.signals.finished, timeout=2000):
        worker.add_task('move', file_path, dest_dir)

    assert mock_move.call_count == 3
    worker.stop()

def test_adversarial_undo_missing_original(test_env, qtbot, mocker):
    """Test undo stack integrity when original paths are deleted."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])
    dest_dir = str(test_env["dest_dir"])

    # 1. Do Move
    undo_data = None
    def on_undo(data):
        nonlocal undo_data
        undo_data = data

    worker.signals.undo_record.connect(on_undo)

    with qtbot.waitSignal(worker.signals.finished, timeout=1000):
        worker.add_task('move', file_path, dest_dir)

    # 2. Delete original path parent dir (simulate abrupt folder deletion)
    shutil.rmtree(test_env["src_dir"])

    current_path = undo_data['current']

    # Undo should fail to move back because directory doesn't exist
    # but the error signal should be emitted and application shouldn't crash
    with qtbot.waitSignal(worker.signals.error, timeout=1000):
         worker.add_task('undo_move', current_path, file_path)

    worker.stop()

def test_file_trash(test_env, qtbot, mocker):
    """Test standard file trash operation."""
    worker = QueueWorker(test_env["settings"])

    file_path = str(test_env["file_path"])

    # Mock send2trash so we don't actually move to OS trash
    mock_trash = mocker.patch('src.queue_worker.send2trash')

    with qtbot.waitSignal(worker.signals.finished, timeout=1000):
        worker.add_task('trash', file_path)

    assert mock_trash.called
    worker.stop()

def test_rapid_queue_flooding(test_env, qtbot, mocker):
    """Test rapid multi-threaded queue flooding."""
    # Temporarily increase threads for this test to ensure multithreading is exercised
    test_env["settings"].set('advanced', 'worker_threads', 4)
    worker = QueueWorker(test_env["settings"])

    dest_dir = str(test_env["dest_dir"])

    # Create 100 dummy files
    files_to_move = []
    for i in range(100):
        f = test_env["src_dir"] / f"flood_{i}.jpg"
        f.write_text("flood")
        files_to_move.append(str(f))

    # We want to wait for all finished signals
    # Since we can't easily use qtbot.waitSignals with a dynamic count, we'll use a manual count
    # and wait for it to reach 100.

    finished_count = 0
    def on_finished(path):
        nonlocal finished_count
        finished_count += 1

    worker.signals.finished.connect(on_finished)

    # Flood the queue
    for file_path in files_to_move:
        worker.add_task('move', file_path, dest_dir)

    # Wait for the worker to process everything
    qtbot.waitUntil(lambda: finished_count == 100, timeout=5000)
    worker.stop()

    assert finished_count == 100
    for file_path in files_to_move:
        assert not os.path.exists(file_path)
        assert os.path.exists(os.path.join(dest_dir, os.path.basename(file_path)))
