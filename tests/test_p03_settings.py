import os

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QMessageBox

from imagesorter.paths import _ensure_dir_or_fallback
from imagesorter.settings_manager import (
    SettingsManager,
    SettingsPersistenceError,
)
from imagesorter.ui_settings import SettingsWindow


class InterruptibleDownloaderDouble(QThread):
    """Test double for ModelDownloader honoring interruption requests deterministically."""
    def __init__(self):
        super().__init__()
        self.interrupted = False

    def run(self):
        for _ in range(50):
            if self.isInterruptionRequested():
                self.interrupted = True
                return
            self.msleep(10)


def test_tooltip_round_trip(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))
    assert sm.get("ui", "tooltips_enabled") is True

    window = SettingsWindow(sm)
    qtbot.addWidget(window)

    # Toggle tooltips off
    window.chk_tooltips.setChecked(False)

    # Save settings
    with pytest.MonkeyPatch.context() as m:
        m.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
        window.save_settings()

    # Re-read settings from manager and new instance
    assert sm.get("ui", "tooltips_enabled") is False

    sm_reloaded = SettingsManager(filepath=str(settings_file))
    assert sm_reloaded.get("ui", "tooltips_enabled") is False


def test_shared_contract_snapshot_and_apply_changes(tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    snap = sm.snapshot()
    assert isinstance(snap, dict)
    assert snap["ui"]["font_size"] == 24

    # Mutating snapshot dict must not affect internal state
    snap["ui"]["font_size"] = 99
    assert sm.get("ui", "font_size") == 24

    # Apply changes atomically
    changes = {
        "ui": {
            "fullscreen": True,
            "show_tags": False,
            "tooltips_enabled": False,
            "theme": "High Contrast",
            "font_size": 36
        }
    }
    sm.apply_changes(changes)
    assert sm.get("ui", "fullscreen") is True
    assert sm.get("ui", "tooltips_enabled") is False
    assert sm.get("ui", "font_size") == 36


def test_atomic_apply_changes_failure_raises_persistence_error(tmp_path, monkeypatch):
    sm = SettingsManager(filepath=str(tmp_path / "settings.json"))

    def mock_persist_dict(data):
        raise SettingsPersistenceError("Simulated write failure")

    monkeypatch.setattr(sm, "_persist_dict", mock_persist_dict)

    changes = {"ui": {"font_size": 48}}
    initial_font_size = sm.get("ui", "font_size")

    with pytest.raises(SettingsPersistenceError):
        sm.apply_changes(changes)

    # In-memory state must remain unchanged when persistence fails
    assert sm.get("ui", "font_size") == initial_font_size


def test_get_returns_deep_copy(tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    ui_dict = sm.get("section_does_not_exist")
    assert ui_dict is None

    ui_dict = sm.get("ui")
    assert isinstance(ui_dict, dict)
    ui_dict["font_size"] = 999

    assert sm.get("ui", "font_size") == 24


def test_file_vs_directory_and_hotkey_validation(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    window = SettingsWindow(sm)
    qtbot.addWidget(window)

    # Create a regular file to test file vs directory rejection
    test_file = tmp_path / "regular_file.txt"
    test_file.write_text("hello", encoding="utf-8")

    # 1. Source dir set to a regular file
    window.src_edit.setText(str(test_file))
    warn_calls = []

    with pytest.MonkeyPatch.context() as m:
        m.setattr(QMessageBox, "warning", lambda parent, title, text: warn_calls.append(text))
        window.save_settings()

    assert len(warn_calls) == 1
    assert "file, not a directory" in warn_calls[0]
    window.src_edit.setText("")

    # 2. Hotkey multi-character rejection
    window.add_hotkey_row(key="AB", action="move", folder="", auto_advance=True)
    warn_calls.clear()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(QMessageBox, "warning", lambda parent, title, text: warn_calls.append(text))
        window.save_settings()

    assert len(warn_calls) == 1
    assert "single character" in warn_calls[0]

    # 3. Hotkey duplicate key rejection
    window.hotkey_table.setRowCount(0)
    window.add_hotkey_row(key="X", action="move", folder="", auto_advance=True)
    window.add_hotkey_row(key="X", action="copy", folder="", auto_advance=True)
    warn_calls.clear()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(QMessageBox, "warning", lambda parent, title, text: warn_calls.append(text))
        window.save_settings()

    assert len(warn_calls) == 1
    assert "Duplicate hotkey binding" in warn_calls[0]


def test_user_isolated_temp_fallback(tmp_path, monkeypatch):
    target_dir = tmp_path / "unwritable_dir"
    target_dir.mkdir()

    # Make target_dir unwritable by mocking _test_directory_writable to return False
    monkeypatch.setattr("imagesorter.paths._test_directory_writable", lambda d: False)

    fallback_dir = _ensure_dir_or_fallback(target_dir, "config")

    uid = os.getuid() if hasattr(os, "getuid") else os.getlogin()
    expected_part = f"imagesorter-{uid}"
    assert expected_part in str(fallback_dir)


def test_downloader_interruption_cancellation(qtbot):
    downloader = InterruptibleDownloaderDouble()
    downloader.start()
    assert downloader.isRunning()

    downloader.requestInterruption()
    downloader.wait(2000)

    assert not downloader.isRunning()
    assert downloader.interrupted is True


def test_clear_button_enabled_on_line_edits(qtbot, tmp_path):
    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    window = SettingsWindow(sm)
    qtbot.addWidget(window)

    assert window.src_edit.isClearButtonEnabled() is True
    assert window.trash_edit.isClearButtonEnabled() is True

    window.add_hotkey_row(key="A", action="move", folder="/tmp", auto_advance=True)
    folder_widget = window.hotkey_table.cellWidget(0, 2)
    folder_edit = folder_widget.layout().itemAt(0).widget()
    assert folder_edit.isClearButtonEnabled() is True
