from pathlib import Path

import imagesorter.paths as paths_module
from imagesorter.paths import get_config_dir, get_data_dir, is_portable_mode


def test_xdg_validation_strict_absolute(monkeypatch, tmp_path):
    # Relative path in XDG should be ignored and fall back to POSIX home default
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setattr(paths_module, "get_app_dir", lambda: tmp_path / "app")

    home = Path.home()
    assert get_config_dir() == home / ".config" / "ImageSorter"
    assert get_data_dir() == home / ".local" / "share" / "ImageSorter"

    # Absolute path in XDG should be respected
    abs_config = tmp_path / "abs_config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(abs_config))
    assert get_config_dir() == abs_config / "ImageSorter"


def test_portable_flag_detection(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(paths_module, "get_app_dir", lambda: app_dir)

    assert not is_portable_mode()
    (app_dir / "portable.flag").touch()
    assert is_portable_mode()


def test_freedesktop_desktop_entry(tmp_path):
    import build_desktop as build
    build.generate_freedesktop_artifacts(tmp_path)

    desktop_file = tmp_path / "imagesorter.desktop"

    assert desktop_file.exists()
    assert "Exec=ImageSorter %F" in desktop_file.read_text(encoding="utf-8")
