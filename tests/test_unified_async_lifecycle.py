from __future__ import annotations

import threading
import time
from unittest.mock import patch

from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_settings import SettingsWindow


def test_queue_worker_does_not_load_ai_on_calling_thread(qtbot, tmp_path):
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    settings.set("ai_tagger", "enabled", True)
    started = threading.Event()
    release = threading.Event()
    def slow_reader(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return {"tags": [], "input_sha256": "0" * 64}, b""

    with patch("imagesorter.queue_worker.run_reader", side_effect=slow_reader):
        before = time.monotonic()
        worker = QueueWorker(settings)
        elapsed = time.monotonic() - before
        assert elapsed < 0.5
        assert not started.is_set()  # No inference/model discovery before a committed image.
        worker._requests["parent"] = {"task_options": {"settings_snapshot": settings.snapshot()}}
        worker._result({"operation_id": "parent", "action": "copy", "state": "completed",
                        "source_path": "original", "destination_path": "committed"})
        qtbot.waitUntil(started.is_set, timeout=1000)
        assert worker.readers_running()
        release.set()
        qtbot.waitUntil(lambda: not worker.readers_running(), timeout=3000)
        worker.shutdown()


def test_model_integrity_check_does_not_block_settings_dialog(qtbot, tmp_path):
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    started = threading.Event()
    release = threading.Event()

    def slow_check(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return {"valid": False}, b""

    with patch("imagesorter.reader_process.run_reader", slow_check):
        before = time.monotonic()
        window = SettingsWindow(settings)
        qtbot.addWidget(window)
        elapsed = time.monotonic() - before

        assert elapsed < 0.5
        assert started.wait(timeout=1)
        assert window.btn_download_model.text() == "Checking Model…"
        release.set()
        qtbot.waitUntil(
            lambda: window.btn_download_model.text() == "Manage AI Model…",
            timeout=3000,
        )


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
