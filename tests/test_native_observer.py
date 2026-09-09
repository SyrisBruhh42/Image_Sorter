"""Observer elevation is opt-in, descriptor-only, and never application authority."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def observer():
    spec = importlib.util.spec_from_file_location("native_observer_test", Path(__file__).parents[1] / "scripts" / "native_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_multiplexed_trace_preserves_per_pid_exit_and_attempts(observer, tmp_path):
    raw = tmp_path / "raw.log"
    raw.write_text('123 1780000000.000001 socket(AF_INET, SOCK_STREAM, 0) = 4\n'
                   '[pid 124] 1780000000.000002 execve("/bin/true", [], []) = 0\n'
                   '123 1780000000.000003 +++ exited with 0 +++\n'
                   '124 1780000000.000004 +++ exited with 0 +++\n')
    assert observer.split_multiplexed_trace(raw, tmp_path) == []
    telemetry = observer.summarize_trace(sorted(tmp_path.glob("process-trace.*")))
    assert telemetry["process_ids"] == [123, 124]
    assert telemetry["network_attempts"][0]["pid"] == 123
    assert "+++ exited with 0 +++" in (tmp_path / "process-trace.124").read_text()


def test_multiplexed_trace_unparsed_output_cannot_silently_pass(observer, tmp_path):
    raw = tmp_path / "raw.log"
    raw.write_text("unexpected observer output\n")
    assert observer.split_multiplexed_trace(raw, tmp_path) == ["unexpected observer output"]


@pytest.mark.skipif(__import__("sys").platform != "linux", reason="Linux native observer")
def test_privileged_observer_only_writes_inherited_fd_and_drops_application_uid(observer, tmp_path, monkeypatch):
    import pwd
    monkeypatch.setattr(observer.os, "getuid", lambda: 1234)
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_dir="/home/test-user", pw_name="test-user"))
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda path, *a, **kw: SimpleNamespace(st_mode=0o100755, st_uid=0)
                        if str(path) in {"/usr/bin/strace", "/usr/bin/pkexec", "/usr/bin/env"}
                        else original_stat(path, *a, **kw))
    command = observer.privileged_trace_command(
        ["/usr/bin/strace", "-ff", "-o", str(tmp_path / "unsafe-root-output"), "--", "/private/ImageSorter.AppImage"],
        {"IMAGESORTER_PROFILE_ROOT": str(tmp_path / "profile"), "DISPLAY": ":0", "LD_PRELOAD": "/injected.so"})
    assert command[:5] == ["/usr/bin/pkexec", "--disable-internal-agent", "/usr/bin/strace", "-u", "test-user"]
    assert command[command.index("-o") + 1] == "/proc/self/fd/1"
    assert "-ff" not in command and "_trace_child" in command
    assert "LD_PRELOAD=/injected.so" not in command
    assert "HOME=/home/test-user" in command


def test_trace_child_rejects_arbitrary_launch_shape(observer):
    with pytest.raises(ValueError, match="ordinary desktop user"):
        observer.trace_child(["/bin/sh", "-c", "arbitrary"])
