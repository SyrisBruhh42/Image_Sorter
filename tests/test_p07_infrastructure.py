import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

import imagesorter.paths as paths_module
from imagesorter.paths import get_config_dir, get_data_dir
from scripts.diagnose_linux_p07 import (
    generate_diagnostic_report,
    get_distro_info,
    run_command,
    sanitize_string,
)


def test_sanitize_string_redacts_home_and_user(monkeypatch):
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.setenv("HOME", "/home/testuser")

    raw_text = "Error loading /home/testuser/photos/vacation.jpg for testuser"
    sanitized = sanitize_string(raw_text)

    assert "/home/testuser" not in sanitized
    assert "~" in sanitized or "[REDACTED_USER]" in sanitized
    assert "vacation.jpg" in sanitized


def test_diagnose_linux_distro_info():
    info = get_distro_info()
    assert "system" in info
    assert "release" in info
    assert "machine" in info


def test_run_command_nonexistent():
    res = run_command(["nonexistent_command_12345"])
    assert res is None


def test_generate_diagnostic_report_structure():
    report = generate_diagnostic_report()
    assert "distro" in report
    assert "kde_plasma" in report
    assert "qt" in report
    assert "display_session" in report
    assert "runtime_dependencies" in report
    assert "onnx_providers" in report


def test_diagnostic_script_cli():
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "diagnose_linux_p07.py"

    # Test standard output
    res = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "ImageSorter System Diagnostics" in res.stdout

    # Test JSON output
    res_json = subprocess.run(
        [sys.executable, str(script_path), "--json"],
        capture_output=True,
        text=True,
    )
    assert res_json.returncode == 0
    parsed = json.loads(res_json.stdout)
    assert "distro" in parsed
    assert "runtime_dependencies" in parsed


def test_path_isolation_and_temp_file_ops(monkeypatch, tmp_path):
    original_home = os.environ.get("HOME")
    isolated_config = tmp_path / "isolated_config"
    isolated_data = tmp_path / "isolated_data"

    monkeypatch.setattr(paths_module, "get_app_dir", lambda: tmp_path / "app")

    if sys.platform.startswith("win"):
        monkeypatch.setenv("APPDATA", str(isolated_config))
        monkeypatch.setenv("LOCALAPPDATA", str(isolated_data))
        expected_config = isolated_config / "ImageSorter"
        expected_data = isolated_data / "ImageSorter" / "Data"
    elif sys.platform == "darwin":
        expected_config = Path.home() / "Library" / "Application Support" / "ImageSorter"
        expected_data = Path.home() / "Library" / "Application Support" / "ImageSorter"
    else:
        # Linux / POSIX
        monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_config))
        monkeypatch.setenv("XDG_DATA_HOME", str(isolated_data))
        expected_config = isolated_config / "ImageSorter"
        expected_data = isolated_data / "ImageSorter"

    # Verify HOME environment variable remains unchanged
    assert os.environ.get("HOME") == original_home

    # Verify paths module routes config and data into expected paths
    config_dir = get_config_dir()
    data_dir = get_data_dir()

    assert config_dir == expected_config
    assert data_dir == expected_data

    # Verify writing temporary image fixture in isolated environment
    data_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = data_dir / "sample.jpg"
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(fixture_path, "JPEG")

    assert fixture_path.exists()
