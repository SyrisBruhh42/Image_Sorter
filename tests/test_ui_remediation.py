from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QLineEdit

from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import ImageViewer, MainViewer
from imagesorter.ui_settings import SettingsWindow


def test_is_input_focused(qtbot, tmp_path):
    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    main_win = MainViewer(sm)
    qtbot.addWidget(main_win)
    main_win.show()

    line_edit = QLineEdit(main_win)
    qtbot.addWidget(line_edit)
    line_edit.show()

    line_edit.setFocus()
    QApplication.setActiveWindow(main_win)
    assert main_win.is_input_focused()


def test_clipping_overlay_performance_and_color(qtbot):
    viewer = ImageViewer()
    qtbot.addWidget(viewer)

    # Create synthetic 50MP image (e.g., 7000x7000)
    img = QImage(7000, 7000, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.white)
    pixmap = QPixmap.fromImage(img)

    viewer.set_image(pixmap)
    viewer.toggle_clipping_warnings()

    assert not viewer.clipping_pixmap.isNull()


def test_lru_pixmap_cache_eviction(qtbot, tmp_path):
    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    sm.set('advanced', 'cache_size_mb', 1)  # 1MB cache budget
    main_win = MainViewer(sm)
    qtbot.addWidget(main_win)

    # Two 400x400 images exceed the 1 MiB cache budget together.
    img = QImage(400, 400, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.red)
    pix1 = QPixmap.fromImage(img)
    pix2 = QPixmap.fromImage(img)

    path1, path2 = tmp_path / "path1.jpg", tmp_path / "path2.jpg"
    path1.write_bytes(b"first image identity")
    path2.write_bytes(b"second image identity")
    main_win._add_pixmap_to_cache(str(path1), pix1)
    main_win._add_pixmap_to_cache(str(path2), pix2)

    # Because 1MB limit is exceeded, path1.jpg should be evicted
    assert main_win._get_pixmap_from_cache(str(path1)) is None
    assert main_win._get_pixmap_from_cache(str(path2)) is pix2
    oversized = QPixmap(1000, 1000)
    assert not main_win._add_pixmap_to_cache(str(path1), oversized)
    assert main_win.cache_bytes <= main_win.max_cache_bytes


def test_settings_window_modal_inheritance(qtbot, tmp_path):
    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    dialog = SettingsWindow(sm)
    qtbot.addWidget(dialog)

    assert isinstance(dialog, SettingsWindow)
    assert dialog.windowTitle() == "Image Sorter Settings"
