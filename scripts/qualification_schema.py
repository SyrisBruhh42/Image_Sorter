"""Pure evidence-consistency verifier; does not launch the app or assert observations.

Hashes establish integrity, not an operator's honesty. Automated smoke facts are
recomputed from raw records; interactive facts require attributed observations.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
from datetime import datetime
from pathlib import Path

_trace_spec = importlib.util.spec_from_file_location("imagesorter_qualification_model_trace", Path(__file__).with_name("model_trace.py"))
_model_trace = importlib.util.module_from_spec(_trace_spec)
_trace_spec.loader.exec_module(_model_trace)

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
CASE_CRITERIA = {
    "KDE-01": ("installed_launch", "file_and_folder_arguments"),
    "KDE-02": ("normal_appimage", "dolphin_open_with", "ordered_paths", "desktop_identity"),
    "KDE-03": ("navigation", "zoom_pan", "locked_zoom", "clipping", "zen", "no_stale_frame"),
    "KDE-04": ("move_key", "copy_key", "one_operation_per_key", "auto_advance", "shortcut_precedence"),
    "KDE-05": ("move_pair", "copy_pair", "trash_pair", "collision_no_overwrite", "no_orphans"),
    "KDE-06": ("undo_move", "undo_copy", "undo_trash", "changed_target_refused", "unrelated_bytes_preserved"),
    "KDE-07": ("unwritable_destination", "disconnected_destination", "source_set_intact", "no_false_success"),
    "KDE-08": ("gui_disconnect_drain", "owner_crash", "exclusive_recovery", "ambiguous_claims_preserved", "second_restart_idempotent"),
    "KDE-09": ("settings_roundtrip", "corrupt_backup", "validated_defaults", "real_profile_untouched"),
    "KDE-10": ("base_without_models", "explicit_install", "cpu_inference", "honest_provider_fallback"),
    "KDE-11": ("base_stills", "missing_pack_diagnostic", "installed_formats", "paused_animation", "viewport_and_generation"),
    "KDE-12": ("normal_maximized_fullscreen", "scale_factors", "monitor_layout", "visible_controls_and_dialogs"),
    "KDE-13": ("keyboard_navigation", "focus", "labels_tooltips", "themes", "large_font"),
    "KDE-14": ("stalled_read", "stalled_provider", "model_validation", "download_cancel", "queued_mutations", "durable_replay"),
}
COMPONENT_CRITERIA = ("explicit_install", "malformed_input", "cancellation", "disable", "uninstall", "reinstall", "rollback")
FORMAT_COVERAGE = {
    "codec.heif-avif": {"heic", "heif", "avif"},
    "codec.camera-raw": {"cr2", "nef", "arw", "dng", "orf", "rw2", "pef", "raf", "srw"},
    "viewer.animation-multipage": {"gif", "apng", "webp", "tiff"},
}


class QualificationError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise QualificationError(message)


def hash_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def reference(item, root):
    need(isinstance(item, dict) and isinstance(item.get("path"), str)
         and isinstance(item.get("sha256"), str) and HEX64.fullmatch(item["sha256"]), "Invalid evidence reference")
    path = Path(item["path"])
    if not path.is_absolute():
        path = Path(root) / path
    need(path.is_file() and hash_file(path) == item["sha256"], f"Missing or changed evidence: {path}")
    return path.resolve()


def record(item, root, expected_type=dict):
    path = reference(item, root)
    need(path.stat().st_size <= 20 * 1024 * 1024, "Structured evidence is too large")
    result = json.loads(path.read_text())
    need(isinstance(result, expected_type), f"Structured evidence must be a {expected_type.__name__}")
    return result, path.parent


def number(value, minimum=0, maximum=float("inf")):
    return type(value) in (float, int) and math.isfinite(value) and minimum <= value <= maximum


def source_identity(value, expected_sha, expected_tree):
    need(value.get("schema_version") == 1 and value.get("source_sha") == expected_sha
         and value.get("source_tree") == expected_tree, "Raw evidence source identity mismatch")


def verify_build(artifact, root, source_sha, source_tree):
    build, base = record(artifact.get("build"), root)
    source_identity(build, source_sha, source_tree)
    need(build.get("dirty") is False, "Qualification requires a clean source build")
    need(reference(build.get("launch"), base) == reference(artifact, root), "Build launch mapping differs from the tested artifact")
    reference(build.get("build_log"), base)
    if artifact["kind"] in {"wheel", "appimage"}:
        reference(build.get("delivery"), base)
    runtime = Path(build.get("runtime_root", ""))
    need(runtime.is_absolute() and runtime.is_dir(), "Build runtime inventory root is missing")
    files = build.get("runtime_files")
    need(isinstance(files, list) and bool(files), "Full installed runtime inventory is required")
    names = set()
    for item in files:
        name = item.get("relative_path")
        need(isinstance(name, str) and name and not Path(name).is_absolute()
             and ".." not in Path(name).parts and name not in names, "Invalid or duplicate runtime inventory path")
        names.add(name)
        reference({"path": str(runtime / name), "sha256": item.get("sha256")}, base)
    actual = {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_file()}
    need(names == actual, "Installed runtime contains missing or uninventoried files")
    return build


def trace_facts(paths, startup_files=None):
    pids, connections, models, exited, execution = [], [], [], set(), []
    for path in paths:
        try:
            pid = int(path.name.rsplit(".", 1)[1])
        except ValueError as exc:
            raise QualificationError("Trace filename lacks its process identity") from exc
        pids.append(pid)
        for line in path.read_text(errors="replace").splitlines():
            if re.search(r"socket\(AF_INET6?\b", line) or (
                    re.search(r"\b(connect|sendto|sendmsg|sendmmsg)\(", line)
                    and re.search(r"AF_INET6?\b|<(?:TCP|UDP):", line)):
                connections.append({"pid": pid, "trace": str(path), "syscall": line})
            if _model_trace.is_model_open(line, startup_files):
                models.append({"pid": pid, "trace": str(path), "syscall": line})
            if "+++ exited with 0 +++" in line:
                exited.add(pid)
            if "execve(" in line:
                execution.append(line)
    need(len(pids) == len(set(pids)) and set(pids) == exited and bool(pids), "Missing, duplicate, or nonzero process exit evidence")
    return {"process_ids": sorted(pids), "network_attempts": connections, "model_open_attempts": models}, execution


def verify_smoke(artifact, root, source_sha, source_tree):
    smoke, base = record(artifact.get("smoke"), root)
    source_identity(smoke, source_sha, source_tree)
    need(smoke.get("complete") is False and smoke.get("display_backend") == "xcb"
         and smoke.get("native") is True, "An actual native smoke receipt, not a synthetic complete result, is required")
    need(smoke.get("artifact", {}).get("kind") == artifact["kind"]
         and reference(smoke["artifact"], base) == reference(artifact, root), "Smoke artifact mapping differs")
    need(smoke.get("returncode") == 0 and smoke.get("all_processes_stopped") is True
         and smoke.get("error") is None, "Artifact smoke did not cleanly complete")
    need(number(smoke.get("first_launch_observation_seconds"), 30), "First-launch observation was too short")
    start, end = smoke.get("observation_started_unix"), smoke.get("observation_ended_unix")
    need(number(start, 1) and number(end, start + 30), "Missing actual observation interval")
    diagnostic, _ = record(smoke.get("diagnostic"), base)
    identity = diagnostic.get("build_identity", {})
    source_identity(identity, source_sha, source_tree)
    need(identity.get("dirty") is False and bool(identity.get("source_files")), "Installed build identity lacks clean source inventory")
    need(all(isinstance(v, str) and HEX64.fullmatch(v) for v in identity["source_files"].values()), "Malformed embedded source inventory")
    profile = smoke.get("effective_profile_root")
    need(isinstance(profile, str) and Path(profile).is_absolute() and diagnostic.get("profile_root") == profile,
         "Actual explicit profile is not recorded consistently")
    need(isinstance(smoke.get("run_id"), str) and bool(smoke["run_id"]), "Smoke run identity is missing")
    traces = [reference(item, base) for item in smoke.get("traces", [])]
    build = verify_build(artifact, root, source_sha, source_tree)
    startup_files = _model_trace.attested_startup_files(build)
    need(smoke.get("python_startup_files", []) == list(startup_files.values()), "Python startup exemption lacks exact installed RECORD provenance")
    if startup_files:
        need(reference(smoke.get("installed_build"), base) == reference(artifact["build"], root), "Startup exemption uses another build record")
    actual, executions = trace_facts(traces, startup_files)
    telemetry, _ = record(smoke.get("telemetry"), base)
    need(telemetry == actual and not actual["network_attempts"] and not actual["model_open_attempts"], "Raw network/model trace contradicts telemetry or attempted egress is nonzero")
    need(diagnostic.get("pid") in actual["process_ids"], "GUI process is not in the observed process tree")
    launch = str(reference(artifact, root))
    need(any(f'execve("{launch}"' in line for line in executions), "Trace does not bind the launch to the hashed artifact")
    events = diagnostic.get("events", [])
    presented = [item for item in events if item.get("event") == "image_presented"]
    shutdown = [item for item in events if item.get("event") == "gui_shutdown"]
    need(len(presented) == 1 and len(shutdown) == 1, "Ambiguous readiness or shutdown receipt")
    ready, closed = presented[0], shutdown[0]
    need(ready.get("backend") == "xcb" and type(ready.get("generation")) is int
         and ready.get("width") == 160 and ready.get("height") == 100, "Actual painted image identity differs")
    need(number(ready.get("monotonic")) and number(closed.get("monotonic"), ready["monotonic"] + 30)
         and number(closed.get("elapsed_ms"), 0, 5000), "Actual native observation or GUI shutdown bound failed")
    origin = Path(ready.get("module_origin", ""))
    need(origin.is_absolute() and "src/imagesorter" not in str(origin), "Native package imported checkout source")
    build, _ = record(artifact["build"], root)
    runtime = Path(build["runtime_root"])
    need(origin.is_relative_to(runtime) or (artifact["kind"] == "appimage" and str(origin).startswith("/tmp/.mount_")), "Module origin is outside the tested runtime")
    before, before_root = record(smoke.get("fixtures_before"), base, list)
    after, _ = record(smoke.get("fixtures_after"), base, list)
    need(len(before) >= 2 and before == after, "First-launch fixture inventory changed or is incomplete")
    fixture_paths = [str(reference(item, before_root)) for item in before]
    need(ready.get("filepath") in fixture_paths, "Painted image is not the observed fixture")
    for field in ("base_first_launch_network_attempts", "base_model_open_attempts"):
        need(type(smoke.get(field)) is int and smoke[field] == 0, "Base first-launch attempt count is missing or nonzero")
    for field in ("no_model_files", "ready_identity_verified", "fixture_hashes_unchanged", "build_identity_verified"):
        need(smoke.get(field) is True, f"Missing native observation: {field}")


def verify_gpu_execution(gpu):
    """Verify the canonical single-inference CPU/CUDA profile from our helper."""
    need(type(gpu) is dict, "GPU reply must be an object")
    need(gpu.get("ok") is True and gpu.get("error") is None and gpu.get("fallback_reason") is None,
         "GPU execution reply was not successful")
    need(not {"node_providers", "session_providers"} & set(gpu),
         "Legacy GPU provider aliases are not canonical evidence")
    providers = gpu.get("actual_providers")
    need(type(providers) is list and 1 <= len(providers) <= 2
         and all(type(value) is str and value in {"CUDAExecutionProvider", "CPUExecutionProvider"} for value in providers)
         and len(set(providers)) == len(providers) and "CUDAExecutionProvider" in providers,
         "GPU evidence lacks a valid actual CUDA session")
    events, nodes = gpu.get("cuda_compute_events"), gpu.get("compute_nodes")
    need(type(gpu.get("provider")) is str and gpu["provider"] == "CUDAExecutionProvider" and type(events) is int and events > 0
         and type(nodes) is list and 1 <= len(nodes) <= 100000,
         "GPU evidence lacks typed positive node execution")
    names, cuda_nodes = set(), 0
    for node in nodes:
        need(type(node) is dict and set(node) == {"name", "provider"},
             "GPU compute node must contain its exact name and provider")
        name, provider = node["name"], node["provider"]
        need(type(name) is str and 0 < len(name) <= 4096 and name.strip() == name
             and bool(name.strip()) and chr(0) not in name and name not in names,
             "GPU compute node name is missing, malformed or duplicated")
        need(type(provider) is str and provider in providers,
             "GPU compute node provider was not in the actual session")
        names.add(name)
        cuda_nodes += provider == "CUDAExecutionProvider"
    need(cuda_nodes == events, "GPU event count contradicts named CUDA compute nodes")
    for key in ("tensor_sha256", "profile_sha256"):
        value = gpu.get(key)
        need(type(value) is str and bool(HEX64.fullmatch(value)),
             f"GPU evidence lacks its exact {key}")


def verify_observation(case, artifact, component_refs, root, source_sha, source_tree):
    observation, base = record(case.get("observation"), root)
    source_identity(observation, source_sha, source_tree)
    need(observation.get("kind") == "operator-observation" and observation.get("id") == case["id"]
         and observation.get("artifact_sha256") == artifact["sha256"], "Interactive observation identity mismatch")
    for name in ("operator", "run_id", "expected", "observed"):
        need(isinstance(observation.get(name), str) and bool(observation[name].strip()), f"Missing attributed observation field: {name}")
    observed_at = datetime.fromisoformat(observation.get("observed_at", "").replace("Z", "+00:00"))
    need(observed_at.tzinfo is not None, "Observation timestamp must include timezone")
    steps = observation.get("steps")
    need(isinstance(steps, list) and bool(steps) and all(isinstance(s, str) and s.strip() for s in steps), "Exact executed steps are required")
    support = observation.get("supporting_evidence")
    need(isinstance(support, list) and bool(support), "Observation lacks screenshot/log evidence")
    for item in support:
        reference(item, base)
    criteria = CASE_CRITERIA.get(case["id"], COMPONENT_CRITERIA)
    need(all(observation.get("criteria", {}).get(name) is True for name in criteria), f"Case coverage incomplete: {case['id']}")
    for field in ("fixtures_before", "fixtures_after"):
        rows = observation.get(field)
        need(isinstance(rows, list) and bool(rows), f"Case {case['id']} needs {field}")
        names = set()
        for item in rows:
            name = item.get("relative_path")
            need(isinstance(name, str) and name and not Path(name).is_absolute() and ".." not in Path(name).parts
                 and name not in names and HEX64.fullmatch(item.get("sha256", "")) and type(item.get("size")) is int
                 and item["size"] >= 0, "Malformed case fixture inventory")
            names.add(name)
    if case["id"] in {"KDE-04", "KDE-05", "KDE-06", "KDE-07", "KDE-08", "KDE-14"}:
        receipts = observation.get("mutation_receipts")
        need(isinstance(receipts, list) and bool(receipts), "File-operation case lacks actual durable result receipts")
        for item in receipts:
            result, _ = record(item, base)
            need(result.get("schema_version") == 1 and bool(result.get("operation_id"))
                 and result.get("state") in {"completed", "completed_with_warning", "failed", "cancelled", "recovery_required"}, "Mutation result is not terminal or recoverable")
    if case["id"] == "KDE-14":
        need(number(observation.get("gui_shutdown_ms"), 0, 5000)
             and number(observation.get("readonly_reap_ms"), 0, 3000)
             and observation.get("mutation_drain_completed") is True
             and observation.get("all_processes_stopped") is True, "Shutdown case lacks bounded reader exit and eventual mutation drain")
    if case["id"].startswith("COMPONENT:"):
        component_id = case["id"].split(":", 1)[1]
        need(observation.get("component_manifest_sha256") == component_refs[component_id]["sha256"], "Case uses another component manifest")
        if component_id in FORMAT_COVERAGE:
            formats = observation.get("formats", {})
            need(FORMAT_COVERAGE[component_id] <= set(formats) and all(formats[name].get("status") == "passed" for name in FORMAT_COVERAGE[component_id]), "Advertised format coverage is incomplete")
            for name in FORMAT_COVERAGE[component_id]:
                reference(formats[name].get("fixture"), base)
        if component_id == "provider.onnx-nvidia":
            gpu, _ = record(observation.get("gpu_result"), base)
            verify_gpu_execution(gpu)
            need(number(observation.get("cpu_gpu_max_abs_error")) and number(observation.get("cpu_gpu_tolerance"), 0, .01)
                 and observation["cpu_gpu_max_abs_error"] <= observation["cpu_gpu_tolerance"]
                 and bool(observation.get("forced_fallback_reason")), "GPU equivalence/fallback evidence failed")


def verify_semantics(receipt, root, source_sha, source_tree):
    artifacts = {item["kind"]: item for item in receipt["artifacts"]}
    components = {item["id"]: item["manifest"] for item in receipt["components"]}
    for component_id, manifest in components.items():
        descriptor, _ = record(manifest, root)
        need(descriptor.get("id") == component_id and bool(descriptor.get("version"))
             and HEX64.fullmatch(descriptor.get("sha256", "")) and bool(descriptor.get("files")), "Component evidence is not an actual installed descriptor")
    for artifact in artifacts.values():
        build = verify_build(artifact, root, source_sha, source_tree)
        catalogs = [Path(build["runtime_root"]) / item["relative_path"] for item in build["runtime_files"]
                    if item["relative_path"].endswith("imagesorter/resources/component_catalog.json")]
        need(len(catalogs) == 1, "Installed artifact has no unique source-controlled component catalogue")
        catalog = json.loads(catalogs[0].read_text())
        need(catalog.get("schema_version") == 1 and isinstance(catalog.get("components"), list), "Invalid installed component catalogue")
        for component_id, manifest in components.items():
            descriptor, _ = record(manifest, root)
            matches = [item for item in catalog["components"] if item.get("id") == component_id
                       and item.get("version") == descriptor["version"]]
            need(len(matches) == 1 and matches[0] == descriptor, "Component observation descriptor is not trusted by this actual artifact")
        verify_smoke(artifact, root, source_sha, source_tree)
    for case in receipt["cases"]:
        need(case["artifact_kind"] in artifacts, "Unknown artifact in native observation")
        verify_observation(case, artifacts[case["artifact_kind"]], components, root, source_sha, source_tree)
