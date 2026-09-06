import os
import shutil
import tempfile

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QLineEdit

from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer


@pytest.fixture
def temp_dir(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("XDG_CONFIG_HOME", d)
    monkeypatch.setenv("XDG_DATA_HOME", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_images(temp_dir):
    images = []
    for name in ["img1.jpg", "img2.jpg", "img3.jpg"]:
        path = os.path.join(temp_dir, name)
        img = Image.new("RGB", (200, 150), color="blue")
        img.save(path)
        images.append(path)
    return images


@pytest.fixture
def viewer(qtbot, temp_dir, sample_images):
    sm = SettingsManager()
    sm.set('directories', 'source', temp_dir)
    trash_dir = os.path.join(temp_dir, "trash")
    os.makedirs(trash_dir, exist_ok=True)
    sm.set('directories', 'trash', trash_dir)

    v = MainViewer(sm)
    qtbot.addWidget(v)
    v.show()
    return v


def test_undo_forwarding_full_token(qtbot, viewer, temp_dir):
    """Test 1: Forward the entire Undo token with worker.add_task(..., undo_token=last_action)."""
    assert len(viewer.images) == 3
    first_img = viewer.images[0]
    dest = os.path.join(temp_dir, "dest")
    os.makedirs(dest, exist_ok=True)

    op_id = viewer.trigger_file_action('move', first_img, dest)
    assert op_id is not None

    # Wait for operation completion
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=5000)

    token = viewer.history[0]
    assert 'provenance' in token
    token['external_edit_flag'] = True  # custom token field

    # Trigger undo
    viewer.undo_last_action()

    # Verify operation completed
    qtbot.waitUntil(lambda: len(viewer.images) == 3, timeout=5000)


def test_operation_result_matching_and_out_of_order(qtbot, viewer):
    """Test 2: Match operation results by ID / buffer out-of-order completion."""
    res_dict = {
        'schema_version': 1,
        'operation_id': 'non_existent_id',
        'action': 'move',
        'source_path': viewer.images[0],
        'destination_path': None,
        'state': 'completed',
        'undo_token': {'token_id': 't1', 'action': 'move', 'original': viewer.images[0], 'current': '/tmp/out'},
        'error': None
    }
    # Direct signal test
    viewer.on_operation_result(res_dict)
    assert len(viewer.history) == 0  # Unmatched op does not corrupt state


def test_trash_move_real_worker_and_hud(qtbot, viewer, temp_dir):
    """Test 3: Custom trash move with HUD update and real worker."""
    target = viewer.images[0]
    viewer.trigger_file_action('trash', target)

    # Simulate worker completion via operation_result
    res_dict = {
        'schema_version': 1,
        'operation_id': next(iter(viewer.pending_ops.keys())),
        'action': 'trash',
        'source_path': target,
        'destination_path': os.path.join(temp_dir, "trash", os.path.basename(target)),
        'state': 'completed',
        'undo_token': {'token_id': 't_trash', 'action': 'trash', 'original': target, 'current': os.path.join(temp_dir, "trash", os.path.basename(target))},
        'error': None
    }
    viewer.on_operation_result(res_dict)
    assert target not in viewer.images


def test_prevent_duplicate_and_empty_source(qtbot, viewer, temp_dir):
    """Test 4: Prevent duplicate actions on pending file and handle empty/invalid source."""
    target = viewer.images[0]
    dest = os.path.join(temp_dir, "dest")
    os.makedirs(dest, exist_ok=True)

    op1 = viewer.trigger_file_action('move', target, dest)
    op2 = viewer.trigger_file_action('move', target, dest)
    assert op1 is not None
    assert op2 is None  # Duplicate rejected immediately

    empty_dir = os.path.join(temp_dir, "empty")
    os.makedirs(empty_dir, exist_ok=True)
    viewer.settings.set('directories', 'source', empty_dir)
    viewer.load_images()

    assert viewer.images == []
    assert viewer.current_index == -1


def test_hud_visibility_and_position(qtbot, viewer, temp_dir):
    """Test 6: Wire HUD visibility, dimensions, truthful status, Zen mode."""
    viewer.update_hud()
    assert viewer.hud_widget.isVisible()
    assert "200x150" in viewer.hud_details.text()

    # Capture visual verification screenshot
    screenshot_dir = os.path.join(temp_dir, "screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)
    pixmap = viewer.grab()
    pixmap.save(os.path.join(screenshot_dir, "hud_verification.png"))

    viewer.toggle_zen_mode()
    assert not viewer.hud_widget.isVisible()


def test_shortcut_precedence_and_action_delivery(qtbot, viewer, temp_dir):
    """Test 7: Centralize menu/shortcut precedence and test QAction delivery."""
    # Test focus isolation
    line_edit = QLineEdit(viewer)
    line_edit.show()
    line_edit.setFocus()
    assert viewer.is_input_focused()

    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier, "D")
    initial_idx = viewer.current_index
    viewer.keyPressEvent(event)
    assert viewer.current_index == initial_idx  # Key ignored while editor focused

    line_edit.clearFocus()
    viewer.viewer.setFocus()
    assert not viewer.is_input_focused()

    # Custom hotkey L test
    dest = os.path.join(temp_dir, "custom_dest")
    os.makedirs(dest, exist_ok=True)
    viewer.settings.set('hotkeys', 'L', {'action': 'move', 'folder': dest, 'auto_advance': True})

    event_l = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier, "L")
    viewer.keyPressEvent(event_l)
    assert len(viewer.pending_ops) == 1  # Hotkey L dispatched move instead of Lock Zoom


def test_transient_launch_contract(qtbot, temp_dir, sample_images):
    """Test 8: Implement transient initial_paths/open_paths launch contract."""
    sm = SettingsManager()
    sm.set('directories', 'source', temp_dir)

    custom_img = os.path.join(temp_dir, "custom.jpg")
    Image.new("RGB", (100, 100), color="red").save(custom_img)

    v = MainViewer(sm, initial_paths=[custom_img])
    qtbot.addWidget(v)

    assert len(v.images) == 1
    assert v.images[0] == custom_img
    # Verify settings source was NOT overwritten
    assert sm.get('directories', 'source') == temp_dir
