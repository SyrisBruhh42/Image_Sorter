from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtCore import Qt

from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer


def calculate_relative_luminance(color: QColor) -> float:
    """Calculates WCAG 2.1 relative luminance for a QColor."""
    def adjust(channel: int) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r = adjust(color.red())
    g = adjust(color.green())
    b = adjust(color.blue())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def calculate_contrast_ratio(c1: QColor, c2: QColor) -> float:
    """Calculates WCAG 2.1 contrast ratio between two colors."""
    l1 = calculate_relative_luminance(c1)
    l2 = calculate_relative_luminance(c2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_wcag_aaa_color_contrast(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    # Test High Contrast Theme (WCAG AAA >= 7.0:1 requirement)
    sm.set("ui", "theme", "High Contrast")
    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    palette = viewer.palette()
    bg_color = palette.color(QPalette.ColorRole.Window)
    text_color = palette.color(QPalette.ColorRole.WindowText)

    contrast_ratio = calculate_contrast_ratio(bg_color, text_color)
    assert contrast_ratio >= 7.0, f"High Contrast theme ratio {contrast_ratio:.2f} fails WCAG AAA threshold of 7.0:1"


def test_keyboard_focus_isolation(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    # Setup source directory with mock images
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    img1 = src_dir / "img1.png"
    img2 = src_dir / "img2.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xafA\x0c\x00\x00\x00\x00IEND\xaeB`\x82")
    img2.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xafA\x0c\x00\x00\x00\x00IEND\xaeB`\x82")

    sm.set("directories", "source", str(src_dir))

    viewer = MainViewer(sm)
    qtbot.addWidget(viewer)

    assert viewer.current_index == 0

    # Create a QLineEdit input child widget and set focus to simulate typing
    line_edit = QLineEdit(viewer)
    qtbot.addWidget(line_edit)
    viewer.show()
    line_edit.setFocus()

    # Simulate keypresses for 'S', 'R', 'C', 'L', 'Z', and Delete
    qtbot.keyClick(line_edit, Qt.Key.Key_S)
    qtbot.keyClick(line_edit, Qt.Key.Key_R)
    qtbot.keyClick(line_edit, Qt.Key.Key_C)
    qtbot.keyClick(line_edit, Qt.Key.Key_L)
    qtbot.keyClick(line_edit, Qt.Key.Key_Z)

    # Focus isolation must suppress image navigation/sorting/dialog triggers
    assert viewer.current_index == 0
    assert line_edit.text().upper() == "SRCLZ"
    assert len(viewer.images) == 2
