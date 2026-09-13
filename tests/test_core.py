import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import imagesorter.paths as paths_module
from imagesorter import platform_capabilities
from imagesorter.ai_tagger import AITagger, BaseVisionEngine, write_metadata
from imagesorter.hardware_scan import get_prioritized_providers, scan_hardware
from imagesorter.paths import (
    get_cache_dir,
    get_component_cache_dir,
    get_components_dir,
    get_config_dir,
    get_data_dir,
    get_logs_dir,
    get_settings_path,
    is_portable_mode,
)
from imagesorter.platform_capabilities import mutation_unavailable_reason
from imagesorter.settings_manager import SettingsManager


@pytest.fixture
def private_path_environment(monkeypatch, tmp_path):
    """Select OS contracts without using the host's home or explicit CI profile."""
    private_home = tmp_path / "private-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: private_home))
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(paths_module, "get_app_dir", lambda: app_dir)
    monkeypatch.delenv("IMAGESORTER_PROFILE_ROOT", raising=False)
    for name, relative in {
        "XDG_CONFIG_HOME": "xdg-config", "XDG_DATA_HOME": "xdg-data",
        "XDG_CACHE_HOME": "xdg-cache", "XDG_STATE_HOME": "xdg-state",
        "APPDATA": "roaming", "LOCALAPPDATA": "local",
    }.items():
        monkeypatch.setenv(name, str(tmp_path / relative))
    return private_home, app_dir


def assert_profile_paths(expected, *, settings_path=None):
    """Exercise real creation/writability checks as well as path selection."""
    getters = (get_config_dir, get_data_dir, get_cache_dir, get_logs_dir)
    for getter, directory in zip(getters, expected):
        assert getter() == directory
        assert directory.is_dir()
    assert get_settings_path() == (settings_path or expected[0] / "settings.json")
    assert get_components_dir() == expected[1] / "components"
    assert get_component_cache_dir() == expected[2] / "component-downloads"


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_native_platform_paths_with_environment(private_path_environment, monkeypatch, tmp_path, platform):
    private_home, _ = private_path_environment
    # Replace only the paths module's platform input, not the interpreter's sys.platform.
    monkeypatch.setattr(paths_module, "sys", SimpleNamespace(platform=platform))
    expected = {
        "linux": tuple(tmp_path / category / "ImageSorter" for category in
                       ("xdg-config", "xdg-data", "xdg-cache", "xdg-state")),
        "win32": (tmp_path / "roaming" / "ImageSorter",
                  tmp_path / "local" / "ImageSorter" / "Data",
                  tmp_path / "local" / "ImageSorter" / "Cache",
                  tmp_path / "local" / "ImageSorter" / "Logs"),
        "darwin": (private_home / "Library" / "Application Support" / "ImageSorter",
                   private_home / "Library" / "Application Support" / "ImageSorter",
                   private_home / "Library" / "Caches" / "ImageSorter",
                   private_home / "Library" / "Logs" / "ImageSorter"),
    }[platform]
    if platform == "linux":
        expected = (*expected[:3], expected[3] / "logs")
    assert not is_portable_mode()
    assert_profile_paths(expected)


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
@pytest.mark.parametrize("environment", [None, "", "relative-directory"])
def test_native_platform_defaults_reject_nonabsolute_environment(
        private_path_environment, monkeypatch, platform, environment):
    private_home, _ = private_path_environment
    monkeypatch.setattr(paths_module, "sys", SimpleNamespace(platform=platform))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA"):
        if environment is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, environment)
    expected = {
        "linux": (private_home / ".config" / "ImageSorter",
                  private_home / ".local" / "share" / "ImageSorter",
                  private_home / ".cache" / "ImageSorter",
                  private_home / ".local" / "state" / "ImageSorter" / "logs"),
        "win32": (private_home / "AppData" / "Roaming" / "ImageSorter",
                  private_home / "AppData" / "Local" / "ImageSorter" / "Data",
                  private_home / "AppData" / "Local" / "ImageSorter" / "Cache",
                  private_home / "AppData" / "Local" / "ImageSorter" / "Logs"),
        "darwin": (private_home / "Library" / "Application Support" / "ImageSorter",
                   private_home / "Library" / "Application Support" / "ImageSorter",
                   private_home / "Library" / "Caches" / "ImageSorter",
                   private_home / "Library" / "Logs" / "ImageSorter"),
    }[platform]
    assert not is_portable_mode()
    assert_profile_paths(expected)


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_portable_paths_override_native_environment(private_path_environment, monkeypatch, platform):
    _, app_dir = private_path_environment
    monkeypatch.setattr(paths_module, "sys", SimpleNamespace(platform=platform))
    (app_dir / "portable.flag").touch()
    assert is_portable_mode()
    assert_profile_paths(tuple(app_dir / category for category in ("config", "data", "cache", "logs")),
                         settings_path=app_dir / "settings.json")


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_explicit_profile_overrides_portable_and_native_paths(
        private_path_environment, monkeypatch, tmp_path, platform):
    _, app_dir = private_path_environment
    monkeypatch.setattr(paths_module, "sys", SimpleNamespace(platform=platform))
    (app_dir / "portable.flag").touch()
    profile = tmp_path / "explicit-profile"
    monkeypatch.setenv("IMAGESORTER_PROFILE_ROOT", str(profile))
    assert not is_portable_mode()
    assert_profile_paths((profile / "config" / "ImageSorter", profile / "data" / "ImageSorter",
                          profile / "cache" / "ImageSorter", profile / "state" / "ImageSorter" / "logs"))
    assert list(app_dir.iterdir()) == [app_dir / "portable.flag"]


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_unavailable_explicit_profile_fails_without_native_fallback(
        private_path_environment, monkeypatch, tmp_path, platform):
    private_home, app_dir = private_path_environment
    monkeypatch.setattr(paths_module, "sys", SimpleNamespace(platform=platform))
    profile = tmp_path / "unavailable-profile"
    profile.write_text("existing file", encoding="utf-8")
    monkeypatch.setenv("IMAGESORTER_PROFILE_ROOT", str(profile))
    for getter in (get_config_dir, get_data_dir, get_cache_dir, get_logs_dir):
        with pytest.raises(OSError, match="Explicit profile is unavailable"):
            getter()
    assert profile.read_text(encoding="utf-8") == "existing file"
    assert not private_home.exists()
    assert not list(app_dir.iterdir())


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


@pytest.mark.parametrize("unsupported", [False, True])
def test_ai_tagger_contract_and_metadata(tmp_path, monkeypatch, unsupported):
    if unsupported:
        monkeypatch.setattr(platform_capabilities, "sys", SimpleNamespace(platform="win32"))
    # Verify contract inheritance
    tagger = AITagger(model_dir=str(tmp_path))
    assert isinstance(tagger, BaseVisionEngine)

    # Test sidecar metadata writing
    img_file = tmp_path / "test.jpg"
    img_file.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xFF\xD9")

    tags = ["cat", "animal"]
    sidecar = tmp_path / "test.jpg.txt"
    if reason := mutation_unavailable_reason():
        before = {path.relative_to(tmp_path): path.read_bytes()
                  for path in tmp_path.rglob("*") if path.is_file()}
        with pytest.raises(RuntimeError) as error:
            write_metadata(str(img_file), tags, write_exif=False, write_sidecar=True)
        assert str(error.value) == reason
        assert not sidecar.exists()
        # Refusal must leave the image exact and create no staged/sidecar files.
        assert {path.relative_to(tmp_path): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file()} == before
    else:
        write_metadata(str(img_file), tags, write_exif=False, write_sidecar=True)
        assert sidecar.exists()
        assert sidecar.read_text(encoding="utf-8") == "cat, animal"
