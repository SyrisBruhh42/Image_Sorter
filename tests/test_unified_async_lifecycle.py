from __future__ import annotations

import threading
from unittest.mock import patch

from PyQt6.QtCore import QThread, QTimer

from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_settings import SettingsWindow


def test_queue_worker_does_not_load_ai_on_calling_thread(qtbot, tmp_path):
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    settings.set("ai_tagger", "enabled", True)
    gui_thread_id = threading.get_ident()
    gui_qthread = QThread.currentThread()
    committed = threading.Event()
    started = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    calls = []

    def blocked_reader(request, **_kwargs):
        calls.append((request, threading.get_ident(), QThread.currentThread(), committed.is_set()))
        started.set()
        # A synchronous regression must fail the identity assertions, not deadlock
        # the calling thread before its finally block can release the barrier.
        if threading.get_ident() != gui_thread_id:
            release.wait()
        returned.set()
        return {"tags": [], "input_sha256": "0" * 64}, b""

    worker = None
    with patch("imagesorter.queue_worker.run_reader", side_effect=blocked_reader):
        try:
            worker = QueueWorker(settings)
            before_commit_ticks = []
            QTimer.singleShot(0, lambda: before_commit_ticks.append(threading.get_ident()))
            qtbot.waitUntil(lambda: bool(before_commit_ticks), timeout=5000)
            assert before_commit_ticks == [gui_thread_id]
            assert calls == []
            assert not worker.readers_running()

            # The facade receives the successful primary result before it starts
            # optional enrichment. This test exercises that lifecycle boundary.
            worker.signals.operation_result.connect(lambda _result: committed.set())
            worker._requests["parent"] = {"task_options": {"settings_snapshot": settings.snapshot()}}
            worker._result({"operation_id": "parent", "action": "copy", "state": "completed",
                            "source_path": "original", "destination_path": "committed"})
            qtbot.waitUntil(started.is_set, timeout=5000)
            assert len(calls) == 1
            request, reader_thread_id, reader_qthread, saw_commit = calls[0]
            assert request["action"] == "infer"
            assert request["filepath"] == "committed"
            assert saw_commit
            assert reader_thread_id != gui_thread_id
            assert reader_qthread != gui_qthread
            assert worker.readers_running()
            assert not returned.is_set()

            during_inference_ticks = []
            QTimer.singleShot(0, lambda: during_inference_ticks.append(
                (threading.get_ident(), QThread.currentThread(), returned.is_set())))
            qtbot.waitUntil(lambda: bool(during_inference_ticks), timeout=5000)
            assert during_inference_ticks == [(gui_thread_id, gui_qthread, False)]
            assert worker.readers_running()
            assert not release.is_set()
        finally:
            release.set()
            if worker is not None:
                try:
                    qtbot.waitUntil(lambda: not worker.readers_running(), timeout=5000)
                finally:
                    worker.shutdown()
        assert returned.is_set()


def test_model_integrity_check_does_not_block_settings_dialog(qtbot, tmp_path):
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    gui_thread_id = threading.get_ident()
    gui_qthread = QThread.currentThread()
    started = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    calls = []

    def blocked_check(request, **_kwargs):
        calls.append((request, threading.get_ident(), QThread.currentThread()))
        started.set()
        # Preserve a failure path for an accidental GUI-thread invocation.
        if threading.get_ident() != gui_thread_id:
            release.wait()
        returned.set()
        return {"valid": False}, b""

    window = None
    with patch("imagesorter.reader_process.run_reader", blocked_check):
        try:
            window = SettingsWindow(settings)
            qtbot.addWidget(window)
            window.show()
            qtbot.waitUntil(started.is_set, timeout=5000)
            assert len(calls) == 1
            request, check_thread_id, check_qthread = calls[0]
            assert request["action"] == "validate_model"
            assert check_thread_id != gui_thread_id
            assert check_qthread != gui_qthread
            assert window.check_worker is not None
            assert window.check_worker.isRunning()
            assert window.btn_download_model.text() == "Checking Model…"
            assert not window.btn_download_model.isEnabled()
            assert not returned.is_set()

            # Dispatch actual input and a queued timer while the check cannot
            # complete. Neither response depends on a wall-clock latency target.
            window.src_edit.setFocus()
            qtbot.keyClicks(window.src_edit, "still responsive")
            assert window.src_edit.text() == "still responsive"
            ui_ticks = []
            QTimer.singleShot(0, lambda: ui_ticks.append(
                (threading.get_ident(), QThread.currentThread(), returned.is_set(),
                 window.btn_download_model.text())))
            qtbot.waitUntil(lambda: bool(ui_ticks), timeout=5000)
            assert ui_ticks == [(gui_thread_id, gui_qthread, False, "Checking Model…")]
            assert window.check_worker.isRunning()
            assert not release.is_set()
        finally:
            release.set()
            if window is not None:
                qtbot.waitUntil(lambda: window.check_worker is None, timeout=5000)
        assert returned.is_set()
        assert window.btn_download_model.text() == "Manage AI Model…"
        assert window.btn_download_model.isEnabled()


def test_closing_settings_does_not_open_download_completion_dialog(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    window = SettingsWindow(SettingsManager(filepath=str(tmp_path / "settings.json")))
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window.check_worker or not window.check_worker.isRunning(), timeout=3000)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Closing settings must not open a blocking completion dialog")
    for name in ("information", "critical"):
        monkeypatch.setattr(QMessageBox, name, forbidden)
    window._close_pending = True
    window.on_download_finished(False, "Cancelled")
    window._close_pending = False
