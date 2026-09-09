from __future__ import annotations

import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import build_desktop as build


def test_run_app_compiles():
    """Verify that run_app.py compiles without syntax errors."""
    run_app_path = Path(__file__).resolve().parent.parent / "run_app.py"
    compiled_path = py_compile.compile(str(run_app_path), doraise=True)
    assert compiled_path is not None


def test_imagesorter_main_importable():
    """Verify imagesorter.main:main remains importable and runnable module-wise."""
    from imagesorter.main import main

    assert callable(main)


def test_module_execution():
    """Verify python -m imagesorter.main can be invoked in a subprocess without relative import errors."""
    cmd = [sys.executable, "-c", "import imagesorter.main; assert callable(imagesorter.main.main)"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Failed module execution check: {res.stderr}"


def test_build_pyinstaller_preflight():
    """Verify check_pyinstaller_installed returns boolean correctly."""
    assert build.check_pyinstaller_installed() in (True, False)


def test_appdir_preparation(tmp_path):
    """Verify AppDir structure, desktop integration, and AppRun permissions."""
    root_dir = Path(__file__).resolve().parent.parent
    dist_dir = tmp_path / "dist"
    exe_dir = dist_dir / "ImageSorter"
    exe_dir.mkdir(parents=True)
    (exe_dir / "ImageSorter").write_bytes(b"binary")

    build.generate_freedesktop_artifacts(dist_dir)
    app_dir = build.prepare_appdir(dist_dir, root_dir)

    assert (app_dir / "usr" / "bin" / "ImageSorter").exists()
    assert (app_dir / "usr" / "share" / "applications" / "imagesorter.desktop").exists()
    assert (app_dir / "imagesorter.desktop").exists()

    app_run = app_dir / "AppRun"
    assert app_run.exists()
    assert os.access(app_run, os.X_OK)

    desktop_content = (app_dir / "imagesorter.desktop").read_text(encoding="utf-8")
    assert "Icon=imagesorter" in desktop_content
    assert "Exec=ImageSorter %F" in desktop_content
    assert ".isp" not in desktop_content


def test_unverified_appimagetool_is_rejected(tmp_path):
    tool = tmp_path / "appimagetool"
    tool.write_bytes(b"not the pinned upstream binary")

    with pytest.raises(build.BuildError, match="checksum mismatch"):
        build.get_appimagetool_executable(tmp_path, explicit_tool=tool)


def test_appimage_success_without_output_is_failure(tmp_path):
    root_dir = Path(__file__).resolve().parent.parent
    dist_dir = tmp_path / "dist"
    executable = dist_dir / "ImageSorter" / "ImageSorter"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    build.generate_freedesktop_artifacts(dist_dir)
    build.ensure_icon_assets(root_dir)
    runtime = tmp_path / "runtime"
    runtime.write_bytes(b"pinned-test-runtime")

    with patch.object(
        build, "get_appimagetool_executable", return_value=Path("/bin/true")
    ), patch.object(build, "get_appimage_runtime_file", return_value=runtime), patch.object(
        build, "APPIMAGE_RUNTIME_X86_64_SHA256", hashlib.sha256(runtime.read_bytes()).hexdigest()
    ):
        with pytest.raises(build.BuildError, match="without producing"):
            build.build_appimage(root_dir, dist_dir)


def test_runtime_tampering_is_rejected_without_network_fallback(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.write_bytes(b"modified")
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("Unexpected fallback download"))
    with pytest.raises(build.BuildError, match="checksum mismatch"):
        build.get_appimage_runtime_file(tmp_path, runtime)


def test_appimage_builder_supplies_exact_runtime_and_rejects_changed_prefix(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    dist = tmp_path / "dist"
    executable = dist / "ImageSorter/ImageSorter"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"application")
    build.generate_freedesktop_artifacts(dist)
    runtime = tmp_path / "runtime"
    runtime.write_bytes(b"pinned-runtime")
    monkeypatch.setattr(build, "get_appimage_runtime_file", lambda *_args: runtime)
    monkeypatch.setattr(build, "get_appimagetool_executable", lambda *_args: runtime)
    monkeypatch.setattr(build, "APPIMAGE_RUNTIME_X86_64_SHA256", hashlib.sha256(runtime.read_bytes()).hexdigest())
    def tool(command, **_kwargs):
        assert command[1] == "--runtime-file"
        snapshot = Path(command[2])
        assert snapshot != runtime and snapshot.read_bytes() == b"pinned-runtime"
        assert snapshot.stat().st_mode & 0o222 == 0
        Path(command[-1]).write_bytes(snapshot.read_bytes() + b"squashfs")
    def normalize(path, input_file, digest):
        assert input_file == runtime
        assert digest == hashlib.sha256(runtime.read_bytes()).hexdigest()
        assert path.read_bytes() == runtime.read_bytes() + b"squashfs"
        return {"verified": True}
    monkeypatch.setattr(build.subprocess, "run", tool)
    monkeypatch.setattr(build, "normalize_runtime_digest", normalize)
    artifact = build.build_appimage(root, dist)
    assert artifact.is_file()
    assert json.loads((dist / "appimage-build-inputs.json").read_text())["runtime_prefix_verified"] is True
    def reject(*_args):
        raise build.AppImageFormatError("prefix differs")
    monkeypatch.setattr(build, "normalize_runtime_digest", reject)
    with pytest.raises(build.BuildError, match="prefix differs"):
        build.build_appimage(root, dist)


@pytest.mark.packaging
def test_pyinstaller_executable_smoke(tmp_path):
    """Require decoded-frame readiness and graceful exit, not timeout survival.

    Offscreen CI coverage supplements the separate real xcb/KDE acceptance gate.
    """
    from build_support import source_identity
    source = Path(__file__).resolve().parent.parent
    identity = source_identity(source)
    root_dir = tmp_path / "captured-source"
    for name in identity["source_files"]:
        target = root_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    (root_dir / "_source_identity.json").write_text(json.dumps(identity))
    build_script = root_dir / "build_desktop.py"

    build_res = subprocess.run([sys.executable, str(build_script)], capture_output=True, text=True, cwd=root_dir)
    (tmp_path / "packaging-build.stdout.log").write_text(build_res.stdout)
    (tmp_path / "packaging-build.stderr.log").write_text(build_res.stderr)
    assert build_res.returncode == 0, f"desktop build failed:\nSTDOUT:\n{build_res.stdout}\nSTDERR:\n{build_res.stderr}"

    exe_name = "ImageSorter.exe" if sys.platform == "win32" else "ImageSorter"
    exe_path = root_dir / "dist" / "ImageSorter" / exe_name
    assert exe_path.exists(), f"Executable not found at {exe_path}"

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    from PIL import Image
    source = tmp_path / "readiness.png"
    Image.new("RGB", (160, 100), (33, 88, 144)).save(source)
    profile = tmp_path / "profile"
    receipt = profile / "state" / "readiness.json"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    command = [str(exe_path), "--profile-root", str(profile), "--diagnostic-receipt", str(receipt),
               "--acceptance-exit-after-ready", str(source)]
    proc = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        _stdout, stderr = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        # No writer is force-killed: this process is only the disposable GUI.
        proc.terminate()
        try:
            proc.communicate(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=3)
        pytest.fail("Frozen application never reported readiness and graceful close within 30 seconds")
    assert proc.returncode == 0, stderr
    observed = json.loads(receipt.read_text())
    ready = next(item for item in observed["events"] if item["event"] == "image_presented")
    closed = next(item for item in observed["events"] if item["event"] == "gui_shutdown")
    assert ready["backend"] == "offscreen"
    assert ready["filepath"] == str(source)
    assert (ready["width"], ready["height"]) == (160, 100)
    assert Path(ready["module_origin"]).is_relative_to(exe_path.parent)
    assert ready["build_identity"]["source_sha"]
    assert closed["monotonic"] >= ready["monotonic"]
    assert closed["elapsed_ms"] <= 6000
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
