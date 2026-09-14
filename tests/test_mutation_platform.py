"""Unsupported native mutations fail before admission or filesystem changes."""
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QTimer

from imagesorter import platform_capabilities
from imagesorter.metadata_io import write_metadata
from imagesorter.mutation_client import MutationClient
from imagesorter.operation_engine import OperationEngine
from imagesorter.operation_journal import OperationJournal
from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager


@pytest.fixture
def unsupported(monkeypatch):
    monkeypatch.setattr(platform_capabilities, 'sys', SimpleNamespace(platform='win32'))
    return platform_capabilities.mutation_unavailable_reason()


def test_unsupported_client_dispatches_gui_without_socket_or_pending_work(qtbot, tmp_path, unsupported):
    client = MutationClient(journal_path=str(tmp_path / 'journal.db'))
    assert client.unavailable_reason == unsupported
    assert not client._timer.isActive()
    ticks = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    for index in range(200):
        with pytest.raises(RuntimeError, match='unavailable on Windows'):
            client.submit({'operation_id': str(index)})
    client.poll()
    qtbot.waitUntil(lambda: bool(ticks), timeout=3000)
    assert not client.pending
    assert client.connection is None and client.process is None
    assert not (tmp_path / 'journal.db').exists()
    client.disconnect()


def test_unsupported_queue_rejects_primary_undo_and_recovery_before_admission(qtbot, tmp_path, unsupported):
    worker = QueueWorker(SettingsManager(filepath=str(tmp_path / 'settings.json')))
    for action in ('move', 'copy', 'trash', 'undo_move', 'undo_copy'):
        with pytest.raises(RuntimeError, match='unavailable on Windows'):
            worker.add_task(action, str(tmp_path / 'image.jpg'), str(tmp_path / 'target'))
    with pytest.raises(RuntimeError, match='unavailable on Windows'):
        worker.add_recovery_task({'operation_id': 'old', 'source_path': str(tmp_path / 'image.jpg')})
    assert not worker.client.pending and not worker._requests
    assert worker.client.process is None
    worker.shutdown()


def test_unsupported_engine_returns_existing_failure_contract_without_file_work(tmp_path, unsupported):
    image = tmp_path / 'image.jpg'
    image.write_bytes(b'original image')
    sidecar = tmp_path / 'image.jpg.txt'
    sidecar.write_bytes(b'original notes')
    destination = tmp_path / 'destination'
    destination.mkdir()
    journal = OperationJournal(str(tmp_path / 'journal.db'))
    engine = OperationEngine(journal)
    result = engine.execute({'operation_id': 'unsupported', 'action': 'move',
                             'source_path': str(image), 'destination_path': str(destination),
                             'task_options': {'view_generation': 17}})
    assert result['schema_version'] == 1 and result['state'] == 'failed'
    assert result['view_generation'] == 17 and result['error'] == unsupported
    assert result['undo_token'] is None and result['destination_path'] is None
    assert journal.get_entry('unsupported') is None
    assert image.read_bytes() == b'original image' and sidecar.read_bytes() == b'original notes'
    assert not list(destination.iterdir())


@pytest.mark.parametrize('existing_sidecar', [False, True])
def test_unsupported_metadata_preserves_exact_original_members(tmp_path, unsupported, existing_sidecar):
    image = tmp_path / 'image.jpg'
    image.write_bytes(b'original image')
    sidecar = tmp_path / 'image.jpg.txt'
    if existing_sidecar:
        sidecar.write_bytes(b'original notes')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError, match='unavailable on Windows'):
        write_metadata(str(image), ['new tag'], write_exif=True, write_sidecar=True)
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_missing_unix_sockets_is_an_explicit_capability_failure(monkeypatch):
    monkeypatch.setattr(platform_capabilities, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(platform_capabilities, 'socket', SimpleNamespace())
    with pytest.raises(RuntimeError, match='Unix sockets'):
        platform_capabilities.require_mutation_support()


@pytest.fixture(params=["native", "windows-directory-mode"])
def component_directory_mode(request, monkeypatch):
    from imagesorter import component_manager, image_identity

    image_identity._identity_manager.cache_clear()
    if request.param == "windows-directory-mode":
        # Simulate only the directory permission evidence exposed by Windows.
        # Keep the real manager, Qt, reader process, source stat, and bytes.
        mode_api = SimpleNamespace(**{name: getattr(stat, name) for name in dir(stat)})
        mode_api.S_IMODE = lambda mode: 0o777 if stat.S_ISDIR(mode) else stat.S_IMODE(mode)
        monkeypatch.setattr(component_manager, "stat", mode_api)
        from imagesorter.paths import get_components_dir
        with pytest.raises(component_manager.ComponentError, match="private"):
            component_manager.ComponentManager(root=get_components_dir())
    yield request.param
    image_identity._identity_manager.cache_clear()


def _wait_for_review_image(qtbot, viewer, records, index):
    try:
        qtbot.waitUntil(lambda: viewer.current_index == index and viewer.viewer.isVisible()
                       and not viewer.viewer.original_pixmap.isNull(), timeout=10000)
    except Exception as exc:
        from imagesorter.image_identity import cache_key
        path = viewer.images[index] if index < len(viewer.images) else None
        try:
            identity = repr(cache_key(path)) if path else "No current image"
        except Exception as identity_error:
            identity = f"{type(identity_error).__name__}: {identity_error}"
        diagnostic = {"current_index": viewer.current_index, "expected_index": index,
                      "status": viewer.statusBar().currentMessage(), "empty_label": viewer.empty_label.text(),
                      "cache_identity": identity, "recent_reader_results": records[-4:],
                      "decode_contexts": {key: repr(value) for key, value in viewer._decode_contexts.items()}}
        pytest.fail(f"Image was not presented: {json.dumps(diagnostic, default=str)}; wait error: {exc}")


def _set_distinct_fixture_timestamps(path):
    """Make Windows creation/change semantics distinct without sleeping."""
    birth_ns, modified_ns = 946684800000000000, 978307200000000000
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.SetFileTime.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME))
        kernel.SetFileTime.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        # FILE_WRITE_ATTRIBUTES, share read/write/delete, OPEN_EXISTING. This
        # handle changes only our generated fixture before its before snapshot.
        handle = kernel.CreateFileW(str(path), 0x100, 7, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ticks = birth_ns // 100 + 116444736000000000
            creation = wintypes.FILETIME(ticks & 0xffffffff, ticks >> 32)
            if not kernel.SetFileTime(handle, ctypes.byref(creation), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if not kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
    os.utime(path, ns=(modified_ns, modified_ns))
    info = path.stat()
    assert info.st_mtime_ns == modified_ns
    if os.name == "nt":
        assert getattr(info, "st_birthtime_ns", info.st_ctime_ns) == birth_ns
        assert birth_ns != info.st_mtime_ns


def test_actual_png_reader_preserves_binary_pixels_and_source_identity(tmp_path, qapp):
    from PIL import Image

    from imagesorter.image_identity import cache_key, matches_metadata
    from imagesorter.reader_process import run_reader

    source = tmp_path / "binary-pixels.png"
    # CR, LF, and DOS EOF values expose text-mode translation in either the
    # original snapshot or returned RGBA descriptor, on an actual native run.
    expected = bytes((13, 10, 26, 255, 26, 13, 10, 255, 10, 26, 13, 255,
                      0, 255, 13, 255, 26, 0, 10, 255, 255, 26, 0, 255))
    Image.frombytes("RGBA", (3, 2), expected).save(source)
    _set_distinct_fixture_timestamps(source)
    before = source.read_bytes(), source.stat().st_mode
    metadata, pixels = run_reader({"action": "decode", "request_id": "native-binary-pixels", "filepath": str(source)})
    assert pixels == expected
    assert (metadata["width"], metadata["height"], metadata["stride"]) == (3, 2, 12)
    assert metadata["input_sha256"] == hashlib.sha256(before[0]).hexdigest()
    assert metadata["decoder_identity"] == ["core.qt", "base"]
    assert matches_metadata(cache_key(source), metadata), metadata
    assert (source.read_bytes(), source.stat().st_mode) == before


def test_actual_image_review_and_gui_refusals_preserve_files_and_undo(
        qtbot, tmp_path, unsupported, component_directory_mode):
    from PIL import Image
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from imagesorter.ui_main import MainViewer

    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    for index, color in enumerate(('red', 'blue')):
        Image.new('RGB', (96, 64), color).save(source / f'{index}.png')
        _set_distinct_fixture_timestamps(source / f'{index}.png')
    before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in source.iterdir()}
    settings = SettingsManager(filepath=str(tmp_path / 'settings.json'))
    settings.set('directories', 'source', str(source))
    settings.set('hotkeys', 'M', {'action': 'move', 'folder': str(target), 'auto_advance': True})
    viewer = MainViewer(settings)
    qtbot.addWidget(viewer)
    records = []
    viewer.loader.image_ready.connect(lambda result: records.append(
        {key: value for key, value in result.items() if key != "image"}))
    viewer.show()
    QApplication.setActiveWindow(viewer)
    _wait_for_review_image(qtbot, viewer, records, 0)
    assert viewer.viewer.original_pixmap.width() == 96
    viewer.viewer.setFocus()
    qtbot.keyClick(viewer, Qt.Key.Key_M)
    assert 'unavailable' in viewer.statusBar().currentMessage()
    token = {'token_id': 'historical-unsupported', 'action': 'move',
             'current': str(target / 'old.png'), 'original': str(source / 'old.png'), 'revision': 1}
    viewer.history.append(token)
    qtbot.keyClick(viewer, Qt.Key.Key_Z, modifier=Qt.KeyboardModifier.ControlModifier)
    assert viewer.history == [token]
    assert not viewer.pending_ops and not viewer.worker.client.pending
    assert not viewer.worker._requests and viewer.worker.client.process is None
    assert {p.name: (p.read_bytes(), p.stat().st_mode) for p in source.iterdir()} == before
    assert not list(target.iterdir())
    qtbot.keyClick(viewer, Qt.Key.Key_Right)
    _wait_for_review_image(qtbot, viewer, records, 1)
    assert '1.png' in viewer.windowTitle()


def test_unsupported_retention_cleanup_precedes_journal_read_and_preserves_files(tmp_path, monkeypatch):
    import time
    from unittest.mock import Mock

    source = tmp_path / "image.jpg"
    source.write_bytes(b"retained original image")
    (tmp_path / "image.jpg.txt").write_bytes(b"retained original companion")
    destination = tmp_path / "destination"
    destination.mkdir()
    journal = OperationJournal(str(tmp_path / "journal.db"))
    engine = OperationEngine(journal)
    operation_id = "retained-before-platform-change"
    if platform_capabilities.mutation_unavailable_reason() is None:
        # A real supported-host transaction supplies its authoritative retention
        # manifest; only then switch the capability boundary to unsupported.
        result = engine.execute({"operation_id": operation_id, "action": "move",
                                 "source_path": str(source), "destination_path": str(destination)})
        assert result["state"] == "completed"
        entry = journal.get_entry(operation_id)
        assert all(Path(item["claim"]).exists() for item in entry["manifest"]["files"])
    else:
        # Native Windows must not manufacture a supported mutation. Preserve
        # representative retained bytes and prove rejection precedes any lookup
        # that could authorize them as cleanup material.
        retained = tmp_path / "retained"
        retained.mkdir()
        (retained / "image.jpg").write_bytes(source.read_bytes())
        (retained / "image.jpg.txt").write_bytes(b"retained original companion")
    before = {str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mode)
              for p in tmp_path.rglob("*") if p.is_file()}
    lookup = Mock(side_effect=AssertionError("Platform refusal must precede journal authority lookup"))
    monkeypatch.setattr(journal, "get_entry", lookup)
    monkeypatch.setattr(platform_capabilities, "sys", SimpleNamespace(platform="win32"))
    with pytest.raises(RuntimeError, match="unavailable on Windows"):
        engine.cleanup_retained(operation_id, now=time.time() + 31 * 86400)
    lookup.assert_not_called()
    assert {str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mode)
            for p in tmp_path.rglob("*") if p.is_file()} == before


def test_recovery_dialog_refusal_preserves_records_and_dispatches_without_exception(
        qtbot, tmp_path, unsupported):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from imagesorter.ui_main import MainViewer
    from imagesorter.ui_recovery import RecoveryDialog

    collection = tmp_path / "collection"
    collection.mkdir()
    original = collection / "original.jpg"
    original.write_bytes(b"preserved original")
    companion = collection / "original.jpg.txt"
    companion.write_bytes(b"preserved companion")
    before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in collection.iterdir()}
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[])
    qtbot.addWidget(viewer)
    record = {"operation_id": "retained-record", "state": "recovery_required", "action": "move",
              "source_path": str(original), "manifest": {"version": 2, "mode": "move", "files": [
                  {"role": "image", "source": str(original), "claim": str(original)},
                  {"role": "sidecar", "source": str(companion), "claim": str(companion)}]}}
    viewer._recovery_records = [record]
    viewer._recovery_generations = {"earlier-request": 7}
    historical_token = {"token_id": "preserve-history"}
    viewer.history.append(historical_token)
    dialog = RecoveryDialog(viewer)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.rollback.isEnabled()
    confirmations = []

    def approve_recorded_rollback():
        confirmation = QApplication.activeModalWidget()
        assert isinstance(confirmation, QMessageBox)
        confirmations.append(confirmation.text())
        qtbot.mouseClick(confirmation.button(QMessageBox.StandardButton.Yes), Qt.MouseButton.LeftButton)

    # Enter through the real RecoveryDialog button and actual confirmation
    # modal, not a direct handler call or a mocked worker admission method.
    QTimer.singleShot(0, approve_recorded_rollback)
    qtbot.mouseClick(dialog.rollback, Qt.MouseButton.LeftButton)
    assert len(confirmations) == 1 and str(original) in confirmations[0]
    assert "Recovery was not queued" in viewer.statusBar().currentMessage()
    assert unsupported in viewer.statusBar().currentMessage()
    assert viewer._recovery_records == [record] and dialog.rows == [record]
    assert viewer._recovery_generations == {"earlier-request": 7}
    assert viewer.history == [historical_token]
    assert dialog.rollback.isEnabled()
    assert not viewer.pending_ops and not viewer.worker._requests and not viewer.worker.client.pending
    assert viewer.worker.client.process is None
    ticks = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    qtbot.waitUntil(lambda: bool(ticks), timeout=3000)
    assert {p.name: (p.read_bytes(), p.stat().st_mode) for p in collection.iterdir()} == before


def test_unsupported_service_entry_refuses_before_runtime_pin_or_profile_artifacts(
        tmp_path, monkeypatch, unsupported):
    from unittest.mock import Mock

    from imagesorter import mutation_service

    pin = Mock(side_effect=AssertionError("Unsupported service must not pin or start a runtime"))
    monkeypatch.setattr(mutation_service, "_pin_runtime", pin)
    journal = tmp_path / "uncreated-service-profile" / "journal.db"
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    with pytest.raises(RuntimeError, match="unavailable on Windows") as error:
        mutation_service.main(["--journal", str(journal)])
    assert str(error.value) == unsupported
    pin.assert_not_called()
    assert not journal.parent.exists()
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before
