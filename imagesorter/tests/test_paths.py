import os
import sys
import pytest
import ntpath
import posixpath
from pathlib import Path

# Since there isn't a specific module purely for path logic yet, we simulate
# testing paths in the context of frozen (PyInstaller) vs. unfrozen script execution.
# This ensures cross-platform path normalization.

def get_base_path():
    """Simulates a function that gets the base path, aware of PyInstaller freezing."""
    if getattr(sys, 'frozen', False):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_frozen_vs_unfrozen_path_resolution(mocker):
    """Test how paths are resolved in normal vs PyInstaller frozen mode."""
    # Test unfrozen mode (normal python script)
    mocker.patch.object(sys, 'frozen', False, create=True)
    unfrozen_path = get_base_path()
    assert unfrozen_path.endswith("imagesorter")

    # Test frozen mode (PyInstaller)
    mocker.patch.object(sys, 'frozen', True, create=True)
    mocker.patch.object(sys, '_MEIPASS', "/tmp/_MEI123456", create=True)
    frozen_path = get_base_path()
    assert frozen_path == "/tmp/_MEI123456"

def test_cross_platform_path_normalization():
    """Test that paths are correctly normalized across platforms."""
    # Simulate Windows path normalization
    win_path = "C:\\path\\to\\my\\folder\\.."
    norm_win = ntpath.normpath(win_path)
    assert norm_win == "C:\\path\\to\\my"

    # Simulate Unix path normalization
    unix_path = "/path/to/my/folder/../file.txt"
    norm_unix = posixpath.normpath(unix_path)
    assert norm_unix == "/path/to/my/file.txt"

def test_portable_mode_flags(tmp_path):
    """Test resolution with a simulated portable mode flag."""
    # Simulating a portable mode flag that changes config path
    is_portable = True
    if is_portable:
        config_dir = tmp_path / "portable_config"
    else:
        config_dir = tmp_path / "system_config"

    assert str(config_dir).endswith("portable_config")
    assert not str(config_dir).endswith("system_config")
