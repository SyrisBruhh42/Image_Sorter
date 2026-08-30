import os
import time
import pytest
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QImage, QPixmap
import errno
from src.settings_manager import SettingsManager
from src.queue_worker import QueueWorker, atomic_move, UndoToken
from src.image_loader import ImageLoader
from src.ui_main import MainViewer


def test_atomic_move_exdev(tmp_path, monkeypatch):
    src_file = tmp_path / "src.jpg"
    dst_dir = tmp_path / "dst"
    dst_file = dst_dir / "src.jpg"
    src_file.write_bytes(b"image data")

    original_replace = os.replace

    def mock_replace(src, dst):
        if "move_" not in str(src) and "src.jpg" in str(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", mock_replace)

    atomic_move(str(src_file), str(dst_file))

    assert not src_file.exists()
    assert dst_file.exists()
    assert dst_file.read_bytes() == b"image data"


def test_image_loader_generation_counter(qtbot, tmp_path):
    loader = ImageLoader()
    img_path1 = tmp_path / "img1.jpg"
    img_path2 = tmp_path / "img2.jpg"
    QImage(10, 10, QImage.Format.Format_RGB32).save(str(img_path1))
    QImage(10, 10, QImage.Format.Format_RGB32).save(str(img_path2))

    loaded = []
    loader.image_loaded.connect(lambda p, img: loaded.append(p))

    loader.add_task(str(img_path1))
    loader.clear_tasks()  # Increments generation
    loader.add_task(str(img_path2))

    loader.start()
    qtbot.waitUntil(lambda: len(loaded) == 1, timeout=3000)
    loader.stop()

    assert loaded == [str(img_path2)]


def test_queue_worker_stop_timeout(tmp_path):
    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    worker = QueueWorker(sm)

    res = worker.stop(timeout_ms=100)
    assert res is True


def test_image_loader_queue(qtbot, tmp_path):
    loader = ImageLoader()
    img_path = tmp_path / "sample.jpg"
    img = QImage(10, 10, QImage.Format.Format_RGB32)
    img.save(str(img_path))

    received = []
    loader.image_loaded.connect(lambda p, i: received.append(p))

    loader.start()
    loader.add_task(str(img_path))

    qtbot.waitUntil(lambda: len(received) == 1, timeout=3000)
    loader.stop()

    assert len(received) == 1
    assert received[0] == str(img_path)


def test_queue_worker_move_and_undo(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    file_a = src_dir / "photo.jpg"
    file_a.write_text("dummy image data")

    worker = QueueWorker(sm)

    undo_tokens = []
    worker.signals.undo_record.connect(lambda token: undo_tokens.append(token))

    # Test move
    worker.add_task("move", str(file_a), str(dst_dir))
    worker.stop()
    QCoreApplication.processEvents()

    assert not file_a.exists()
    moved_file = dst_dir / "photo.jpg"
    assert moved_file.exists()
    assert len(undo_tokens) == 1
    token = undo_tokens[0]
    assert token.action == "move"

    # Test undo move
    worker.add_task("undo_move", token.current_path, token.original_path)
    worker.stop()
    QCoreApplication.processEvents()

    assert file_a.exists()
    assert not moved_file.exists()
