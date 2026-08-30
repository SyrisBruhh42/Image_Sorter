from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLineEdit, QApplication
from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer, ImageViewer
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

    # 1000x1000 image is ~4MB in ARGB32
    img = QImage(1000, 1000, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.red)
    pix1 = QPixmap.fromImage(img)
    pix2 = QPixmap.fromImage(img)

    main_win._add_pixmap_to_cache("path1.jpg", pix1)
    main_win._add_pixmap_to_cache("path2.jpg", pix2)

    # Because 1MB limit is exceeded, path1.jpg should be evicted
    assert "path1.jpg" not in main_win.pixmap_cache
    assert "path2.jpg" in main_win.pixmap_cache


def test_settings_window_modal_inheritance(qtbot, tmp_path):
    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))
    dialog = SettingsWindow(sm)
    qtbot.addWidget(dialog)

    assert isinstance(dialog, SettingsWindow)
    assert dialog.windowTitle() == "Image Sorter Settings"
