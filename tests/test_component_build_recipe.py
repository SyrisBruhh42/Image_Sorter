"""Isolate pack builds and exclude unused console/GUI dependencies."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_component_packs import (
    COLLECT,
    clean_build_environment,
    helper_freeze_command,
    run,
)


@pytest.mark.parametrize("component_id", sorted(COLLECT))
def test_only_noninteractive_isolated_helper_dependencies(component_id):
    command = helper_freeze_command("/runtime/python", Path("/job"), Path("/captured"), component_id)
    assert command[:4] == ["/runtime/python", "-I", "-m", "PyInstaller"]
    excluded = [command[n + 1] for n, item in enumerate(command) if item == "--exclude-module"]
    assert excluded == ["PyQt6", "readline"]
    assert command[-1] == "/captured/src/imagesorter/component_worker.py"
    assert [command[n + 1] for n, item in enumerate(command) if item == "--collect-all"] == COLLECT[component_id]


def test_model_data_pack_has_no_executable_environment():
    with pytest.raises(ValueError, match="Only executable"):
        helper_freeze_command("unused", Path("/job"), Path("/captured"), "ai.mobilenet-v2")


@pytest.mark.parametrize("key", [
    "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP",
    "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
    "PIP_CONSTRAINT", "PIP_REQUIREMENT", "PIP_TRUSTED_HOST", "PIP_CONFIG_FILE",
    "_PYI_APPLICATION_HOME_DIR", "_PYI_PARENT_PROCESS_LEVEL", "_MEIPASS2",
    "PYINSTALLER_CONFIG_DIR", "LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG",
    "LD_PRELOAD", "LD_AUDIT", "DYLD_LIBRARY_PATH", "QT_PLUGIN_PATH",
    "QML_IMPORT_PATH", "VIRTUAL_ENV", "CONDA_PREFIX", "IMAGESORTER_PROFILE_ROOT",
    "LIBRARY_PATH", "CFLAGS", "CMAKE_PREFIX_PATH", "PKG_CONFIG_PATH",
])
def test_build_environment_removes_inherited_overrides(tmp_path, monkeypatch, key):
    monkeypatch.setenv(key, "/unrelated/ambient-setting")
    result = clean_build_environment(tmp_path)
    if key == "PIP_CONFIG_FILE":
        assert result[key] == os.devnull
    else:
        assert key not in result
    assert os.environ[key] == "/unrelated/ambient-setting"
    assert result["PATH"] == os.defpath
    assert result["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                 "XDG_STATE_HOME", "TMPDIR", "TMP", "TEMP"):
        path = Path(result[name])
        assert path.is_dir() and path.is_relative_to(tmp_path)
    assert result["PIP_NO_INPUT"] == result["PIP_NO_CACHE_DIR"] == "1"


def test_run_does_not_inherit_caller_cwd_or_python_path(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "unexpected_import.py").write_text("SHADOW = True\n")
    job = tmp_path / "job"
    job.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("PYTHONPATH", str(caller))
    code = ("import importlib.util,json,os; print(json.dumps({"
            "'cwd':os.getcwd(),'unexpected':importlib.util.find_spec('unexpected_import') is not None,"
            "'home':os.environ['HOME']}))")
    log = job / "build.log"
    run([sys.executable, "-c", code], log)
    observed = json.loads(log.read_text().splitlines()[-1])
    assert observed["cwd"] == str(job)
    assert observed["unexpected"] is False
    assert Path(observed["home"]).is_relative_to(job)


def test_pip_check_ignores_external_metadata_but_rejects_real_broken_environment(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    log = job / "build.log"
    environment = job / "venv"
    run([sys.executable, "-I", "-m", "venv", str(environment)], log)
    python = str(environment / "bin/python")
    caller = tmp_path / "caller"
    metadata_dir = caller / "src/ambient_contamination-1.0.dist-info"
    metadata_dir.mkdir(parents=True)
    metadata_text = ("Metadata-Version: 2.1\nName: ambient-contamination\nVersion: 1.0\n"
                     "Requires-Dist: imagesorter-nonexistent-test-dependency==999\n")
    (metadata_dir / "METADATA").write_text(metadata_text)
    unsafe_env = clean_build_environment(job)
    unsafe_env["PYTHONPATH"] = "src"
    unsafe = subprocess.run([python, "-m", "pip", "check"], cwd=caller,
                            env=unsafe_env, text=True, capture_output=True)
    assert unsafe.returncode == 1
    assert "imagesorter-nonexistent-test-dependency" in unsafe.stdout
    monkeypatch.chdir(caller)
    monkeypatch.setenv("PYTHONPATH", "src")
    run([python, "-I", "-m", "pip", "check"], log)
    assert "No broken requirements found." in log.read_text()
    site = next(environment.glob("lib/python*/site-packages"))
    installed = site / metadata_dir.name
    installed.mkdir()
    (installed / "METADATA").write_text(metadata_text)
    with pytest.raises(subprocess.CalledProcessError):
        run([python, "-I", "-m", "pip", "check"], log)
