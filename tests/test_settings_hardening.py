import os
import json
import warnings
import pytest
from pathlib import Path
from unittest.mock import patch

from imagesorter.settings_manager import SettingsManager, DEFAULT_SETTINGS
import imagesorter.paths as paths_module
from imagesorter.paths import get_config_dir, get_data_dir, get_cache_dir, get_logs_dir
from imagesorter.logger import setup_logger


def test_malformed_json_corrupt_backup(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{corrupt json ...", encoding="utf-8")

    sm = SettingsManager(filepath=str(settings_file))
    assert sm.get("ui", "font_size") == 24

    corrupt_backups = list(tmp_path.glob("settings.json.corrupt.*.bak"))
    assert len(corrupt_backups) == 1
    assert corrupt_backups[0].read_text(encoding="utf-8") == "{corrupt json ..."


def test_corrupt_settings_preservation_when_backup_fails(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_content = "{corrupt json content}"
    settings_file.write_text(settings_content, encoding="utf-8")

    with patch.object(SettingsManager, "_backup_corrupt_file", return_value=False):
        sm = SettingsManager(filepath=str(settings_file))
        assert sm.get("ui", "font_size") == 24

    # Corrupt settings file must be preserved on disk and not overwritten
    assert settings_file.exists()
    assert settings_file.read_text(encoding="utf-8") == settings_content


def test_corrupt_backup_collision_resolution(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("bad data 1", encoding="utf-8")

    sm1 = SettingsManager(filepath=str(settings_file))
    bak1 = list(tmp_path.glob("settings.json.corrupt.*.bak"))
    assert len(bak1) == 1

    settings_file.write_text("bad data 2", encoding="utf-8")
    sm2 = SettingsManager(filepath=str(settings_file))
    bak2 = list(tmp_path.glob("settings.json.corrupt.*.bak"))
    assert len(bak2) == 2


def test_invalid_section_types_replacement(tmp_path):
    settings_file = tmp_path / "settings.json"
    invalid_data = {
        "ui": "not a dictionary",
        "hotkeys": ["not", "a", "dict"],
        "metadata": {
            "write_exif": False,
            "write_sidecar": True
        },
        "custom_root": "custom_value"
    }
    settings_file.write_text(json.dumps(invalid_data), encoding="utf-8")

    sm = SettingsManager(filepath=str(settings_file))

    # Invalid sections should revert to default dictionary
    assert sm.get("ui", "font_size") == 24
    assert sm.get("hotkeys") == {}

    # Valid sections and unknown root keys must be preserved
    assert sm.get("metadata", "write_exif") is False
    assert sm.get("metadata", "write_sidecar") is True
    assert sm.get("custom_root") == "custom_value"


def test_strict_types_and_numeric_clamping(tmp_path):
    settings_file = tmp_path / "settings.json"
    data = {
        "ui": {
            "font_size": 150,           # Exceeds max 72 -> clamped to 72
            "fullscreen": "yes"         # Invalid type -> default False
        },
        "advanced": {
            "worker_threads": -5,       # Below min 1 -> clamped to 1
            "hardware_acceleration": 1  # Int instead of bool -> default True
        },
        "ai_tagger": {
            "threshold": 5.0,           # Exceeds 1.0 -> clamped to 1.0
            "enabled": True
        }
    }
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    sm = SettingsManager(filepath=str(settings_file))
    assert sm.get("ui", "font_size") == 72
    assert sm.get("ui", "fullscreen") is False
    assert sm.get("advanced", "worker_threads") == 1
    assert sm.get("advanced", "hardware_acceleration") is True
    assert sm.get("ai_tagger", "threshold") == 1.0
    assert sm.get("ai_tagger", "enabled") is True

    # Test boolean strictness: True should NOT be accepted as worker_threads integer 1
    data_bool = {
        "advanced": {
            "worker_threads": True
        }
    }
    settings_file.write_text(json.dumps(data_bool), encoding="utf-8")
    sm_bool = SettingsManager(filepath=str(settings_file))
    assert sm_bool.get("advanced", "worker_threads") == 2


def test_hotkeys_dictionary_validation(tmp_path):
    settings_file = tmp_path / "settings.json"
    data = {
        "hotkeys": {
            " A ": {
                "action": "copy",
                "folder": "/valid/path",
                "auto_advance": False
            },
            "B": "invalid_item_type",
            "C": {
                "action": 123,                 # Non-string action -> fallback "move"
                "folder": "path\x00with_ctrl",  # Control char -> empty string ""
                "auto_advance": "not_bool"     # Non-bool auto_advance -> True
            },
            "": {
                "action": "move"
            }
        }
    }
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    sm = SettingsManager(filepath=str(settings_file))
    hotkeys = sm.get("hotkeys")

    assert "A" in hotkeys
    assert hotkeys["A"]["action"] == "copy"
    assert hotkeys["A"]["auto_advance"] is False
    assert "B" not in hotkeys
    assert "" not in hotkeys

    assert "C" in hotkeys
    assert hotkeys["C"]["action"] == "move"
    assert hotkeys["C"]["folder"] == ""
    assert hotkeys["C"]["auto_advance"] is True


def test_directory_validation_and_unreachable_preservation(tmp_path):
    non_existent_path = str(tmp_path / "non_existent_folder" / "sub")
    settings_file = tmp_path / "settings.json"

    data = {
        "directories": {
            "source": non_existent_path,
            "trash": 12345  # Non-string path -> reset to ""
        }
    }
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    sm = SettingsManager(filepath=str(settings_file))

    # Unreachable / non-existent valid string paths must NOT be erased
    assert sm.get("directories", "source") == os.path.normpath(non_existent_path)
    # Malformed non-string paths must be reset to ""
    assert sm.get("directories", "trash") == ""


def test_unknown_fields_preservation(tmp_path):
    settings_file = tmp_path / "settings.json"
    data = {
        "ui": {
            "font_size": 20,
            "custom_ui_field": "preserved"
        },
        "future_section": {
            "feature_enabled": True
        }
    }
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    sm = SettingsManager(filepath=str(settings_file))
    assert sm.get("ui", "font_size") == 20
    assert sm.get("ui", "custom_ui_field") == "preserved"
    assert sm.get("future_section", "feature_enabled") is True


def test_paths_unwritable_directory_fallback(tmp_path, monkeypatch):
    unwritable_dir = tmp_path / "unwritable_dir"
    unwritable_dir.mkdir()

    def mock_mkdir(self, *args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        config_dir = get_config_dir()
        data_dir = get_data_dir()
        cache_dir = get_cache_dir()
        logs_dir = get_logs_dir()

        # Should fall back to tempdir/ImageSorter/<category>
        assert "ImageSorter" in str(config_dir)
        assert "config" in str(config_dir)
        assert "data" in str(data_dir)
        assert "cache" in str(cache_dir)
        assert "logs" in str(logs_dir)

        # Runtime warnings should have been recorded
        assert len(w) >= 4
        assert any("Falling back to temporary directory" in str(item.message) for item in w)


def test_logger_fallback_to_console_when_unwritable(monkeypatch):
    def mock_get_logs_dir():
        raise OSError(13, "Logs directory unwritable")

    monkeypatch.setattr("imagesorter.logger.get_logs_dir", mock_get_logs_dir)

    # Calling setup_logger should not raise any exception
    test_logger = setup_logger(name="TestUnwritableLogger")
    assert test_logger is not None
    assert len(test_logger.handlers) >= 1
