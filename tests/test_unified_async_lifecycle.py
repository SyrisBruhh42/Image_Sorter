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
    sentinel = object()

    def slow_factory(**_kwargs):
        started.set()
        release.wait(timeout=2)
        return sentinel

    with patch("imagesorter.queue_worker.AITagger", side_effect=slow_factory):
        before = time.monotonic()
        worker = QueueWorker(settings)
        elapsed = time.monotonic() - before
        assert elapsed < 0.5
        assert started.wait(timeout=1)
        assert worker.ai_tagger is None

        release.set()
        qtbot.waitUntil(lambda: worker.ai_tagger is sentinel, timeout=3000)
        worker.shutdown()


def test_model_integrity_check_does_not_block_settings_dialog(qtbot, tmp_path):
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    started = threading.Event()
    release = threading.Event()

    def slow_check(_model_dir=None):
        started.set()
        release.wait(timeout=2)
        return False

    with patch("imagesorter.ui_settings.is_model_and_labels_valid", slow_check):
        before = time.monotonic()
        window = SettingsWindow(settings)
        qtbot.addWidget(window)
        elapsed = time.monotonic() - before

        assert elapsed < 0.5
        assert started.wait(timeout=1)
        assert window.btn_download_model.text() == "Checking Model…"
        release.set()
        qtbot.waitUntil(
            lambda: window.btn_download_model.text() == "Download Model",
            timeout=3000,
        )
