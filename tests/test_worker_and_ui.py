from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QImage
from imagesorter.settings_manager import SettingsManager
from imagesorter.queue_worker import QueueWorker
from imagesorter.image_loader import ImageLoader


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
    assert token["action"] == "move"

    # Test undo move
    worker.add_task("undo_move", token["current"], token["original"])
    worker.stop()
    QCoreApplication.processEvents()

    assert file_a.exists()
    assert not moved_file.exists()
