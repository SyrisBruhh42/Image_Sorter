"""Observer classification tests; these synthetic records are not qualification."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("native_drain_probe", Path(__file__).parents[1] / "scripts" / "native_drain_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def wheel(tmp_path):
    runtime = tmp_path / "installed"
    binary = runtime / "bin"
    binary.mkdir(parents=True)
    real_python = tmp_path / "system-python"
    real_python.write_bytes(b"synthetic interpreter")
    interpreter = binary / "python"
    interpreter.symlink_to(real_python)
    launcher = binary / "imagesorter"
    launcher.write_text(f"#!{interpreter}\nfrom imagesorter.bootstrap import entry\n")
    (runtime / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    origin = runtime / "lib" / "python3.12" / "site-packages" / "imagesorter" / "__init__.py"
    origin.parent.mkdir(parents=True)
    origin.write_text("# synthetic installed origin\n")
    process = tmp_path / "proc" / "123"
    process.mkdir(parents=True)
    (process / "exe").symlink_to(real_python)
    profile = tmp_path / "profile"
    arguments = [str(interpreter), "-m", "imagesorter.bootstrap", "--mutation-service", "--journal",
                 str(profile / "data" / "ImageSorter" / "operation_journal.db"), "--startup-fd", "9"]
    (process / "cmdline").write_bytes(b"\0".join(part.encode() for part in arguments) + b"\0")
    (process / "environ").write_bytes(b"PYTHONDONTWRITEBYTECODE=1\0")
    return launcher, origin, profile, process


def observe(wheel):
    launcher, origin, profile, process = wheel
    return probe.runtime_observation("wheel", launcher, 123, str(origin), profile, proc_root=process.parent)


def test_installed_wheel_drain_does_not_claim_frozen_pinning(wheel):
    result = observe(wheel)
    assert result["mode"] == "installed-wheel"
    assert all(result["checks"].values())
    assert "independent_pinned_runtime" not in result["checks"]
    assert len(result["files"]) == 3


@pytest.mark.parametrize("override", [b"PYTHONPATH=/checkout/src\0", b"PYTHONHOME=/elsewhere\0"])
def test_wheel_import_override_is_not_qualified(wheel, override):
    (wheel[3] / "environ").write_bytes(override)
    assert observe(wheel)["checks"]["no_python_import_override"] is False


def test_wheel_other_service_interpreter_is_not_qualified(wheel):
    process = wheel[3]
    other = process / "other-python"
    other.write_bytes(b"different")
    (process / "exe").unlink()
    (process / "exe").symlink_to(other)
    assert observe(wheel)["checks"]["installed_interpreter_matches_service"] is False


def test_wheel_wrong_profile_service_is_not_qualified(wheel):
    (wheel[3] / "cmdline").write_bytes(b"python\0-m\0imagesorter.bootstrap\0--mutation-service\0--journal\0/unrelated\0")
    assert observe(wheel)["checks"]["installed_service_command"] is False


def test_wheel_editable_origin_is_rejected(wheel, tmp_path):
    origin = tmp_path / "checkout" / "src" / "imagesorter" / "__init__.py"
    origin.parent.mkdir(parents=True)
    origin.write_text("# not installed\n")
    with pytest.raises(ValueError):
        observe((wheel[0], origin, wheel[2], wheel[3]))


@pytest.mark.parametrize("shebang", ["#!/usr/bin/env python\n", "#!/elsewhere/python\n", "not a launcher\n"])
def test_wheel_ambiguous_launcher_is_rejected(wheel, shebang):
    wheel[0].write_text(shebang)
    with pytest.raises(ValueError, match="interpreter|venv"):
        observe(wheel)


def test_frozen_drain_still_requires_private_pinned_runtime(tmp_path):
    profile = tmp_path / "profile"
    pinned = profile / "data" / "ImageSorter" / "runtime" / "digest" / "ImageSorter"
    pinned.parent.mkdir(parents=True)
    pinned.write_bytes(b"synthetic frozen runtime")
    process = tmp_path / "proc" / "123"
    process.mkdir(parents=True)
    (process / "exe").symlink_to(pinned)
    result = probe.runtime_observation("appimage", tmp_path / "ImageSorter.AppImage", 123, "/tmp/.mount_example/package", profile, proc_root=process.parent)
    assert result["mode"] == "frozen-pinned"
    assert result["checks"] == {"independent_pinned_runtime": True}


def mount_line(mount):
    escaped = str(mount).replace(" ", r"\040")
    return f"100 22 0:80 / {escaped} ro - fuse.ImageSorter ImageSorter ro\n"


@pytest.mark.parametrize("which", ["mount", "directory", "both"])
def test_mount_cleanup_waits_for_both_kernel_and_directory(tmp_path, which):
    mount = tmp_path / ".mount_example space"
    mount.mkdir()
    info = tmp_path / "mountinfo"
    info.write_text(mount_line(mount))
    ticks = [0.0]

    def advance(seconds):
        ticks[0] += seconds
        if ticks[0] >= 0.05:
            if which in {"mount", "both"}:
                info.write_text("")
            if which in {"directory", "both"} and mount.exists():
                mount.rmdir()

    result = probe.wait_for_mount_teardown(mount, timeout=0.1, mountinfo=info,
                                          monotonic=lambda: ticks[0], sleep=advance)
    assert result["passed"] is (which == "both")
    assert result["observations"][0]["kernel_mount_present"] is True
    assert result["observations"][0]["directory_present"] is True
    assert result["elapsed_ms"] == (50 if which == "both" else 100)


def test_mount_read_failure_is_not_success(tmp_path):
    result = probe.wait_for_mount_teardown(tmp_path / ".mount_missing", mountinfo=tmp_path / "absent")
    assert result["passed"] is False and "FileNotFoundError" in result["error"]


def test_malformed_mountinfo_is_not_success(tmp_path):
    info = tmp_path / "mountinfo"
    info.write_text("malformed\n")
    result = probe.wait_for_mount_teardown(tmp_path / ".mount_missing", mountinfo=info)
    assert result["passed"] is False and "Malformed" in result["error"]


def test_nonmounted_route_is_explicitly_not_required():
    assert probe.wait_for_mount_teardown(None) == {
        "mount": None, "deadline_ms": 5000, "required": False,
        "observations": [], "passed": True, "elapsed_ms": 0,
    }


@pytest.mark.parametrize("timeout", [0, -1, 5.01, True, float("nan")])
def test_mount_wait_cannot_exceed_five_seconds(timeout):
    with pytest.raises(ValueError, match="deadline"):
        probe.wait_for_mount_teardown(None, timeout=timeout)
