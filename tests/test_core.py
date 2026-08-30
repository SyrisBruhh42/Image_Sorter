import json

import imagesorter.paths as paths_module
from imagesorter.paths import get_config_dir, get_data_dir, get_cache_dir, get_logs_dir, get_settings_path, is_portable_mode
from imagesorter.settings_manager import SettingsManager
from imagesorter.hardware_scan import scan_hardware, get_prioritized_providers
from imagesorter.ai_tagger import AITagger, BaseVisionEngine, write_metadata


def test_paths_xdg_and_portable(monkeypatch, tmp_path):
    # Test non-portable path resolution with XDG env vars
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    # Mock get_app_dir to prevent portable mode detection from app root
    monkeypatch.setattr(paths_module, "get_app_dir", lambda: tmp_path / "non_portable_root")

    assert get_config_dir() == tmp_path / "config" / "ImageSorter"
    assert get_data_dir() == tmp_path / "data" / "ImageSorter"
    assert get_cache_dir() == tmp_path / "cache" / "ImageSorter"
    assert get_logs_dir() == tmp_path / "state" / "ImageSorter" / "logs"
    assert get_settings_path() == tmp_path / "config" / "ImageSorter" / "settings.json"
    assert not is_portable_mode()

    # Test Portable Mode path resolution
    portable_root = tmp_path / "portable_app"
    portable_root.mkdir()
    (portable_root / "portable.flag").touch()
    monkeypatch.setattr(paths_module, "get_app_dir", lambda: portable_root)

    assert is_portable_mode()
    assert get_config_dir() == portable_root / "config"
    assert get_data_dir() == portable_root / "data"
    assert get_cache_dir() == portable_root / "cache"
    assert get_logs_dir() == portable_root / "logs"
    assert get_settings_path() == portable_root / "settings.json"


def test_settings_manager_atomic_and_corruption(tmp_path):
    settings_file = tmp_path / "test_settings.json"
    sm = SettingsManager(filepath=str(settings_file))

    # Verify initial write and structure
    assert settings_file.exists()
    sm.set("directories", "source", str(tmp_path / "source"))
    assert sm.get("directories", "source") == str(tmp_path / "source")

    # Verify atomic update
    sm.set("ui", "theme", "High Contrast")
    sm.save()
    with open(settings_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["ui"]["theme"] == "High Contrast"

    # Simulate corrupt JSON
    with open(settings_file, "w", encoding="utf-8") as f:
        f.write("{invalid_json_corrupted:")

    # Reload should self-heal and back up corrupt file with timestamp
    sm.load()
    assert sm.get("directories", "source") == ""  # reset to default
    corrupt_files = list(tmp_path.glob("*.corrupt.*.bak"))
    assert len(corrupt_files) == 1


def test_hardware_scan():
    hw = scan_hardware()
    assert "physical_cores" in hw
    assert "logical_cores" in hw
    assert "onnx_providers" in hw
    assert "CPUExecutionProvider" in hw["onnx_providers"]

    providers = get_prioritized_providers()
    assert isinstance(providers, list)
    assert len(providers) > 0


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
