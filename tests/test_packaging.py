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


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="AppRun is a Linux package launcher")
@pytest.mark.parametrize("selection", ["extracted", "appimage", "native", "explicit", "inherited", "invalid"])
def test_generated_apprun_preserves_storage_identity(tmp_path, selection):
    """Run the generated launcher with a disposable frozen-layout Python probe."""
    root = tmp_path / "fixture source"
    icon = root / "src/imagesorter/resources/imagesorter.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"icon fixture")
    dist = tmp_path / "USB with spaces"
    executable = dist / "ImageSorter/ImageSorter"
    executable.parent.mkdir(parents=True)
    executable.write_text(f"#!{sys.executable}\n" + """
import json, os, sys
from pathlib import Path
sys.frozen = True
sys.executable = str(Path(__file__).resolve())
from imagesorter import paths
print(json.dumps({"root": str(paths.get_portable_root()), "settings": str(paths.get_settings_path()),
                  "appdir": os.environ.get("APPDIR"), "origin": paths.__file__,
                  "explicit": os.environ.get("IMAGESORTER_PROFILE_ROOT"),
                  "inherited": os.environ.get("IMAGESORTER_PORTABLE_ROOT"), "args": sys.argv[1:]}))
""")
    executable.chmod(0o700)
    build.generate_freedesktop_artifacts(dist)
    appdir = build.prepare_appdir(dist, root)
    environment = os.environ.copy()
    for key in ("APPDIR", "APPIMAGE", "IMAGESORTER_PROFILE_ROOT", "IMAGESORTER_PORTABLE_ROOT"):
        environment.pop(key, None)
    source = Path(__file__).resolve().parents[1] / "src"
    environment.update(PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1")
    host = tmp_path / "native state"
    for category in ("CONFIG", "DATA", "CACHE", "STATE"):
        environment[f"XDG_{category}_HOME"] = str(host / category.lower())
    expected_root = appdir
    if selection != "native":
        (appdir / "portable.flag").touch()
    if selection == "appimage":
        artifact = dist / "Image Sorter.AppImage"
        artifact.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02fixture")
        environment.update(APPIMAGE=str(artifact), APPDIR=str(appdir))
        (dist / "portable.flag").touch()
        expected_root = dist
    elif selection in {"explicit", "inherited"}:
        selected = tmp_path / "selected profile"
        selected.mkdir()
        key = "IMAGESORTER_PROFILE_ROOT" if selection == "explicit" else "IMAGESORTER_PORTABLE_ROOT"
        environment[key] = str(selected)
        # Existing identity wins even when inherited package metadata is stale.
        environment.update(APPDIR="stale-mount", APPIMAGE="stale-artifact")
        expected_root = selected
    elif selection == "invalid":
        environment["APPDIR"] = "invalid-mount"
    cwd = tmp_path / "unrelated cwd"
    cwd.mkdir()
    result = subprocess.run([str(appdir / "AppRun"), "image with spaces.png"], cwd=cwd,
                            env=environment, text=True, capture_output=True, timeout=15)
    if selection == "invalid":
        assert result.returncode != 0 and "package identity" in result.stderr
        assert not host.exists()
        return
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert Path(observed["origin"]).resolve() == source / "imagesorter/paths.py"
    assert observed["args"] == ["image with spaces.png"]
    assert observed["explicit"] == environment.get("IMAGESORTER_PROFILE_ROOT")
    assert observed["inherited"] == environment.get("IMAGESORTER_PORTABLE_ROOT")
    if selection == "native":
        assert observed["root"] == "None"
        assert observed["settings"] == str(host / "config/ImageSorter/settings.json")
    elif selection == "explicit":
        assert observed["root"] == "None"
        assert observed["settings"] == str(expected_root / "config/ImageSorter/settings.json")
    else:
        assert observed["root"] == str(expected_root)
        assert observed["settings"] == str(expected_root / "settings.json")
    if selection != "native":
        assert not host.exists()


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

    build_res = subprocess.run([sys.executable, str(build_script), '--portable'], capture_output=True, text=True, cwd=root_dir)
    (tmp_path / "packaging-build.stdout.log").write_text(build_res.stdout)
    (tmp_path / "packaging-build.stderr.log").write_text(build_res.stderr)
    assert build_res.returncode == 0, f"desktop build failed:\nSTDOUT:\n{build_res.stdout}\nSTDERR:\n{build_res.stderr}"

    from scripts.portable_package import runtime_layout
    runtime, relative_executable = runtime_layout(root_dir / 'dist', sys.platform)
    exe_path = runtime / relative_executable
    assert exe_path.exists(), f"Executable not found at {exe_path}"

    from PIL import Image
    image_path = tmp_path / "readiness.png"
    Image.new("RGB", (160, 100), (33, 88, 144)).save(image_path)
    before = hashlib.sha256(image_path.read_bytes()).hexdigest()
    launches = []
    if sys.platform.startswith('linux'):
        # Linux windowed binaries report the refusal on stderr. Native Windows
        # and macOS error-dialog behavior needs its separate interactive gate.
        launches.append(('missing-marker', exe_path, runtime))
    launches.append(("explicit", exe_path, tmp_path / "explicit-profile"))
    portable_root = runtime.parent if sys.platform == 'darwin' else runtime
    launches.append(("portable-bundle" if sys.platform == 'darwin' else "portable-onedir", exe_path, portable_root))
    if sys.platform.startswith("linux"):
        appdir = build.prepare_appdir(root_dir / "dist", root_dir)
        launches.append(("portable-appdir", appdir / "AppRun", appdir))

    for mode, launcher, profile in launches:
        env = os.environ.copy()
        for key in ("IMAGESORTER_PROFILE_ROOT", "IMAGESORTER_PORTABLE_ROOT", "APPIMAGE", "APPDIR"):
            env.pop(key, None)
        host = tmp_path / f"host-{mode}"
        host.mkdir()
        env.update(QT_QPA_PLATFORM="offscreen", HOME=str(host), USERPROFILE=str(host))
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                    "APPDATA", "LOCALAPPDATA", "TMPDIR", "TMP", "TEMP"):
            location = host / key
            location.mkdir()
            env[key] = str(location)
        sentinel = host / "XDG_CONFIG_HOME/QtProject.conf"
        sentinel.write_text("[sentinel]\nvalue=unchanged\n")
        host_before = {str(p.relative_to(host)): p.read_bytes() for p in host.rglob("*") if p.is_file()}
        receipt = profile / "state" / "readiness.json"
        command = [str(launcher)]
        if mode == "explicit":
            command += ["--profile-root", str(profile)]
        elif mode != 'missing-marker':
            (profile / "portable.flag").touch()
        command += ["--diagnostic-receipt", str(receipt), "--acceptance-exit-after-ready", str(image_path)]
        proc = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            # No writer is force-killed: this process is only the disposable GUI.
            proc.terminate()
            try:
                proc.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=3)
            pytest.fail(f"Frozen {mode} never reported readiness and graceful close within 30 seconds")
        (tmp_path / f"packaging-{mode}.stdout.log").write_text(stdout)
        (tmp_path / f"packaging-{mode}.stderr.log").write_text(stderr)
        if mode == 'missing-marker':
            assert proc.returncode != 0 and 'Portable build requires' in stderr
            assert not receipt.exists()
            assert not (profile / 'config').exists()
            assert {str(p.relative_to(host)): p.read_bytes() for p in host.rglob('*') if p.is_file()} == host_before
            continue
        assert proc.returncode == 0, f"{mode}: {stderr}"
        observed = json.loads(receipt.read_text())
        ready = next(item for item in observed["events"] if item["event"] == "image_presented")
        closed = next(item for item in observed["events"] if item["event"] == "gui_shutdown")
        assert ready["backend"] == "offscreen"
        assert ready["filepath"] == str(image_path)
        assert (ready["width"], ready["height"]) == (160, 100)
        code_root = launcher.parent / "usr/bin" if mode == "portable-appdir" else runtime
        assert Path(ready["module_origin"]).is_relative_to(code_root)
        assert ready["build_identity"] == identity
        assert closed["monotonic"] >= ready["monotonic"]
        assert closed["elapsed_ms"] <= 6000
        assert hashlib.sha256(image_path.read_bytes()).hexdigest() == before
        host_after = {str(p.relative_to(host)): p.read_bytes() for p in host.rglob("*") if p.is_file()}
        assert host_after == host_before, f"{mode} wrote host-profile files"
        if mode != "explicit":
            assert (profile / "config").is_dir()
            assert (profile / "cache/temporary").is_dir()
            if code_root != profile:
                assert not (code_root / "config").exists()

    from scripts.qualify_portable_package import qualify
    packages = list((root_dir / 'dist').glob('*-portable/portable-package.json'))
    assert len(packages) == 1
    package = packages[0].parent
    extracted = tmp_path / 'extracted-portable'
    if sys.platform == 'win32':
        import zipfile
        with zipfile.ZipFile(package.with_name(package.name + '.zip')) as archive:
            archive.extractall(extracted)
    else:
        import tarfile
        with tarfile.open(package.with_name(package.name + '.tar.gz')) as archive:
            archive.extractall(extracted, filter='data')
    result = qualify(extracted / package.name, tmp_path / 'portable-qualification', identity)
    assert result['status'] == 'PASS_PORTABLE_FROZEN_SMOKE'
    assert len(result['launches']) == 2
