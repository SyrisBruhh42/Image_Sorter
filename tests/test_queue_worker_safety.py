import os
import shutil
import threading
import time
import pytest
from PyQt6.QtCore import QCoreApplication
from imagesorter.settings_manager import SettingsManager
from imagesorter.queue_worker import QueueWorker


def test_concurrent_same_name_moves(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir1 = tmp_path / "src1"
    src_dir2 = tmp_path / "src2"
    dst_dir = tmp_path / "dst"
    src_dir1.mkdir()
    src_dir2.mkdir()
    dst_dir.mkdir()

    file_1 = src_dir1 / "image.jpg"
    file_2 = src_dir2 / "image.jpg"
    file_1.write_text("content of file 1")
    file_2.write_text("content of file 2")

    finished_paths = []
    worker.signals.finished.connect(lambda path: finished_paths.append(path))

    barrier = threading.Barrier(2)

    def worker_thread(filepath):
        barrier.wait()
        worker.add_task("move", filepath, str(dst_dir))

    t1 = threading.Thread(target=worker_thread, args=(str(file_1),))
    t2 = threading.Thread(target=worker_thread, args=(str(file_2),))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    worker.stop()
    QCoreApplication.processEvents()

    assert len(finished_paths) == 2
    assert not file_1.exists()
    assert not file_2.exists()

    dst_files = list(dst_dir.glob("image*.jpg"))
    assert len(dst_files) == 2

    contents = {f.read_text() for f in dst_files}
    assert contents == {"content of file 1", "content of file 2"}


def test_concurrent_same_name_copies(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir1 = tmp_path / "src1"
    src_dir2 = tmp_path / "src2"
    dst_dir = tmp_path / "dst"
    src_dir1.mkdir()
    src_dir2.mkdir()
    dst_dir.mkdir()

    file_1 = src_dir1 / "image.jpg"
    file_2 = src_dir2 / "image.jpg"
    file_1.write_text("content of file 1")
    file_2.write_text("content of file 2")

    finished_paths = []
    worker.signals.finished.connect(lambda path: finished_paths.append(path))

    barrier = threading.Barrier(2)

    def worker_thread(filepath):
        barrier.wait()
        worker.add_task("copy", filepath, str(dst_dir))

    t1 = threading.Thread(target=worker_thread, args=(str(file_1),))
    t2 = threading.Thread(target=worker_thread, args=(str(file_2),))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    worker.stop()
    QCoreApplication.processEvents()

    assert len(finished_paths) == 2
    assert file_1.exists()
    assert file_2.exists()

    dst_files = list(dst_dir.glob("image*.jpg"))
    assert len(dst_files) == 2

    contents = {f.read_text() for f in dst_files}
    assert contents == {"content of file 1", "content of file 2"}


def test_existing_destination_collision_never_overwrites(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    existing_file = dst_dir / "photo.jpg"
    existing_file.write_text("existing destination content")

    new_file = src_dir / "photo.jpg"
    new_file.write_text("new photo content")

    finished_paths = []
    worker.signals.finished.connect(lambda path: finished_paths.append(path))

    worker.add_task("move", str(new_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(finished_paths) == 1
    assert finished_paths[0] == str(dst_dir / "photo_1.jpg")
    assert existing_file.read_text() == "existing destination content"
    assert (dst_dir / "photo_1.jpg").read_text() == "new photo content"


def test_invalid_destination_rejection(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "test.jpg"
    src_file.write_text("test")

    errors = []
    worker.signals.error.connect(lambda path, msg: errors.append((path, msg)))

    # Non-existent destination folder
    worker.add_task("move", str(src_file), str(tmp_path / "non_existent"))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(errors) == 1
    assert src_file.exists()


def test_same_file_rejection(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "test.jpg"
    src_file.write_text("test")

    errors = []
    worker.signals.error.connect(lambda path, msg: errors.append((path, msg)))

    worker.add_task("move", str(src_file), str(src_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(errors) == 1
    assert "same" in errors[0][1].lower()
    assert src_file.exists()


def test_cross_device_failure_preservation(qtbot, tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "test.jpg"
    src_file.write_text("important source data")

    errors = []
    finished = []
    worker.signals.error.connect(lambda path, msg: errors.append((path, msg)))
    worker.signals.finished.connect(lambda path: finished.append(path))

    def mock_copyfileobj(f_src, f_dst, length=65536):
        f_dst.write(f_src.read(10))
        raise IOError("Simulated disk write failure")

    monkeypatch.setattr(shutil, "copyfileobj", mock_copyfileobj)

    worker.add_task("move", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(errors) == 1
    assert len(finished) == 0
    assert src_file.exists()
    assert src_file.read_text() == "important source data"
    assert len(list(dst_dir.glob("*"))) == 0


def test_undo_copy_replacement_protection(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "test.jpg"
    src_file.write_text("original copy content")

    undo_tokens = []
    worker.signals.undo_record.connect(lambda token: undo_tokens.append(token))

    worker.add_task("copy", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(undo_tokens) == 1
    token = undo_tokens[0]
    copied_path = token["current"]

    # Tamper with copied file to simulate replacement with different content
    with open(copied_path, "w") as f:
        f.write("replaced content!")

    errors = []
    worker.signals.error.connect(lambda path, msg: errors.append((path, msg)))

    worker.add_task("undo_copy", copied_path, token)
    worker.stop()
    QCoreApplication.processEvents()

    assert len(errors) == 1
    assert os.path.exists(copied_path)
    assert open(copied_path).read() == "replaced content!"


def test_undo_move_restore_collision_protection(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "test.jpg"
    src_file.write_text("moved file content")

    undo_tokens = []
    worker.signals.undo_record.connect(lambda token: undo_tokens.append(token))

    worker.add_task("move", str(src_file), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(undo_tokens) == 1
    token = undo_tokens[0]

    # Create a replacement file at original location before undo
    src_file.write_text("unrelated file at original location")

    errors = []
    worker.signals.error.connect(lambda path, msg: errors.append((path, msg)))

    worker.add_task("undo_move", token["current"], token)
    worker.stop()
    QCoreApplication.processEvents()

    assert len(errors) == 1
    assert os.path.exists(token["current"])
    assert src_file.read_text() == "unrelated file at original location"


def test_custom_trash_collision_safety(qtbot, tmp_path):
    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()

    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    sm.set("directories", "trash", str(trash_dir))

    worker = QueueWorker(sm)

    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Pre-create file in custom trash
    trash_file = trash_dir / "photo.jpg"
    trash_file.write_text("already in trash")

    file_to_trash = src_dir / "photo.jpg"
    file_to_trash.write_text("to be trashed")

    finished = []
    worker.signals.finished.connect(lambda path: finished.append(path))

    worker.add_task("trash", str(file_to_trash))
    worker.stop()
    QCoreApplication.processEvents()

    assert len(finished) == 1
    assert finished[0] == str(trash_dir / "photo_1.jpg")
    assert not file_to_trash.exists()
    assert trash_file.read_text() == "already in trash"
    assert (trash_dir / "photo_1.jpg").read_text() == "to be trashed"
