import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLineEdit, QMessageBox, QPushButton

from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer
from imagesorter.ui_recovery import RecoveryDialog, eligible, explanation
from imagesorter.ui_settings import SettingsWindow


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


def test_recovery_dialog_accessibility_and_escape_dismissal(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    viewer = MainViewer(sm, initial_paths=[])
    qtbot.addWidget(viewer)
    dialog = RecoveryDialog(viewer)
    qtbot.addWidget(dialog)
    dialog.show()

    # Accessibility names and descriptions
    assert dialog.accessibleName() == "Preserved operation recovery dialog"
    assert "Dialog for reviewing interrupted" in dialog.accessibleDescription()
    assert dialog.status.accessibleName() == "Recovery status label"
    assert dialog.technical.accessibleName() == "Show technical record checkbox"
    assert dialog.technical.toolTip() == "Show raw JSON journal record for advanced debugging."
    assert dialog.rollback.accessibleName() == "Review and request rollback button"
    assert dialog.rollback.toolTip() == "Review selected record details and request automatic transaction rollback."

    # Escape key dismissal
    assert dialog.isVisible()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()


def test_recovery_utility_docstrings():
    assert eligible.__doc__ is not None and len(eligible.__doc__.strip()) > 0
    assert explanation.__doc__ is not None and len(explanation.__doc__.strip()) > 0


def test_settings_qol_and_accessibility(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    src_dir = tmp_path / "src_dir"
    src_dir.mkdir()
    trash_dir = tmp_path / "trash_dir"
    trash_dir.mkdir()

    window = SettingsWindow(sm)
    qtbot.addWidget(window)

    # 1. Clear button enabled on directory line edits
    assert window.src_edit.isClearButtonEnabled() is True
    assert window.trash_edit.isClearButtonEnabled() is True

    # 2. Whitespace auto-trimming on editingFinished
    window.src_edit.setText(f"   {src_dir}   ")
    window.src_edit.editingFinished.emit()
    assert window.src_edit.text() == str(src_dir)

    window.trash_edit.setText(f"   {trash_dir}   ")
    window.trash_edit.editingFinished.emit()
    assert window.trash_edit.text() == str(trash_dir)

    # 3. Hotkey row QOL and Accessibility
    window.add_hotkey_row(key="A", action="move", folder=f"   {src_dir}   ", auto_advance=True)
    folder_widget = window.hotkey_table.cellWidget(0, 2)
    folder_edit = folder_widget.layout().itemAt(0).widget()
    folder_btn = folder_widget.layout().itemAt(1).widget()

    assert folder_edit.isClearButtonEnabled() is True
    folder_edit.editingFinished.emit()
    assert folder_edit.text() == str(src_dir)

    assert folder_btn.toolTip() == "Browse target folder"
    assert folder_btn.accessibleName() == "Browse target folder for hotkey A"
    assert "Opens a file dialog" in folder_btn.accessibleDescription()

    # 4. Hotkey row fallback accessible key when key is empty
    window.add_hotkey_row(key="", action="copy", folder=str(src_dir), auto_advance=False)
    folder_widget2 = window.hotkey_table.cellWidget(1, 2)
    folder_edit2 = folder_widget2.layout().itemAt(0).widget()
    folder_btn2 = folder_widget2.layout().itemAt(1).widget()
    assert "row 2" in folder_edit2.accessibleName()
    assert "row 2" in folder_btn2.accessibleName()

    # 5. Save settings trims paths before saving
    with pytest.MonkeyPatch.context() as m:
        m.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
        window.save_settings()

    assert sm.get("directories", "source") == str(src_dir)
    assert sm.get("directories", "trash") == str(trash_dir)
    assert sm.get("hotkeys")["A"]["folder"] == str(src_dir)
