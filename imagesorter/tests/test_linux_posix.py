import os
import sys
from pathlib import Path
import pytest
from unittest import mock
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import Qt

from src.paths import (
    get_config_dir,
    get_data_dir,
    get_cache_dir,
    get_logs_dir,
    get_resource_dir,
    get_app_dir,
    is_portable_mode,
    _get_valid_env_path,
)


def test_get_valid_env_path(tmp_path, monkeypatch):
    # Absolute path
    abs_dir = str(tmp_path / "abs_path")
    monkeypatch.setenv("TEST_ENV_VAR", abs_dir)
    assert _get_valid_env_path("TEST_ENV_VAR") == Path(abs_dir)

    # Relative path
    monkeypatch.setenv("TEST_ENV_VAR", "relative/path")
    assert _get_valid_env_path("TEST_ENV_VAR") is None

    # Empty path
    monkeypatch.setenv("TEST_ENV_VAR", "")
    assert _get_valid_env_path("TEST_ENV_VAR") is None

    # Unset variable
    monkeypatch.delenv("TEST_ENV_VAR", raising=False)
    assert _get_valid_env_path("TEST_ENV_VAR") is None


def test_xdg_paths_valid_and_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")

    # 1. Custom absolute paths
    custom_config = str(tmp_path / "custom_config")
    custom_data = str(tmp_path / "custom_data")
    custom_cache = str(tmp_path / "custom_cache")
    custom_state = str(tmp_path / "custom_state")

    monkeypatch.setenv("XDG_CONFIG_HOME", custom_config)
    monkeypatch.setenv("XDG_DATA_HOME", custom_data)
    monkeypatch.setenv("XDG_CACHE_HOME", custom_cache)
    monkeypatch.setenv("XDG_STATE_HOME", custom_state)

    assert get_config_dir() == Path(custom_config) / "ImageSorter"
    assert get_data_dir() == Path(custom_data) / "ImageSorter"
    assert get_cache_dir() == Path(custom_cache) / "ImageSorter"
    assert get_logs_dir() == Path(custom_state) / "ImageSorter" / "logs"

    # 2. Relative paths (should fall back strictly to home defaults)
    monkeypatch.setenv("XDG_CONFIG_HOME", "rel_config")
    monkeypatch.setenv("XDG_DATA_HOME", "rel_data")
    monkeypatch.setenv("XDG_CACHE_HOME", "rel_cache")
    monkeypatch.setenv("XDG_STATE_HOME", "rel_state")

    home = Path.home()
    assert get_config_dir() == home / ".config" / "ImageSorter"
    assert get_data_dir() == home / ".local" / "share" / "ImageSorter"
    assert get_cache_dir() == home / ".cache" / "ImageSorter"
    assert get_logs_dir() == home / ".local" / "state" / "ImageSorter" / "logs"


def test_is_portable_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("src.paths.get_app_dir", lambda: tmp_path)

    # settings.json exists, but NO portable.flag
    (tmp_path / "settings.json").touch()
    assert not is_portable_mode()

    # portable.flag exists
    (tmp_path / "portable.flag").touch()
    assert is_portable_mode()


def test_get_resource_dir(tmp_path, monkeypatch):
    # Non-frozen script execution
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr("src.paths.get_app_dir", lambda: tmp_path)
    assert get_resource_dir() == tmp_path

    # Frozen PyInstaller binary with _MEIPASS
    meipass_dir = tmp_path / "meipass_temp"
    meipass_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass_dir), raising=False)
    assert get_resource_dir() == meipass_dir.resolve()


def test_display_server_overrides(monkeypatch):
    from src.main import main

    # Non-Linux platform should NOT alter QT_QPA_PLATFORM
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    # Mock QApplication and MainViewer to prevent full UI loop during main call test
    with mock.patch("src.main.QApplication"), \
         mock.patch("src.main.MainViewer"), \
         mock.patch("sys.exit"):
        main()
        assert "QT_QPA_PLATFORM" not in os.environ

    # Linux platform with WAYLAND_DISPLAY
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")

    with mock.patch("src.main.QApplication"), \
         mock.patch("src.main.MainViewer"), \
         mock.patch("sys.exit"):
        main()
        assert os.environ.get("QT_QPA_PLATFORM") == "wayland;xcb"

    # Linux platform with only DISPLAY
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with mock.patch("src.main.QApplication"), \
         mock.patch("src.main.MainViewer"), \
         mock.patch("sys.exit"):
        main()
        assert os.environ.get("QT_QPA_PLATFORM") == "xcb"


def test_desktop_file_name_configuration(qapp):
    assert QGuiApplication.desktopFileName() == "imagesorter.desktop"
