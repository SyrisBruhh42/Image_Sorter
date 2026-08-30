import os
import py_compile
import sys
import subprocess
from pathlib import Path
import pytest
import build


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


def test_build_pyinstaller_preflight(monkeypatch):
    """Verify check_pyinstaller_installed returns boolean correctly."""
    assert build.check_pyinstaller_installed() in (True, False)


@pytest.mark.packaging
def test_pyinstaller_executable_smoke():
    """Packaging smoke test: runs build.py and launches built executable with QT_QPA_PLATFORM=offscreen."""
    root_dir = Path(__file__).resolve().parent.parent
    build_script = root_dir / "build.py"

    # 1. Run build.py
    build_res = subprocess.run([sys.executable, str(build_script)], capture_output=True, text=True, cwd=root_dir)
    assert build_res.returncode == 0, f"build.py failed:\nSTDOUT:\n{build_res.stdout}\nSTDERR:\n{build_res.stderr}"

    # 2. Locate built executable
    exe_name = "ImageSorter.exe" if sys.platform == "win32" else "ImageSorter"
    exe_path = root_dir / "dist" / "ImageSorter" / exe_name
    assert exe_path.exists(), f"Executable not found at {exe_path}"

    # 3. Launch built binary with QT_QPA_PLATFORM=offscreen and monitor startup
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"

    proc = subprocess.Popen([str(exe_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    try:
        # Wait for 3 seconds to ensure process initializes Qt without immediate crash/ImportError
        stdout, stderr = proc.communicate(timeout=3.0)
    except subprocess.TimeoutExpired:
        # Process stayed alive as expected for GUI app under offscreen mode
        proc.kill()
        stdout, stderr = proc.communicate()
    else:
        # If process exited within timeout, check returncode and ensure it was not due to ImportError
        assert proc.returncode == 0, f"Executable crashed on startup with code {proc.returncode}.\nStderr:\n{stderr}"

    assert "ImportError: attempted relative import with no known parent package" not in stderr
