import os

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtGui import QPixmap

from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer


def create_dummy_image(filepath: str) -> None:
    p = QPixmap(20, 20)
    p.fill(Qt.GlobalColor.blue)
    p.save(filepath, "PNG")


def test_successful_move_removes_image_once(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    img2 = str(src_dir / "img2.png")
    create_dummy_image(img1)
    create_dummy_image(img2)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    assert len(viewer.images) == 2
    assert viewer.current_index == 0
    assert viewer.images[0] == img1

    # Trigger move for img1
    op_id = viewer.trigger_file_action("move", img1, str(dst_dir))
    assert op_id is not None
    # UI advances immediately to non-pending image img2
    assert viewer.current_index == 1 or viewer.images[viewer.current_index] == img2
    assert img1 in viewer.images  # Still in list while pending

    # Stop worker to wait for background execution
    viewer.worker.stop()
    QCoreApplication.processEvents()

    assert img1 not in viewer.images
    assert len(viewer.images) == 1
    assert viewer.images[0] == img2
    assert viewer.current_index == 0


def test_failed_move_restores_image_to_queue(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "non_existent_dst"
    src_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    img2 = str(src_dir / "img2.png")
    create_dummy_image(img1)
    create_dummy_image(img2)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    assert len(viewer.images) == 2

    # Trigger move to invalid destination folder which will fail
    viewer.trigger_file_action("move", img1, str(dst_dir))
    viewer.worker.stop()
    QCoreApplication.processEvents()

    # Image should be reinserted at original position without duplicates
    assert len(viewer.images) == 2
    assert viewer.images[0] == img1
    assert viewer.images[1] == img2


def test_failed_trash_restores_image_to_queue(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    create_dummy_image(img1)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))
    # Configure invalid trash directory
    sm.set("directories", "trash", str(tmp_path / "non_existent_trash_dir"))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    # Trigger trash operation on non-existent source file (e.g. removed right before op)
    viewer.trigger_file_action("trash", img1, None)
    os.remove(img1)

    viewer.worker.stop()
    QCoreApplication.processEvents()

    # Worker error will occur because file is missing
    # Image path should remain restored in queue
    assert img1 in viewer.images


def test_successful_undo_restores_image_at_recorded_position(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    img2 = str(src_dir / "img2.png")
    create_dummy_image(img1)
    create_dummy_image(img2)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    # Perform move of img1
    viewer.trigger_file_action("move", img1, str(dst_dir))
    viewer.worker.stop()
    QCoreApplication.processEvents()

    assert img1 not in viewer.images
    assert len(viewer.history) == 1

    # Now perform undo
    viewer.undo_last_action()
    viewer.worker.stop()
    QCoreApplication.processEvents()

    assert len(viewer.images) == 2
    assert viewer.images[0] == img1
    assert viewer.images[1] == img2
    assert viewer.images.count(img1) == 1  # Exactly once


def test_failed_undo_keeps_undo_record_available(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    create_dummy_image(img1)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    # Perform move
    viewer.trigger_file_action("move", img1, str(dst_dir))
    viewer.worker.stop()
    QCoreApplication.processEvents()

    assert len(viewer.history) == 1
    token = viewer.history[0]
    moved_path = token["current"]

    # Delete moved destination file so undo move fails
    os.remove(moved_path)

    # Trigger undo
    viewer.undo_last_action()
    viewer.worker.stop()
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=3000)

    # History record must be restored to history for retry
    assert len(viewer.history) == 1
    assert viewer.history[0]["token_id"] == token["token_id"]


def test_system_trash_messaging(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    create_dummy_image(img1)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))
    # Unset custom trash folder to use system send2trash
    sm.set("directories", "trash", "")

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    viewer.trigger_file_action("trash", img1, None)

    # Status message should clearly warn that Image Sorter Undo is unavailable for system trash
    status_text = viewer.statusBar().currentMessage()
    assert "Moved to system trash" in status_text
    assert "Undo is unavailable" in status_text


def test_cache_bytes_reset_on_reload_and_clear(qtbot, tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    create_dummy_image(img1)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    # Load image into cache
    viewer.show_image()
    qtbot.waitUntil(lambda: viewer.cache_bytes > 0, timeout=3000)
    assert viewer.cache_bytes > 0
    assert len(viewer.pixmap_cache) > 0

    # Clear cache explicitly
    viewer.clear_pixmap_cache()
    assert viewer.cache_bytes == 0
    assert len(viewer.pixmap_cache) == 0

    # Load image again to populate cache
    viewer.show_image()
    qtbot.waitUntil(lambda: viewer.cache_bytes > 0, timeout=3000)
    assert viewer.cache_bytes > 0

    # Reload directory
    viewer.load_images()
    qtbot.waitUntil(lambda: viewer.cache_bytes > 0, timeout=3000)
    assert viewer.cache_bytes > 0
    viewer.clear_pixmap_cache()
    assert viewer.cache_bytes == 0


def test_operation_result_is_idempotent(qtbot, tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    create_dummy_image(img1)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)
    monkeypatch.setattr(
        viewer.worker,
        "add_task",
        lambda *args, **kwargs: kwargs["operation_id"],
    )

    op_id = viewer.trigger_file_action("move", img1, str(dst_dir))
    pending_op = viewer.pending_ops[op_id]

    undo_token = {
        "version": 1,
        "token_id": "test-uuid-1",
        "action": "move",
        "original": img1,
        "current": str(dst_dir / "img1.png"),
        "timestamp": 12345.0
    }
    result = {
        "schema_version": 1,
        "operation_id": op_id,
        "action": "move",
        "source_path": img1,
        "destination_path": str(dst_dir / "img1.png"),
        "state": "completed",
        "undo_token": undo_token,
        "error": None,
        "warning": None,
    }
    viewer.on_operation_result(result)
    assert pending_op.state == "finished"
    assert len(viewer.history) == 1

    viewer.on_operation_result(result)
    assert img1 not in viewer.images
    assert len(viewer.images) == 0
    assert len(viewer.history) == 1


def test_navigation_during_pending_operations(qtbot, tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    img1 = str(src_dir / "img1.png")
    img2 = str(src_dir / "img2.png")
    img3 = str(src_dir / "img3.png")
    create_dummy_image(img1)
    create_dummy_image(img2)
    create_dummy_image(img3)

    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)
    monkeypatch.setattr(
        viewer.worker,
        "add_task",
        lambda *args, **kwargs: kwargs["operation_id"],
    )

    # Current index is 0 (img1)
    assert viewer.current_index == 0

    # Action on img1 -> pending op created, advances UI to index 1 (img2)
    op_id = viewer.trigger_file_action("move", img1, str(dst_dir))
    assert viewer.current_index == 1
    assert viewer.images[viewer.current_index] == img2

    # User manually navigates to img3 while img1 op is still pending in background
    viewer.current_index = 2
    assert viewer.images[viewer.current_index] == img3

    # Now finish img1 operation
    viewer.on_operation_result(
        {
            "schema_version": 1,
            "operation_id": op_id,
            "action": "move",
            "source_path": img1,
            "destination_path": str(dst_dir / "img1.png"),
            "state": "completed",
            "undo_token": None,
            "error": None,
            "warning": None,
        }
    )

    # img1 removed from list
    assert img1 not in viewer.images
    assert len(viewer.images) == 2
    # Current visible image should remain img3
    assert viewer.images[viewer.current_index] == img3
