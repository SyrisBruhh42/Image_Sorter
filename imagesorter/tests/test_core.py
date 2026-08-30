import os
import json
import pytest
from pathlib import Path

from src.paths import get_config_dir, get_data_dir, get_cache_dir, get_logs_dir, get_settings_path, is_portable_mode
from src.settings_manager import SettingsManager, SettingsSaveError
from src.hardware_scan import scan_hardware, get_prioritized_providers
from src.ai_tagger import AITagger, BaseVisionEngine, write_metadata


def test_paths_xdg_and_portable(monkeypatch, tmp_path):
    # Test non-portable path resolution
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    assert get_config_dir() == tmp_path / "config" / "ImageSorter"
    assert get_data_dir() == tmp_path / "data" / "ImageSorter"
    assert get_cache_dir() == tmp_path / "cache" / "ImageSorter"
    assert get_logs_dir() == tmp_path / "state" / "ImageSorter" / "logs"


def test_settings_manager_atomic_and_corruption(tmp_path):
    settings_file = tmp_path / "test_settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    # Verify initial write
    assert settings_file.exists()
    sm.set("directories", "source", str(tmp_path / "source"))
    assert sm.get("directories", "source") == str(tmp_path / "source")

    # Verify deepcopy snapshot isolation
    snapshot = sm.get("directories")
    snapshot["source"] = "mutated_value"
    assert sm.get("directories", "source") == str(tmp_path / "source")

    # Simulate corrupt JSON
    with open(settings_file, "w") as f:
        f.write("{invalid_json:")

    # Reload should self-heal and back up corrupt file
    sm.load()
    assert sm.get("directories", "source") == ""  # reset to default
    corrupt_files = list(tmp_path.glob("*.corrupt.*.bak"))
    assert len(corrupt_files) == 1


def test_settings_manager_save_error(tmp_path):
    # Pass a path in a non-existent file directory that raises PermissionError/OSError on creation
    read_only_dir = tmp_path / "readonly"
    read_only_dir.mkdir()
    os.chmod(read_only_dir, 0o444)

    settings_file = read_only_dir / "settings.json"
    sm = SettingsManager.__new__(SettingsManager)
    sm.filepath = str(settings_file)
    sm._lock = __import__("threading").RLock()
    sm.settings = {}

    with pytest.raises(SettingsSaveError):
        sm.save()

    os.chmod(read_only_dir, 0o777)


def test_hardware_scan():
    hw = scan_hardware()
    assert "physical_cores" in hw
    assert "logical_cores" in hw
    assert "onnx_providers" in hw
    assert "CPUExecutionProvider" in hw["onnx_providers"]


def test_ai_tagger_contract_and_metadata(tmp_path):
    # Verify contract inheritance
    tagger = AITagger(model_dir=str(tmp_path))
    assert isinstance(tagger, BaseVisionEngine)

    # Test sidecar metadata writing
    img_file = tmp_path / "test.jpg"
    img_file.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xFF\xD9")

    tags = ["cat", "animal"]
    write_metadata(str(img_file), tags, write_exif=False, write_sidecar=True)
    sidecar = tmp_path / "test.jpg.txt"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") == "cat, animal"
