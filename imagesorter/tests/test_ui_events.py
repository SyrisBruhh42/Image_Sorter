import pytest
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QKeyEvent, QKeySequence
from src.ui_main import MainViewer
from src.settings_manager import SettingsManager

@pytest.fixture
def viewer(tmp_path):
    """Fixture providing a MainViewer instance with a temporary settings manager."""
    settings_file = tmp_path / "settings.json"
    settings = SettingsManager(filepath=str(settings_file))
    viewer = MainViewer(settings)
    return viewer

def test_ctrl_z_vs_z_keypress(qtbot, viewer, mocker):
    """Test Ctrl+Z (Undo) vs Z (Zen Mode) keypress dispatching."""
    viewer.show()
    qtbot.addWidget(viewer)

    # Spy on the target methods
    undo_spy = mocker.spy(viewer, 'undo_last_action')
    zen_spy = mocker.spy(viewer, 'toggle_zen_mode')

    # 1. Test Zen Mode (Z)
    event_z = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.NoModifier, "Z")
    viewer.keyPressEvent(event_z)

    zen_spy.assert_called_once()
    undo_spy.assert_not_called()

    zen_spy.reset_mock()
    undo_spy.reset_mock()

    # 2. Test Undo (Ctrl+Z)
    event_ctrl_z = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, "Z")
    viewer.keyPressEvent(event_ctrl_z)

    undo_spy.assert_called_once()
    zen_spy.assert_not_called()

def test_hotkey_parsing(qtbot, viewer, tmp_path, mocker):
    """Test hotkey execution correctly parses actions and destinations."""
    viewer.show()
    qtbot.addWidget(viewer)

    dest_folder = str(tmp_path / "dest")

    # Mock settings to have a hotkey
    viewer.settings.settings['hotkeys'] = {
        "M": {"folder": dest_folder, "action": "move"}
    }

    # Need images loaded to test hotkeys that act on images
    test_img = tmp_path / "test.jpg"
    test_img.write_bytes(b"dummy")
    viewer.images = [str(test_img)]
    viewer.current_index = 0

    worker_add_task_spy = mocker.spy(viewer.worker, 'add_task')

    event_m = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier, "M")
    viewer.keyPressEvent(event_m)

    worker_add_task_spy.assert_called_once_with('move', str(test_img), dest_folder)

def test_theme_switching(qtbot, viewer):
    """Test theme toggling correctly applies stylesheets."""
    viewer.show()
    qtbot.addWidget(viewer)

    initial_theme = viewer.settings.get('ui', 'theme')

    # Simulate switching theme
    if initial_theme == 'light':
        viewer.settings.set('ui', 'theme', 'Dark')
    else:
         viewer.settings.set('ui', 'theme', 'Light')

    viewer.apply_theme()

    new_theme = viewer.settings.get('ui', 'theme')
    assert new_theme != initial_theme

    # Check that stylesheet contains some expected color indicative of the theme
    style = viewer.styleSheet()

    # A crude check, but confirms apply_theme ran and updated the style string
    if new_theme == 'Dark':
        # style string may be empty if we rely on palette instead of stylesheet directly, but check if color changed
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        assert app.palette().color(viewer.backgroundRole()).name() in ["#1e1e1e", "#121212", "#353535", "#1e1e1e"]
    else:
        # QApplication.setPalette applies it globally, so we check app instance
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        assert app.palette().color(viewer.backgroundRole()).name() in ["#ffffff", "#f0f0f0", "#e0e0e0", "#efefef"]

def test_abrupt_folder_switching_mid_preload(qtbot, viewer, tmp_path, mocker):
    """Test adversarial case: Abrupt folder switching mid-preload (flushing queue)."""
    viewer.show()
    qtbot.addWidget(viewer)

    # Setup multiple directories
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    (dir1 / "test1.jpg").write_bytes(b"dummy1")

    dir2 = tmp_path / "dir2"
    dir2.mkdir()
    (dir2 / "test2.jpg").write_bytes(b"dummy2")

    # 1. Load first folder
    viewer.settings.set('directories', 'source', str(dir1))
    viewer.load_images()

    assert len(viewer.images) == 1

    # Simulate an abrupt change before preload fully processes by immediately loading dir2
    viewer.settings.set('directories', 'source', str(dir2))
    viewer.load_images()

    assert len(viewer.images) == 1
    assert viewer.images[0].endswith("test2.jpg")
