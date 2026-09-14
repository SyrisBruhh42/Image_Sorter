"""Real Qt dispatch and mutation-service regressions for the source integration.

Only result-order tests inject protocol replies; file-safety cases use the actual
GUI, worker facade, IPC service, journal and temporary image/sidecar files.
"""
from __future__ import annotations

import copy
import os
import uuid
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from imagesorter.model_assets import LABELS_SHA256, MODEL_SHA256
from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer, PendingOp


@pytest.fixture
def review(qtbot, tmp_path):
    source, target, other = (tmp_path / name for name in ('source', 'target', 'other'))
    for folder in (source, target, other):
        folder.mkdir()
    for index, color in enumerate(('red', 'green', 'blue')):
        Image.new('RGB', (96, 64), color).save(source / f'{index}.jpg')
    Image.new('RGB', (96, 64), 'yellow').save(other / 'other.jpg')
    settings = SettingsManager(filepath=str(tmp_path / 'settings.json'))
    settings.set('directories', 'source', str(source))
    settings.set('directories', 'trash', str(target))
    settings.set('hotkeys', 'M', {'action': 'move', 'folder': str(target), 'auto_advance': False})
    settings.set('hotkeys', 'K', {'action': 'copy', 'folder': str(target), 'auto_advance': False})
    settings.set('hotkeys', 'J', {'action': 'move', 'folder': str(target), 'auto_advance': True})
    viewer = MainViewer(settings)
    qtbot.addWidget(viewer)
    viewer.show()
    QApplication.setActiveWindow(viewer)
    viewer.viewer.setFocus()
    qtbot.waitUntil(lambda: viewer.viewer.isVisible() and not viewer.viewer.original_pixmap.isNull(), timeout=10000)
    return viewer, source, target, other


def press(qtbot, viewer, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QApplication.setActiveWindow(viewer)
    viewer.viewer.setFocus()
    qtbot.keyClick(viewer, key, modifier=modifiers)


def settled(qtbot, viewer):
    qtbot.waitUntil(lambda: not viewer.worker.client.pending and
                   all(op.state != 'pending' for op in viewer.pending_ops.values()), timeout=10000)


@pytest.mark.parametrize('replacement', ['different_bytes', 'same_bytes_new_inode', 'sidecar'])
def test_gui_undo_copy_preserves_replaced_member(qtbot, review, replacement):
    viewer, source, target, _other = review
    original = source / '0.jpg'
    sidecar = Path(str(original) + '.txt')
    sidecar.write_bytes(b'original private note\n')
    press(qtbot, viewer, Qt.Key.Key_K)
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
    token_id = viewer.history[-1]['token_id']
    current = target / '0.jpg'
    changed = Path(str(current) + '.txt') if replacement == 'sidecar' else current
    wanted = changed.read_bytes() if replacement == 'same_bytes_new_inode' else b'valuable external replacement'
    substitution = target / 'replacement'
    substitution.write_bytes(wanted)
    os.replace(substitution, changed)
    press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, viewer)
    assert changed.read_bytes() == wanted
    assert current.exists() and original.exists()
    assert len(viewer.history) == 1 and viewer.history[-1]['token_id'] == token_id
    assert any(op.action == 'undo_copy' and op.state == 'error' for op in viewer.pending_ops.values())


@pytest.mark.parametrize('action,key', [('move', Qt.Key.Key_J), ('copy', Qt.Key.Key_K), ('trash', Qt.Key.Key_Delete)])
def test_gui_file_set_round_trip_and_duplicate_results(qtbot, review, action, key):
    viewer, source, target, _other = review
    original = source / '0.jpg'
    image_bytes = original.read_bytes()
    sidecar = Path(str(original) + '.txt')
    sidecar.write_bytes(b'human note\nwith formatting')
    replies = []
    viewer.worker.signals.operation_result.connect(replies.append)
    press(qtbot, viewer, key)
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
    parent = next(reply for reply in replies if reply['action'] == action)
    assert parent['view_generation'] == viewer.load_generation
    current = Path(parent['destination_path'])
    assert current.read_bytes() == image_bytes
    assert Path(str(current) + '.txt').read_bytes() == b'human note\nwith formatting'
    before = list(viewer.images)
    viewer.on_operation_result(parent)
    assert viewer.images == before and len(viewer.history) == 1
    press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, viewer)
    assert original.read_bytes() == image_bytes
    assert sidecar.read_bytes() == b'human note\nwith formatting'
    assert not current.exists() and not Path(str(current) + '.txt').exists()
    assert not viewer.history
    viewer.on_operation_result(parent)
    assert not viewer.history


def test_held_move_is_read_only_and_navigates_original_neighbours(qtbot, review):
    viewer, source, target, _other = review
    press(qtbot, viewer, Qt.Key.Key_D)
    qtbot.waitUntil(lambda: viewer.viewer.isVisible() and viewer.current_index == 1, timeout=10000)
    press(qtbot, viewer, Qt.Key.Key_M)
    qtbot.waitUntil(lambda: viewer._held_move is not None and viewer._held_move.completed, timeout=10000)
    held = viewer._held_move
    assert held.pixmap.width() * held.pixmap.height() <= 4_000_000
    assert not (source / '1.jpg').exists()
    assert str(target / '1.jpg') in viewer.hud_details.text()
    assert 'Moved' in viewer.hud_details.text() and 'read-only' in viewer.hud_details.text()
    operation_count = len(viewer.pending_ops)
    for key in (Qt.Key.Key_Delete, Qt.Key.Key_M, Qt.Key.Key_K, Qt.Key.Key_J):
        press(qtbot, viewer, key)
    assert len(viewer.pending_ops) == operation_count
    assert len(list(target.glob('*.jpg'))) == 1
    press(qtbot, viewer, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == str(target / '1.jpg')
    press(qtbot, viewer, Qt.Key.Key_D)
    assert viewer._held_move is None and viewer.images[viewer.current_index] == str(source / '2.jpg')
    # Undo is still available after leaving the held display.
    press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, viewer)
    assert viewer.images[viewer.current_index] == str(source / '1.jpg')
    qtbot.waitUntil(lambda: viewer.viewer.isVisible(), timeout=10000)
    press(qtbot, viewer, Qt.Key.Key_M)
    qtbot.waitUntil(lambda: viewer._held_move is not None and viewer._held_move.completed, timeout=10000)
    press(qtbot, viewer, Qt.Key.Key_A)
    assert viewer._held_move is None and viewer.images[viewer.current_index] == str(source / '0.jpg')


def test_held_move_bounds_snapshot_and_restores_failed_source(qtbot, review):
    viewer, source, target, _other = review
    # A displayed temporary image can exceed the held-frame budget; no decoder is mocked.
    huge = QPixmap(2500, 2500)
    huge.fill(Qt.GlobalColor.red)
    viewer.viewer.set_image(huge)
    viewer.settings.set('hotkeys', 'M', {'action': 'move', 'folder': str(target / 'missing'), 'auto_advance': False})
    press(qtbot, viewer, Qt.Key.Key_M)
    assert viewer._held_move.pixmap.width() * viewer._held_move.pixmap.height() <= 4_000_000
    settled(qtbot, viewer)
    assert viewer._held_move is None
    assert (source / '0.jpg').exists()
    assert viewer.images[viewer.current_index] == str(source / '0.jpg')
    assert not viewer.history


@pytest.mark.parametrize('new_source', ['blank', 'other'])
@pytest.mark.parametrize('pending_action', ['failed_move', 'undo_move', 'copy'])
def test_old_operation_results_never_repopulate_changed_source(qtbot, review, new_source, pending_action):
    viewer, source, target, other = review
    if pending_action == 'undo_move':
        press(qtbot, viewer, Qt.Key.Key_J)
        qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
        viewer.undo_last_action()
    elif pending_action == 'failed_move':
        viewer.trigger_file_action('move', str(source / '0.jpg'), str(target / 'missing'))
    else:
        viewer.trigger_file_action('copy', str(source / '0.jpg'), str(target), auto_advance=False)
    # Switch before returning to the Qt event loop; accepted IPC work remains real.
    viewer.settings.set('directories', 'source', '' if new_source == 'blank' else str(other))
    viewer.load_images()
    expected = [] if new_source == 'blank' else [str(other / 'other.jpg')]
    settled(qtbot, viewer)
    assert viewer.images == expected
    assert viewer._held_move is None
    if expected:
        qtbot.waitUntil(lambda: viewer.viewer.isVisible(), timeout=10000)
        assert 'other.jpg' in viewer.windowTitle()
    else:
        assert viewer.current_index == -1 and not viewer.viewer.isVisible()
        count = len(viewer.pending_ops)
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_K, Qt.Key.Key_J):
            press(qtbot, viewer, key)
        assert len(viewer.pending_ops) == count
    if pending_action == 'copy':
        assert len(viewer.history) == 1


def test_held_source_change_and_undo_in_another_view(qtbot, review):
    viewer, source, _target, other = review
    press(qtbot, viewer, Qt.Key.Key_M)
    qtbot.waitUntil(lambda: viewer._held_move is not None and viewer._held_move.completed, timeout=10000)
    viewer.settings.set('directories', 'source', str(other))
    viewer.load_images()
    assert viewer._held_move is None
    press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, viewer)
    assert (source / '0.jpg').exists()
    assert viewer.images == [str(other / 'other.jpg')]


def test_real_metadata_child_failure_keeps_gui_primary_undo(qtbot, review):
    viewer, source, target, _other = review
    # Qt can display PNG bytes under .jpg; JPEG EXIF cannot safely parse them.
    original = source / '0.jpg'
    Image.new('RGB', (96, 64), 'red').save(original, format='PNG')
    original_bytes = original.read_bytes()
    viewer.load_images()
    replies = []
    viewer.worker.signals.operation_result.connect(replies.append)
    press(qtbot, viewer, Qt.Key.Key_J)
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
    parent = next(reply for reply in replies if reply['action'] == 'move')
    child_id = uuid.uuid4().hex
    # Supply a deterministic inference fixture to the actual metadata IPC service;
    # model download/inference is separate from this file-transaction regression.
    viewer.worker.client.submit({'operation_id': child_id, 'action': 'metadata',
        'source_path': parent['destination_path'], 'parent_operation_id': parent['operation_id'],
        'expected_sha256': parent['undo_token']['provenance']['sha256'], 'tags': ['fixture'],
        'component_receipt': {'model_sha256': MODEL_SHA256, 'labels_sha256': LABELS_SHA256,
            'tensor_sha256': 'a' * 64, 'provider': 'CPUExecutionProvider', 'cuda_compute_events': 0,
            'component_version': 'base', 'model_component_version': 'explicit-verified'},
        'task_options': {'view_generation': viewer.load_generation, 'settings_snapshot': viewer.settings.snapshot()}})
    qtbot.waitUntil(lambda: any(reply['operation_id'] == child_id for reply in replies), timeout=10000)
    child = next(reply for reply in replies if reply['operation_id'] == child_id)
    assert child['state'] == 'failed' and 'EXIF' in child['error']
    assert str(original) not in viewer.images and not original.exists()
    assert (target / '0.jpg').read_bytes() == original_bytes
    assert len(viewer.history) == 1
    assert 'optional metadata failed' in viewer.statusBar().currentMessage()
    press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, viewer)
    assert original.read_bytes() == original_bytes and not (target / '0.jpg').exists()


@pytest.mark.parametrize("rejection", ["queue_full", "oversized", "non_json"])
def test_undo_admission_refusal_preserves_token_for_retry(qtbot, review, monkeypatch, rejection):
    viewer, source, target, _other = review
    press(qtbot, viewer, Qt.Key.Key_J)
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
    token = copy.deepcopy(viewer.history[-1])
    with monkeypatch.context() as patch:
        if rejection == "queue_full":
            patch.setattr(viewer.worker.client, 'MAX_PENDING', 0)
        else:
            snapshot = viewer.settings.snapshot()
            snapshot['invalid_request'] = 'x' * (1024 * 1024 + 1) if rejection == 'oversized' else object()
            patch.setattr(viewer.settings, 'snapshot', lambda: snapshot)
        press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert not viewer.worker.client.pending and not viewer.worker._requests
        assert not viewer._undo_inflight
        assert viewer.history == [token]
        assert (target / '0.jpg').exists() and not (source / '0.jpg').exists()
    press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, viewer)
    assert (source / '0.jpg').exists() and not viewer.history


def test_metadata_and_recovery_revisions_never_regress_or_resurrect(qtbot, review):
    viewer, source, target, _other = review
    # Protocol ordering test: child-before-parent and a delayed older replay.
    op_id, token_id = 'ordered-primary', 'ordered-token'
    original, current = str(source / '0.jpg'), str(target / '0.jpg')
    viewer.pending_ops[op_id] = PendingOp(op_id, 'copy', original, original, 0, viewer.load_generation)
    token = {'version': 1, 'token_id': token_id, 'action': 'copy', 'original': original,
             'current': current, 'revision': 1}
    updated = dict(token, revision=2)
    def result(identifier, action, undo):
        return {'operation_id': identifier, 'action': action, 'state': 'completed', 'undo_token': undo,
                'view_generation': viewer.load_generation, 'source_path': original, 'destination_path': current}
    viewer.on_operation_result(result('ordered-child', 'metadata', updated))
    assert not viewer.history
    parent = result(op_id, 'copy', token)
    viewer.on_operation_result(parent)
    viewer.on_operation_result(parent)
    viewer.on_recovery_summary({'type': 'history', 'result': {'undo_token': token}})
    assert viewer.history == [updated]
    viewer.pending_ops['ordered-undo'] = PendingOp('ordered-undo', 'undo_copy', current, current, 0,
                                                 viewer.load_generation, undo_token=updated)
    viewer.on_operation_result(result('ordered-undo', 'undo_copy', None))
    viewer.on_operation_result(result('late-child', 'metadata', dict(token, revision=3)))
    viewer.on_recovery_summary({'type': 'history', 'result': {'undo_token': updated}})
    assert not viewer.history


def test_escape_and_custom_letters_use_actual_qt_dispatch(qtbot, review):
    viewer, source, target, _other = review
    press(qtbot, viewer, Qt.Key.Key_Z)
    assert viewer.zen_mode and viewer.isFullScreen()
    press(qtbot, viewer, Qt.Key.Key_Escape)
    assert not viewer.zen_mode and not viewer._closing
    viewer.showFullScreen()
    press(qtbot, viewer, Qt.Key.Key_Escape)
    assert not viewer.isFullScreen() and not viewer._closing
    viewer.settings.set('hotkeys', 'L', {'action': 'copy', 'folder': str(target), 'auto_advance': False})
    press(qtbot, viewer, Qt.Key.Key_L)
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
    assert (target / '0.jpg').exists() and (source / '0.jpg').exists()
    assert not viewer.locked_zoom_action.isChecked()
    press(qtbot, viewer, Qt.Key.Key_Escape)
    assert viewer._closing


@pytest.mark.parametrize('held', [False, True])
def test_next_at_end_completes_and_previous_returns_to_last_remaining(qtbot, review, held):
    viewer, source, _target, _other = review
    viewer.current_index = 2
    viewer.show_image()
    qtbot.waitUntil(lambda: viewer.viewer.isVisible(), timeout=10000)
    if held:
        press(qtbot, viewer, Qt.Key.Key_M)
        qtbot.waitUntil(lambda: viewer._held_move is not None and viewer._held_move.completed, timeout=10000)
    press(qtbot, viewer, Qt.Key.Key_D)
    assert viewer._held_move is None and viewer._view_complete
    assert not viewer.viewer.isVisible() and 'done' in viewer.empty_label.text().lower()
    count = len(viewer.pending_ops)
    press(qtbot, viewer, Qt.Key.Key_Delete)
    assert len(viewer.pending_ops) == count
    press(qtbot, viewer, Qt.Key.Key_A)
    assert not viewer._view_complete
    assert viewer.images[viewer.current_index] == str(source / ('1.jpg' if held else '2.jpg'))


def test_hud_ai_status_obeys_saved_visibility_setting(qtbot, review):
    viewer, _source, _target, _other = review
    viewer.settings.set('ai_tagger', 'enabled', True)
    viewer.settings.set('ui', 'show_tags', False)
    viewer.update_hud()
    assert 'AI Tagging Active' not in viewer.hud_details.text()
    viewer.settings.set('ui', 'show_tags', True)
    viewer.update_hud()
    assert 'AI Tagging Active' in viewer.hud_details.text()


@pytest.mark.parametrize('action,key', [('move', Qt.Key.Key_J), ('copy', Qt.Key.Key_K)])
@pytest.mark.parametrize('collision', ['image', 'sidecar', 'both'])
def test_gui_collision_keeps_file_set_names_and_unrelated_outputs(qtbot, review, action, key, collision):
    viewer, source, target, _other = review
    original = source / '0.jpg'
    original_bytes = original.read_bytes()
    Path(str(original) + '.txt').write_bytes(b'original sidecar')
    existing = {}
    if collision in ('image', 'both'):
        existing[target / '0.jpg'] = b'unrelated target image'
    if collision in ('sidecar', 'both'):
        existing[target / '0.jpg.txt'] = b'unrelated target sidecar'
    for path, payload in existing.items():
        path.write_bytes(payload)
    press(qtbot, viewer, key)
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
    token = viewer.history[-1]
    assert token['action'] == action
    assert token['current'] == str(target / '0_1.jpg')
    assert (target / '0_1.jpg').read_bytes() == original_bytes
    assert (target / '0_1.jpg.txt').read_bytes() == b'original sidecar'
    press(qtbot, viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settled(qtbot, viewer)
    assert original.read_bytes() == original_bytes
    assert Path(str(original) + '.txt').read_bytes() == b'original sidecar'
    assert not (target / '0_1.jpg').exists() and not (target / '0_1.jpg.txt').exists()
    for path, payload in existing.items():
        assert path.read_bytes() == payload


@pytest.mark.parametrize('key', [Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_Delete])
def test_gui_transfer_failure_preserves_pair_and_restores_current_view(qtbot, review, key):
    viewer, source, target, _other = review
    original = source / '0.jpg'
    original_bytes = original.read_bytes()
    sidecar = Path(str(original) + '.txt')
    sidecar.write_bytes(b'valuable source sidecar')
    # The configured directory has become a regular file. Real worker admission
    # and filesystem validation fail without monkeypatching any mutation code.
    target.rmdir()
    target.write_bytes(b'unrelated replacement of destination directory')
    press(qtbot, viewer, key)
    settled(qtbot, viewer)
    assert any(op.state == 'error' for op in viewer.pending_ops.values())
    assert original.read_bytes() == original_bytes and sidecar.read_bytes() == b'valuable source sidecar'
    assert target.read_bytes() == b'unrelated replacement of destination directory'
    assert viewer.images[viewer.current_index] == str(original)
    assert not viewer.history


def test_editor_focus_keeps_escape_and_undo_inside_editor(qtbot, review):
    from PyQt6.QtWidgets import QLineEdit
    viewer, source, target, _other = review
    press(qtbot, viewer, Qt.Key.Key_K)
    qtbot.waitUntil(lambda: len(viewer.history) == 1, timeout=10000)
    original_token = copy.deepcopy(viewer.history[-1])
    editor = QLineEdit(viewer)
    editor.show()
    editor.setFocus()
    qtbot.keyClicks(editor, 'temporary editor text')
    assert viewer.is_input_focused()
    qtbot.keyClick(editor, Qt.Key.Key_Z, modifier=Qt.KeyboardModifier.ControlModifier)
    assert editor.text() == ''
    assert viewer.history == [original_token] and (target / '0.jpg').exists()
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    assert not viewer._closing and viewer.history == [original_token]
    assert (source / '0.jpg').exists()
    # A deliberate menu command still acts even while an editor has focus.
    undo_menu_action = next(action for action in viewer.menuBar().actions()[0].menu().actions()
                            if action.text() == '&Undo Last Action')
    undo_menu_action.trigger()
    settled(qtbot, viewer)
    assert not viewer.history and not (target / '0.jpg').exists()


@pytest.mark.parametrize('invalid_value', ['x' * (1024 * 1024 + 1), object()], ids=['oversized', 'non-json'])
def test_primary_serialization_refusal_leaves_image_interactive(qtbot, review, monkeypatch, invalid_value):
    viewer, source, target, _other = review
    original_bytes = (source / '0.jpg').read_bytes()
    with monkeypatch.context() as patch:
        snapshot = viewer.settings.snapshot()
        snapshot['invalid_request'] = invalid_value
        patch.setattr(viewer.settings, 'snapshot', lambda: snapshot)
        press(qtbot, viewer, Qt.Key.Key_M)
        assert viewer._held_move is None and not viewer.pending_ops and not viewer.history
        assert not viewer.worker.client.pending and not viewer.worker._requests
        assert 'Operation was not queued' in viewer.statusBar().currentMessage()
        assert (source / '0.jpg').read_bytes() == original_bytes and not (target / '0.jpg').exists()
    press(qtbot, viewer, Qt.Key.Key_M)
    qtbot.waitUntil(lambda: viewer._held_move is not None and viewer._held_move.completed, timeout=10000)
    assert (target / '0.jpg').read_bytes() == original_bytes
