#!/usr/bin/env python3
"""Prove a packaged journal owner survives GUI exit and AppImage unmount.

Only new private fixtures/profiles are accepted. The probe retains one explicit
client, closes the GUI normally, performs a paired copy through the surviving
owner, then disconnects and observes its exit. No mutation process is killed.
This is partial evidence, not the complete native qualification matrix.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from imagesorter.worker_protocol import decode, encode, service_address, verify_peer


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def reference(path):
    return {"path": str(Path(path).resolve()), "sha256": digest(path)}


def wait_for_mount_teardown(mount, *, timeout=5.0, mountinfo=Path("/proc/self/mountinfo"),
                            monotonic=time.monotonic, sleep=time.sleep):
    """Observe both kernel unmount and directory removal; never force either."""
    if isinstance(timeout, bool) or not 0 < timeout <= 5.0:
        raise ValueError("Unmount observation deadline must be in (0, 5] seconds")
    started = monotonic()
    observation = {"mount": str(mount) if mount else None, "deadline_ms": round(timeout * 1000),
                   "required": mount is not None, "observations": [], "passed": mount is None}
    if mount is None:
        observation["elapsed_ms"] = 0
        return observation
    try:
        while True:
            mounted = False
            for line in mountinfo.read_text().splitlines():
                fields = line.split()
                if len(fields) < 6:
                    raise ValueError("Malformed kernel mount record")
                target = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4])
                mounted |= target == str(mount)
            facts = {"elapsed_ms": round((monotonic() - started) * 1000),
                     "kernel_mount_present": mounted, "directory_present": mount.exists()}
            observation["observations"].append(facts)
            if not mounted and not facts["directory_present"]:
                observation["passed"] = True
                break
            remaining = timeout - (monotonic() - started)
            if remaining <= 0:
                break
            sleep(min(0.025, remaining))
    except (OSError, ValueError) as exc:
        observation["error"] = f"{type(exc).__name__}: {exc}"
    observation["elapsed_ms"] = round((monotonic() - started) * 1000)
    return observation


def runtime_observation(kind, artifact, service_pid, module_origin, profile, *, proc_root=Path("/proc")):
    """Observe the installed-wheel or frozen service without inventing pinning."""
    process = proc_root / str(service_pid)
    executable = (process / "exe").resolve(strict=True)
    facts = {"mode": "installed-wheel" if kind == "wheel" else "frozen-pinned",
             "service_executable": str(executable), "checks": {}}
    if kind != "wheel":
        facts["checks"]["independent_pinned_runtime"] = executable.is_relative_to(
            (profile / "data" / "ImageSorter" / "runtime").resolve())
        return facts

    # The final wheel gate deliberately uses a dedicated non-editable venv.
    # An /usr/bin/env trampoline or editable checkout is not equivalent evidence.
    artifact = artifact.resolve(strict=True)
    with artifact.open("rb") as handle:
        first_line = handle.readline(4097)
    if len(first_line) > 4096 or not first_line.startswith(b"#!/"):
        raise ValueError("Wheel drain requires a bounded absolute interpreter shebang")
    interpreter = Path(os.fsdecode(first_line[2:].strip()))
    if not interpreter.is_absolute() or not interpreter.is_file() or interpreter.parent != artifact.parent:
        raise ValueError("Wheel launcher must use its own installed venv interpreter")
    runtime = artifact.parent.parent
    configuration = runtime / "pyvenv.cfg"
    if artifact.parent.name != "bin" or not configuration.is_file():
        raise ValueError("Wheel drain requires a dedicated installed virtual environment")
    origin = Path(module_origin).resolve(strict=True)
    relative = origin.relative_to(runtime)
    if (origin.name != "__init__.py" or origin.parent.name != "imagesorter" or
            not any(part in {"site-packages", "dist-packages"} for part in relative.parts)):
        raise ValueError("Wheel package origin is not the installed imagesorter package")
    command = [os.fsdecode(part) for part in (process / "cmdline").read_bytes().split(b"\0") if part]
    environment = dict(part.split(b"=", 1) for part in (process / "environ").read_bytes().split(b"\0") if b"=" in part)
    expected = [str(interpreter), "-m", "imagesorter.bootstrap", "--mutation-service", "--journal",
                str(profile / "data" / "ImageSorter" / "operation_journal.db")]
    facts.update(runtime_root=str(runtime), interpreter=str(interpreter), package_origin=str(origin),
                 service_command=command, files=[reference(executable), reference(configuration), reference(origin)])
    facts["checks"].update({"installed_interpreter_matches_service": interpreter.resolve(strict=True) == executable,
                           "installed_package_origin": True,
                           "installed_service_command": command[:len(expected)] == expected,
                           "no_python_import_override": not environment.get(b"PYTHONPATH") and not environment.get(b"PYTHONHOME")})
    return facts


def run(artifact, output, backend, kind="onedir"):
    artifact = artifact.resolve(strict=True)
    if output.exists():
        raise ValueError("Evidence directory must be new")
    output.mkdir(parents=True, mode=0o700)
    profile = output / "profile"
    profile.mkdir(mode=0o700)
    source = output / "fixture.jpg"
    Image.new("RGB", (160, 100), "purple").save(source)
    sidecar = Path(str(source) + ".txt")
    sidecar.write_text("Human note: preserve this exact companion\n")
    destination = output / "destination"
    destination.mkdir()
    original = [reference(source), reference(sidecar)]
    diagnostic = profile / "state" / "diagnostics.json"
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "IMAGESORTER_PROFILE_ROOT"):
        environment.pop(key, None)
    environment["QT_QPA_PLATFORM"] = backend
    stdout = (output / "stdout.log").open("wb")
    stderr = (output / "stderr.log").open("wb")
    process = subprocess.Popen([str(artifact), "--profile-root", str(profile), "--diagnostic-receipt", str(diagnostic), str(source)],
                               cwd=output, env=environment, stdout=stdout, stderr=stderr, start_new_session=True)
    connection = None
    service_pid = gui_pid = None
    result = {"schema_version": 1, "kind": "packaged-owner-drain-probe", "complete": False,
              "artifact": reference(artifact), "profile_root": str(profile), "fixtures_before": original,
              "observed_at": time.time(), "artifact_kind": kind, "checks": {}}
    received = b""

    def messages_until(predicate, timeout=30):
        nonlocal received
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b"\n" in received:
                line, received = received.split(b"\n", 1)
                message = decode(line)
                if predicate(message):
                    return message
            try:
                data = connection.recv(65536)
            except TimeoutError:
                continue
            if not data:
                raise RuntimeError("Surviving journal owner disconnected unexpectedly")
            received += data
        raise TimeoutError("Journal owner response deadline exceeded; private files preserved")

    try:
        deadline = time.monotonic() + 40
        ready = None
        while time.monotonic() < deadline:
            if diagnostic.exists():
                document = json.loads(diagnostic.read_text())
                ready = next((item for item in document["events"] if item["event"] == "mutation_service_ready"), None)
                painted = next((item for item in document["events"] if item["event"] == "image_presented"), None)
                if ready and painted:
                    gui_pid, service_pid = document["pid"], ready["pid"]
                    break
            if process.poll() is not None:
                raise RuntimeError("Packaged GUI exited before readiness")
            time.sleep(0.05)
        if not ready or service_pid is None:
            raise TimeoutError("Packaged journal owner did not become ready")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(0.5)
        connection.connect(service_address(str(profile / "data" / "ImageSorter" / "operation_journal.db")))
        verify_peer(connection)
        connection.sendall(encode({"type": "hello"}))
        service = messages_until(lambda row: row.get("type") == "ready")
        service_pid = service["pid"]
        runtime = runtime_observation(kind, artifact, service_pid, painted["module_origin"], profile)
        result.update(gui_pid=gui_pid, service_pid=service_pid, runtime=runtime,
                      build_identity=document.get("build_identity"), module_origin=painted["module_origin"])
        result["checks"]["actual_backend_and_paint"] = painted["backend"] == backend and painted["filepath"] == str(source)
        result["checks"].update(runtime["checks"])
        if kind != "wheel":
            result["pinned_executable"] = runtime["service_executable"]
        started = time.monotonic()
        os.kill(gui_pid, signal.SIGTERM)
        process.wait(timeout=8)
        result["gui_process_exit_ms"] = round((time.monotonic() - started) * 1000)
        result["checks"]["gui_bounded_exit"] = result["gui_process_exit_ms"] <= 5000 and process.returncode == 0
        result["checks"]["owner_survived_gui_exit"] = Path(f"/proc/{service_pid}/exe").exists()
        origin = Path(painted["module_origin"])
        mount = next((parent for parent in origin.parents if parent.name.startswith(".mount_")), None)
        result["appimage_mount"] = str(mount) if mount else None
        result["mount_teardown"] = wait_for_mount_teardown(mount)
        result["checks"]["appimage_mount_closed"] = result["mount_teardown"]["passed"]
        request = {"operation_id": "drain-probe-" + uuid.uuid4().hex, "action": "copy", "source_path": str(source),
                   "destination_path": str(destination), "task_options": {}}
        connection.sendall(encode({"type": "submit", "request": request}))
        terminal = messages_until(lambda row: row.get("type") == "result" and row["result"].get("operation_id") == request["operation_id"])["result"]
        result["operation_result"] = terminal
        result["checks"]["durable_operation_after_gui_exit"] = terminal["state"] == "completed"
        copied = Path(terminal["destination_path"])
        result["checks"]["exact_pair_and_originals"] = (digest(copied), digest(Path(str(copied) + ".txt"))) == (original[0]["sha256"], original[1]["sha256"]) and original == [reference(source), reference(sidecar)]
        if kind == "wheel":
            result["checks"]["installed_runtime_files_unchanged"] = all(reference(item["path"]) == item for item in runtime["files"])
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if connection:
            connection.close()
        if process.poll() is None and gui_pid:
            try:
                os.kill(gui_pid, signal.SIGTERM)
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                pass  # Preserve evidence and never kill a mutation owner.
        deadline = time.monotonic() + 10
        while service_pid and Path(f"/proc/{service_pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        result["checks"]["owner_exited_after_disconnect"] = bool(service_pid and not Path(f"/proc/{service_pid}").exists())
        stdout.close()
        stderr.close()
        result["diagnostic"] = reference(diagnostic) if diagnostic.exists() else None
        result["fixtures_after"] = [reference(source), reference(sidecar)]
        result["passed"] = not result.get("error") and all(result["checks"].values())
        (output / "drain.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=["xcb", "offscreen"], default="xcb")
    parser.add_argument("--kind", choices=["wheel", "onedir", "appimage"], default="onedir")
    args = parser.parse_args()
    result = run(args.artifact, args.output.resolve(), args.backend, args.kind)
    print(json.dumps({"passed": result["passed"], "receipt": str(args.output / "drain.json"), "complete": False}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
