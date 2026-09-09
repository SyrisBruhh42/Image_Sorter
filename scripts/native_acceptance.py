#!/usr/bin/env python3
"""Evidence-producing installed-artifact smoke; never a substitute for native cases.

All GUI routes use an explicit profile argument, including generated Dolphin
launchers. Network attempts are measured for the process tree, not system-wide.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def evidence(path):
    return {"path": str(Path(path).resolve()), "sha256": digest(path)}


def write_json(path, data):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def valid_origin(origin, artifact, kind):
    origin, artifact = Path(origin), Path(artifact).resolve()
    if "src/imagesorter" in str(origin):
        return False
    if kind == "wheel":
        return "site-packages" in origin.parts and origin.is_relative_to(artifact.parent.parent)
    if kind == "onedir" or artifact.name == "AppRun":
        return origin.is_relative_to(artifact.parent)
    # AppImage mount paths are ephemeral. The process receipt proves its own
    # origin; a traced exec chain binds it to the hashed input artifact.
    return str(origin).startswith("/tmp/.mount_") and "imagesorter" in origin.parts


def summarize_trace(paths):
    attempts, model_opens, process_ids = [], [], []
    for path in paths:
        try:
            process_id = int(path.name.rsplit(".", 1)[1])
        except ValueError:
            continue
        process_ids.append(process_id)
        for line in path.read_text(errors="replace").splitlines():
            if (re.search(r"socket\(AF_INET6?\b", line) or
                    (re.search(r"\b(connect|sendto|sendmsg|sendmmsg)\(", line) and
                     re.search(r"AF_INET6?\b|<(?:TCP|UDP):", line))):
                attempts.append({"pid": process_id, "trace": str(path), "syscall": line})
            if re.search(r"\b(open|openat|openat2)\(", line) and re.search(r"\.(onnx|safetensors|pt|pth)(?:\"|')", line):
                model_opens.append({"pid": process_id, "trace": str(path), "syscall": line})
    return {"process_ids": sorted(process_ids), "network_attempts": attempts, "model_open_attempts": model_opens}


def desktop_quote(value):
    # Desktop Entry Exec is NOT shell syntax. Escape reserved characters within
    # double quotes and double percent signs before the single explicit %F.
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%") + '"'


def privileged_trace_command(traced, environment):
    """Elevate only the system observer; its fixed child drops to this user.

    No shell, arbitrary observer options, inherited loader injection, global
    ptrace setting or application privilege is involved. Authentication is
    exclusively through the desktop authorization agent.
    """
    import pwd
    import stat
    if sys.platform != "linux" or os.getuid() == 0:
        raise ValueError("Privileged observation requires a non-root Linux desktop user")
    for name in ("/usr/bin/pkexec", "/usr/bin/strace", "/usr/bin/env"):
        path = Path(name)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Observer executables must be root-owned system files without group/world write access")
    if Path(traced[0]).resolve() != Path("/usr/bin/strace").resolve():
        raise ValueError("Privileged observation permits only the system strace executable")
    user = pwd.getpwuid(os.getuid())
    allowed = {"DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
               "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "LANG", "LC_ALL", "QT_QPA_PLATFORM",
               "IMAGESORTER_PROFILE_ROOT", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
               "ONNXRUNTIME_DISABLE_TELEMETRY", "DO_NOT_TRACK"}
    child_environment = {key: value for key, value in environment.items() if key in allowed}
    child_environment.update(HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name, PATH="/usr/bin:/bin")
    # Locate the explicit delimiter instead of interpreting application arguments.
    boundary = traced.index("--")
    observer_options = list(traced[1:boundary])
    observer_options[observer_options.index("-ff")] = "-f"
    # Root must not create files through a caller-writable pathname. The caller
    # already opened stdout; the observer writes only through that inherited FD.
    observer_options[observer_options.index("-o") + 1] = "/proc/self/fd/1"
    output = Path(environment["IMAGESORTER_PROFILE_ROOT"]).parent
    child = [sys.executable, str(Path(__file__).resolve()), "_trace_child",
             str(output / "application-stdout.log"), str(output / "application-stderr.log"), *traced[boundary + 1:]]
    return ["/usr/bin/pkexec", "--disable-internal-agent", "/usr/bin/strace", "-u", user.pw_name,
            *observer_options, "--", "/usr/bin/env", "-i",
            *(f"{key}={value}" for key, value in sorted(child_environment.items())), *child]


def trace_child(arguments):
    """Unprivileged fixed launch adapter; not an application feature."""
    if os.getuid() == 0 or len(arguments) != 8:
        raise ValueError("The trace launch adapter must run as the ordinary desktop user")
    stdout_path, stderr_path, *command = arguments
    profile = Path(command[2])
    if (command[1] != "--profile-root" or command[3] != "--diagnostic-receipt" or
            not profile.is_absolute() or profile != profile.resolve() or
            Path(command[4]) != profile / "state" / "readiness.json" or
            Path(command[5]).parent != profile.parent / "fixtures" or
            Path(stdout_path) != profile.parent / "application-stdout.log" or
            Path(stderr_path) != profile.parent / "application-stderr.log" or
            Path(command[0]) != Path(command[0]).resolve()):
        raise ValueError("Unexpected disposable trace launch arguments")
    for stream, path in ((1, stdout_path), (2, stderr_path)):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.dup2(descriptor, stream)
        os.close(descriptor)
    os.execve(command[0], command, os.environ)


def split_multiplexed_trace(path, output):
    """Retain raw observer output and derive ordinary user-owned PID records."""
    records, unparsed = {}, []
    for line in Path(path).read_text(errors="replace").splitlines():
        match = re.fullmatch(r"(?:\[pid\s+)?(\d+)\]?\s+(.*)", line)
        if not match:
            if line.strip():
                unparsed.append(line)
            continue
        records.setdefault(int(match[1]), []).append(match[2])
    for pid, lines in records.items():
        target = Path(output) / f"process-trace.{pid}"
        with target.open("x", encoding="utf-8") as destination:
            destination.write("\n".join(lines) + "\n")
    return unparsed


def run(options):
    artifact = Path(options.artifact).resolve()
    if not artifact.is_file() or not os.access(artifact, os.X_OK):
        raise ValueError("--artifact must be an existing executable (wheel entry point, onedir executable, AppImage or extracted AppRun)")
    artifact_before = evidence(artifact)
    output = Path(options.output).resolve() if options.output else Path(tempfile.mkdtemp(prefix="imagesorter-native-"))
    output.mkdir(parents=True, exist_ok=True)
    profile = output / "profile"
    if profile.exists():
        raise ValueError("Evidence directory already has a profile; use a new output directory for every first launch")
    for category in ("config", "data", "cache", "state"):
        (profile / category).mkdir(parents=True, mode=0o700)
    fixtures = output / "fixtures"
    fixtures.mkdir(exist_ok=True)
    from PIL import Image
    image_path = fixtures / "sample-1.jpg"
    Image.new("RGB", (160, 100), "#347a92").save(image_path)
    sidecar = Path(str(image_path) + ".txt")
    sidecar.write_text("Human acceptance note\n", encoding="utf-8")
    before = [evidence(image_path), evidence(sidecar)]
    write_json(output / "fixtures-before.json", before)
    receipt_path = profile / "state" / "readiness.json"
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "IMAGESORTER_PROFILE_ROOT", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    environment.update({"QT_QPA_PLATFORM": options.backend, "IMAGESORTER_PROFILE_ROOT": str(profile),
                        "ONNXRUNTIME_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"})
    for category in ("CONFIG", "DATA", "CACHE", "STATE"):
        environment[f"XDG_{category}_HOME"] = str(profile / category.lower())
    command = [str(artifact), "--profile-root", str(profile), "--diagnostic-receipt", str(receipt_path), str(image_path)]
    # This launcher carries isolation into an ALREADY RUNNING Dolphin process;
    # shell-exported XDG variables alone cannot provide that guarantee.
    launcher = output / "imagesorter-acceptance.desktop"
    launcher.write_text("[Desktop Entry]\nType=Application\nName=Image Sorter disposable acceptance\nTerminal=false\nExec=" +
                        " ".join(desktop_quote(item) for item in command[:-1]) + " %F\n", encoding="utf-8")
    trace_program = shutil.which("strace")
    if not trace_program:
        raise ValueError("strace is required to attribute network/model access and process exit; a missing observer is not a pass")
    trace_root = output / "process-trace"
    traced = [trace_program, "-ff", "-q", "-ttt", "-yy", "-s", "512", "-e", "trace=%network,%process,%file", "-o", str(trace_root), "--", *command]
    privileged = bool(getattr(options, "privileged_trace", False))
    if privileged:
        if options.kind != "appimage" or artifact.name == "AppRun" or options.backend != "xcb":
            raise ValueError("Native authorization is restricted to a normal AppImage xcb diagnostic launch")
        traced = privileged_trace_command(traced, environment)
        print("Desktop authorization requested for this disposable AppImage observer only. The application runs as your normal user.", flush=True)
    started, ready_at = time.monotonic(), None
    observation_started_unix = None
    failure = None
    diagnostic = {}
    observed_uid = None
    termination_sent = False
    with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(traced, cwd=output, env=environment, stdout=stdout, stderr=stderr, start_new_session=True)
        while process.poll() is None:
            if receipt_path.exists():
                diagnostic = json.loads(receipt_path.read_text())
                presented = next((item for item in diagnostic.get("events", []) if item["event"] == "image_presented"), None)
                if presented and ready_at is None:
                    ready_at = time.monotonic()
                    observation_started_unix = time.time()
                    status = Path(f"/proc/{diagnostic['pid']}/status").read_text()
                    uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
                    observed_uid = [int(value) for value in uid_line.split()[1:]]
            now = time.monotonic()
            if ready_at is not None and now - ready_at >= options.observe_seconds and not termination_sent:
                os.kill(diagnostic["pid"], signal.SIGTERM)
                termination_sent = True
            if now - started > options.timeout + options.observe_seconds:
                failure = "Readiness or complete process-tree shutdown deadline exceeded"
                # Only this disposable launch process group is terminated. The
                # independent mutation owner is deliberately never force-killed.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (PermissionError, ProcessLookupError):
                    failure += "; authorization/observer could not be signalled; cancel any pending native prompt"
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    failure += "; observer remains active; preserve profile and inspect recorded PIDs"
                break
            time.sleep(0.05)
    if receipt_path.exists():
        diagnostic = json.loads(receipt_path.read_text())
    if privileged and not diagnostic:
        failure = ((failure + "; ") if failure else "") + "No authorization-backed application launch was observed; not qualified. Cancellation, denial, or observer startup failure cannot be distinguished from readiness alone. Unprivileged extracted-route evidence is separate."
    if privileged:
        unparsed = split_multiplexed_trace(output / "stdout.log", output)
        if unparsed:
            failure = failure or "Privileged observer output contained unparsed lines; raw output preserved, not qualified"
    traces = sorted(output.glob("process-trace.*"))
    telemetry = summarize_trace(traces)
    write_json(output / "telemetry.json", telemetry)
    presented = next((item for item in diagnostic.get("events", []) if item["event"] == "image_presented"), {})
    shutdown = next((item for item in diagnostic.get("events", []) if item["event"] == "gui_shutdown"), {})
    identity_ok = (presented.get("backend") == options.backend and presented.get("width") == 160 and
                   presented.get("height") == 100 and presented.get("filepath") == str(image_path) and
                   valid_origin(presented.get("module_origin", ""), artifact, options.kind))
    files_unchanged = before == [evidence(image_path), evidence(sidecar)]
    write_json(output / "fixtures-after.json", [evidence(image_path), evidence(sidecar)])
    build_identity = presented.get("build_identity") or {}
    write_json(output / "build-identity.json", build_identity)
    build_ok = (build_identity.get("source_sha") == options.source_sha and
                build_identity.get("source_tree") == options.source_tree and not build_identity.get("dirty", True))
    stopped = process.poll() == 0 and bool(traces) and all(
        not Path(f"/proc/{pid}").exists() for pid in telemetry["process_ids"])
    no_models = not telemetry["model_open_attempts"] and not list(profile.rglob("*.onnx"))
    observation_complete = ready_at is not None and time.monotonic() - ready_at >= options.observe_seconds
    artifact_unchanged = artifact_before == evidence(artifact)
    application_unprivileged = observed_uid == [os.getuid()] * 4
    success = (not failure and identity_ok and build_ok and files_unchanged and stopped and artifact_unchanged and application_unprivileged and
               shutdown.get("elapsed_ms", 999999) <= 5000 and no_models and
               not telemetry["network_attempts"] and observation_complete)
    all_evidence = [evidence(path) for path in (output / "stdout.log", output / "stderr.log", output / "telemetry.json", *traces)]
    if receipt_path.exists():
        all_evidence.append(evidence(receipt_path))
    all_evidence.extend(evidence(path) for path in (output / "application-stdout.log", output / "application-stderr.log") if path.exists())
    result = {"schema_version": 1, "source_sha": options.source_sha, "source_tree": options.source_tree,
              "run_id": uuid.uuid4().hex, "effective_profile_root": str(profile),
              "observation_started_unix": observation_started_unix, "observation_ended_unix": time.time(),
              "diagnostic": evidence(receipt_path) if receipt_path.exists() else None,
              "telemetry": evidence(output / "telemetry.json"), "traces": [evidence(path) for path in traces],
              "fixtures_before": evidence(output / "fixtures-before.json"),
              "fixtures_after": evidence(output / "fixtures-after.json"),
              "build_receipt": evidence(output / "build-identity.json"),
              "profile_id": "installed-artifact-smoke", "native": options.backend == "xcb",
              "display_backend": options.backend, "session_type": os.environ.get("XDG_SESSION_TYPE"),
              "desktop": os.environ.get("XDG_CURRENT_DESKTOP"), "environment": {"platform": platform.platform()},
              "artifact": {**evidence(artifact), "kind": options.kind}, "components": [],
              "cases": [{"id": "READINESS-NETWORK-SHUTDOWN", "artifact_kind": options.kind,
                         "status": "passed" if success else "failed", "evidence": all_evidence}],
              "complete": False, "all_processes_stopped": stopped,
              "base_first_launch_network_attempts": len(telemetry["network_attempts"]),
              "first_launch_observation_seconds": options.observe_seconds if observation_complete else None,
              "base_model_open_attempts": len(telemetry["model_open_attempts"]), "no_model_files": no_models,
              "ready_identity_verified": identity_ok, "fixture_hashes_unchanged": files_unchanged,
              "build_identity_verified": build_ok,
              "observer_privilege": "system tracer only; application uses caller uid" if privileged else "unprivileged",
              "application_uid": os.getuid(),
              "observed_application_uids": observed_uid, "application_unprivileged": application_unprivileged,
              "artifact_unchanged": artifact_unchanged,
              "error": failure, "returncode": process.poll()}
    write_json(output / "smoke.json", result)
    print(json.dumps({"passed": success, "receipt": str(output / "smoke.json"), "complete": False}))
    return 0 if success else 1


def aggregate(options):
    """Validate a completed operator-authored matrix before sealing its copy."""
    import cutover
    path = Path(options.matrix).resolve()
    record = cutover.verify_qualification(path, options.source_sha, options.source_tree, options.component)
    output = Path(options.output).resolve()
    # Relative references would change meaning when relocated. Rebase all file
    # references only after the strict validator has checked their current hash.
    for artifact in record["artifacts"]:
        artifact["path"] = str(cutover.checked_file(artifact, path.parent))
        if item := artifact.get("smoke"):
            item["path"] = str(cutover.checked_file(item, path.parent))
        if item := artifact.get("build"):
            item["path"] = str(cutover.checked_file(item, path.parent))
    for component in record["components"]:
        item = component["manifest"]
        item["path"] = str(cutover.checked_file(item, path.parent))
    for case in record["cases"]:
        if item := case.get("observation"):
            item["path"] = str(cutover.checked_file(item, path.parent))
        for item in case["evidence"]:
            item["path"] = str(cutover.checked_file(item, path.parent))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Refusing to replace existing qualification evidence")
    write_json(output, record)
    print(json.dumps({"qualification": str(output), "sha256": digest(output)}))
    return 0


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "_trace_child":
        trace_child(arguments[1:])
        return 1  # execve never returns on success.
    parser = argparse.ArgumentParser(description=__doc__)
    if arguments and arguments[0] == "aggregate":
        parser.add_argument("mode")
        parser.add_argument("--matrix", required=True)
        parser.add_argument("--component", action="append", required=True)
        parser.add_argument("--output", required=True)
    else:
        parser.add_argument("--artifact", required=True)
        parser.add_argument("--kind", choices=("wheel", "onedir", "appimage"), required=True)
        parser.add_argument("--backend", choices=("xcb", "offscreen"), default="xcb")
        parser.add_argument("--output")
        parser.add_argument("--observe-seconds", type=float, default=30)
        parser.add_argument("--timeout", type=float, default=45)
        parser.add_argument("--privileged-trace", action="store_true",
                            help="Explicit native authorization for the normal AppImage observer only; never needed by the application")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    options = parser.parse_args(arguments)
    try:
        if hasattr(options, "mode"):
            return aggregate(options)
        if options.observe_seconds < 0 or options.timeout < 1:
            raise ValueError("Observation and timeout must be nonnegative/positive")
        return run(options)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Acceptance observation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
