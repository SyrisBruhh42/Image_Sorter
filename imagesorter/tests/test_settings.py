import os
import json
import pytest
from src.settings_manager import SettingsManager, DEFAULT_SETTINGS

def test_default_values(tmp_path):
    """Test that default values are populated when no settings file exists."""
    settings_file = tmp_path / "settings.json"
    manager = SettingsManager(filepath=str(settings_file))
    assert manager.settings == DEFAULT_SETTINGS

def test_schema_migration(tmp_path):
    """Test merging default settings with existing incomplete settings."""
    settings_file = tmp_path / "settings.json"
    initial_data = {
        "directories": {
            "source": "/custom/path"
        },
        "ui": {
            "fullscreen": True
        }
    }
    settings_file.write_text(json.dumps(initial_data))

    manager = SettingsManager(filepath=str(settings_file))

    # Check that existing values are kept
    assert manager.get("directories", "source") == "/custom/path"
    assert manager.get("ui", "fullscreen") is True

    # Check that missing keys are populated with defaults
    assert manager.get("directories", "trash") == DEFAULT_SETTINGS["directories"]["trash"]
    assert manager.get("ui", "show_tags") == DEFAULT_SETTINGS["ui"]["show_tags"]
    assert manager.get("metadata", "write_exif") == DEFAULT_SETTINGS["metadata"]["write_exif"]

def test_atomic_writing(tmp_path, mocker):
    """Test atomic writing mechanism."""
    settings_file = tmp_path / "settings.json"
    manager = SettingsManager(filepath=str(settings_file))

    # Spy on os.replace to ensure atomic write
    replace_spy = mocker.spy(os, 'replace')

    manager.set("directories", "source", "/new/path")

    assert replace_spy.call_count == 1
    assert replace_spy.call_args[0][1] == str(settings_file)

    # Check the file content
    with open(settings_file, 'r') as f:
        data = json.load(f)
    assert data["directories"]["source"] == "/new/path"

def test_recovery_from_malformed_json(tmp_path):
    """Test adversarial case: Recovery from malformed/corrupted JSON."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{malformed: json,")

    manager = SettingsManager(filepath=str(settings_file))

    # Should fallback to default settings without crashing
    assert manager.settings == DEFAULT_SETTINGS
