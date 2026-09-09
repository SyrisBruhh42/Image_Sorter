"""Fail-closed engineering publication gate; never an automated legal opinion.

Preserved terms and an attributable, artifact-scoped review are prerequisites,
not proof that the reviewer's legal conclusion is correct. This verifier checks
their scope, recorded unresolved issues, source duties and actual delivered bytes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from . import appimage_format as _appimage_format
else:
    import appimage_format as _appimage_format

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_JSON = 20 * 1024 * 1024
CORE = {"wheel", "onedir", "appimage"}
PACK_SOURCE_PATHS = {
    "src/imagesorter/component_worker.py", "src/imagesorter/ai_preprocessing.py",
    "src/imagesorter/apng_frames.py", "src/imagesorter/__init__.py",
    "src/imagesorter/reader_sandbox.py",
    "scripts/build_component_packs.py", "LICENSE",
}


class PublicationError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise PublicationError(message)


def digest(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode), f"Evidence is not a regular file: {path}")
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
        after = os.fstat(stream.fileno())
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         f"Evidence changed while being verified: {path}")
    return value.hexdigest()


def reference(value, root):
    need(isinstance(value, dict) and isinstance(value.get("path"), str) and
         bool(HEX64.fullmatch(str(value.get("sha256", "")))), "Missing preserved evidence reference/hash")
    path = Path(value["path"])
    path = path if path.is_absolute() else Path(root) / path
    need(not path.is_symlink() and digest(path) == value["sha256"], f"Evidence hash mismatch: {path}")
    return path.resolve()


def record(value, root):
    path = reference(value, root)
    need(path.stat().st_size <= MAX_JSON, "Structured publication evidence exceeds 20 MiB")
    data = path.read_bytes()
    need(hashlib.sha256(data).hexdigest() == value["sha256"], "Structured evidence changed after verification")
    result = json.loads(data)
    need(isinstance(result, dict), "Publication evidence must be an object")
    return result, path.parent


def relative(value):
    need(isinstance(value, str) and value and not Path(value).is_absolute() and
         "\\" not in value and not any(ord(char) < 32 for char in value) and
         all(part not in {"", ".", ".."} for part in value.split("/")),
         "Invalid delivery-relative path")
    return value


def source_archive(project, base):
    archive = reference(project.get("archive"), base)
    expected = project.get("files")
    need(isinstance(expected, dict) and bool(expected), "Project source inventory is missing")
    observed, objects = {}, {}
    with tarfile.open(archive, "r:*") as package:
        for member in package:
            need(member.isfile() and member.name.startswith("Image_Sorter/"), "Unsafe project source archive member")
            name = relative(member.name[len("Image_Sorter/"):])
            need(name in expected and name not in observed and member.size <= 512 * 1024 * 1024,
                 "Unexpected, duplicate or oversized source archive member")
            stream = package.extractfile(member)
            value = hashlib.sha256()
            blob = hashlib.sha1(f"blob {member.size}\0".encode(), usedforsecurity=False)
            for block in iter(lambda stream=stream: stream.read(1024 * 1024), b""):
                value.update(block)
                blob.update(block)
            observed[name] = value.hexdigest()
            objects[name] = ("100755" if member.mode & 0o100 else "100644", blob.digest())
    need(observed == expected, "Project source archive does not match its inventory")
    need(git_tree(objects) == project.get("source_tree"), "Project source archive does not match its declared Git tree")
    return expected


def git_tree(objects):
    """Reconstruct the Git tree from captured regular-file bytes and executable bits."""
    root = {}
    for name, value in objects.items():
        node = root
        parts = relative(name).split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            need(isinstance(node, dict), "Source archive file/directory collision")
        need(parts[-1] not in node, "Source archive file/directory collision")
        node[parts[-1]] = value

    def encode_tree(node):
        entries = []
        for name, value in node.items():
            encoded = name.encode("utf-8")
            directory = isinstance(value, dict)
            mode, oid = ("40000", encode_tree(value)) if directory else value
            entries.append((encoded + (b"/" if directory else b""), mode.encode() + b" " + encoded + b"\0" + oid))
        data = b"".join(value for _key, value in sorted(entries))
        return hashlib.sha1(f"tree {len(data)}\0".encode() + data, usedforsecurity=False).digest()

    return encode_tree(root).hex()


def verify_pack_source_relation(provenance, project, project_files, relation, final_sources, source_sha, source_tree):
    """Bind clean pack build P to final catalogue/application C without a hash cycle."""
    source = provenance.get("source") or {}
    files = source.get("files")
    need(source.get("dirty") is False and HEX40.fullmatch(str(source.get("head", ""))) and
         source["head"] == project.get("source_sha"), "Pack provenance requires its exact clean build-source archive")
    need(isinstance(files, dict) and PACK_SOURCE_PATHS.issubset(files) and
         all(HEX64.fullmatch(str(value)) and project_files.get(name) == value for name, value in files.items()),
         "Pack provenance omits or differs from required captured build/helper source")
    helpers = {name: value for name, value in files.items() if name.startswith("src/")}
    need(all(final_sources.get(name) == value for name, value in helpers.items()),
         "Pack helper source is not the final reviewed source")
    need(isinstance(relation, dict) and relation.get("kind") == "pack-source-adoption" and
         relation.get("pack_source_sha") == source["head"] and
         relation.get("pack_source_tree") == project.get("source_tree") and
         relation.get("final_source_sha") == source_sha and relation.get("final_source_tree") == source_tree and
         relation.get("unchanged_helper_files") == helpers,
         "Explicit exact pack-build P to final-catalogue C source relationship is missing")


def appimage_offset(path):
    try:
        return _appimage_format.appimage_offset(path)
    except ValueError as exc:
        raise PublicationError(str(exc)) from exc


def appimage_source_pin(project, base):
    archive = reference(project.get("archive"), base)
    with tarfile.open(archive, "r:*") as package:
        member = package.getmember("Image_Sorter/build_desktop.py")
        need(member.isfile() and member.size <= MAX_JSON, "AppImage build recipe is absent or oversized")
        tree = ast.parse(package.extractfile(member).read())
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "APPIMAGE_RUNTIME_X86_64_SHA256" for target in node.targets):
            found.append(ast.literal_eval(node.value))
    need(len(found) == 1 and isinstance(found[0], str) and HEX64.fullmatch(found[0]),
         "Exact source-approved AppImage runtime pin is missing")
    return found[0]


def squashfs_output(arguments, *, maximum):
    """No extraction or executable launch: bound the independent reader's output."""
    import resource
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum + 1, maximum + 1))
        resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        result = subprocess.run(["unsquashfs", "-no-progress", "-processors", "1", "-data-queue", "16",
                                 "-frag-queue", "16", *arguments], stdout=output, stderr=error,
                                timeout=60, preexec_fn=limits)
        need(result.returncode == 0 and output.tell() <= maximum and error.tell() <= min(maximum + 1, MAX_JSON),
             "Independent SquashFS verification failed or exceeded its output bound")
        output.seek(0)
        return output.read()


def verify_appimage(path, runtime, files, runtime_input, expected_runtime_sha):
    try:
        verified = _appimage_format.verify_runtime_prefix(path, runtime_input, expected_runtime_sha)
    except ValueError as exc:
        raise PublicationError(str(exc)) from exc
    offset = verified["squashfs_offset"]
    listing = squashfs_output(["-lln", "-d", "__publication__", "-offset", str(offset), str(path)], maximum=MAX_JSON).decode("utf-8")
    expected = {item["path"]: item for item in files}
    seen = set()
    for line in listing.splitlines():
        columns = line.split(None, 5)
        if len(columns) != 6 or not columns[5].startswith("__publication__/"):
            need(not columns or not columns[0].startswith(("-", "l", "b", "c", "p", "s")), "Unparseable SquashFS member")
            continue
        mode, name = columns[0], columns[5][len("__publication__/"):]
        if mode.startswith("d"):
            relative(name)
            continue
        name, separator, link = name.partition(" -> ")
        relative(name)
        need(name in expected and name not in seen, "SquashFS contains uninventoried or duplicate files")
        seen.add(name)
        item, target = expected[name], runtime / name
        if mode.startswith("l"):
            need(separator and item.get("symlink") == link, "SquashFS symlink differs from runtime")
        else:
            need(mode.startswith("-") and not separator and "symlink" not in item and
                 stat.filemode(target.stat().st_mode) == mode and int(columns[2]) == target.stat().st_size,
                 "SquashFS member type, size or mode differs from runtime")
            content = squashfs_output(["-cat", "-no-wildcards", "-offset", str(offset), str(path), name], maximum=target.stat().st_size)
            need(hashlib.sha256(content).hexdigest() == item["sha256"] == digest(target), "SquashFS bytes differ from collected runtime")
    need(seen == set(expected), "SquashFS omits collected runtime files")


def verify_archive_delivery(path, runtime, files, *, kind, prefix="", runtime_input=None, expected_runtime_sha=None):
    """Derive delivery contents; a caller's delivery digest is not a mapping proof."""
    if prefix:
        relative(prefix.rstrip("/"))
        need(prefix.endswith("/"), "Delivery prefix must end in a slash")
    expected = {entry["path"]: entry for entry in files}
    observed = set()

    def member(name, stream=None, *, link=None, mode=None, size=None):
        relative(name.rstrip("/"))
        need(name.startswith(prefix), "Archive member escapes the declared delivery prefix")
        name = relative(name[len(prefix):])
        need(name in expected and name not in observed, "Delivery contains missing, duplicate or uninventoried runtime members")
        observed.add(name)
        item, target = expected[name], runtime / name
        if link is not None:
            need(item.get("symlink") == link and target.is_symlink(), "Delivery symlink differs from collected runtime")
        else:
            need("symlink" not in item and size == target.stat().st_size, "Delivery member type/size differs from runtime")
            value = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
            need(value.hexdigest() == item["sha256"] == digest(target), "Delivered bytes differ from collected runtime")
            if mode is not None:
                need(stat.S_IMODE(mode) == stat.S_IMODE(target.stat().st_mode), "Delivered permissions differ from runtime")

    if kind == "wheel":
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                relative(item.filename.rstrip("/"))
                if item.is_dir():
                    continue
                mode = item.external_attr >> 16
                need(not stat.S_ISLNK(mode), "Wheel symlink requires a separately supported delivery format")
                with archive.open(item) as stream:
                    member(item.filename, stream, size=item.file_size, mode=mode if mode else None)
    elif kind == "appimage":
        need(runtime_input is not None and expected_runtime_sha is not None and not prefix,
             "Independent AppImage runtime/payload verification inputs are required")
        verify_appimage(path, runtime, files, runtime_input, expected_runtime_sha)
        return
    else:
        with tarfile.open(path, "r:*") as archive:
            for item in archive:
                relative(item.name.rstrip("/"))
                if item.isdir():
                    continue
                need(item.isfile() or item.issym(), "Unsupported special/hard-linked delivery member")
                if item.issym():
                    member(item.name, link=item.linkname)
                else:
                    with archive.extractfile(item) as stream:
                        member(item.name, stream, size=item.size, mode=item.mode)
    need(observed == set(expected), "Delivery omits collected runtime files")


def verify_closure(value, base, source_sha, source_tree, *, core):
    closure, base = record(value, base)
    need(closure.get("kind") == "runtime-license-source-inventory" and closure.get("schema_version") == 1,
         "A collected runtime/source closure is required")
    need(closure.get("engineering_complete") is True and closure.get("unresolved") == [],
         "Unresolved runtime/source distribution obligations")
    need(closure.get("legal_clearance") == "not-asserted", "A self-declared legal clearance is not publication evidence")
    project = closure.get("project") or {}
    need(project.get("dirty") is False, "Publication requires a clean captured project source")
    if core:
        need(project.get("source_sha") == source_sha and project.get("source_tree") == source_tree,
             "Source closure is not the exact publication candidate")
        identity = closure.get("build_identity") or {}
        need(identity.get("dirty") is False and identity.get("source_sha") == source_sha and
             identity.get("source_tree") == source_tree, "Runtime build identity disagrees with source closure")
    project_files = source_archive(project, base)
    supporting = {project["archive"]["sha256"]}
    runtime = Path(closure.get("runtime_root") or "")
    need(runtime.is_absolute() and runtime.is_dir() and not runtime.is_symlink(), "Final runtime root is missing")
    files = closure.get("runtime_files")
    need(isinstance(files, list) and bool(files), "Final runtime inventory is absent")
    observed = set()
    for item in files:
        name = relative(item.get("path"))
        need(name not in observed, "Duplicate runtime inventory path")
        observed.add(name)
        path = runtime / name
        need(path.resolve().is_relative_to(runtime.resolve()), "Runtime path escapes delivery")
        if "symlink" in item:
            need(path.is_symlink() and str(path.readlink()) == item["symlink"], "Runtime symlink changed")
        else:
            reference({"path": str(path), "sha256": item.get("sha256")}, base)
    actual = {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if not p.is_dir() or p.is_symlink()}
    need(observed == actual, "Final runtime contains missing or uninventoried files")
    if core:
        identities = [runtime / name for name in observed if Path(name).name == "build_identity.json"]
        need(len(identities) == 1 and json.loads(identities[0].read_text()) == closure["build_identity"],
             "Collected identity differs from actual embedded runtime identity")
        source_files = closure["build_identity"].get("source_files")
        need(isinstance(source_files, dict) and bool(source_files) and
             all(project_files.get(name) == value for name, value in source_files.items()),
             "Embedded runtime source hashes disagree with accompanying project source")
    sources = closure.get("sources")
    packages = closure.get("packages")
    need(isinstance(sources, list) and bool(sources) and isinstance(packages, list), "Source/component mapping absent")
    source_map = {item.get("id"): item for item in sources}
    need(len(source_map) == len(sources) and all(isinstance(key, str) and key for key in source_map),
         "Duplicate or missing source identity")
    for item in sources:
        expression = item.get("license_expression")
        need(isinstance(expression, str) and bool(expression), "Unresolved source licence expression")
        required = bool(re.search(r"(?:A?L?GPL)[- v]", expression, re.IGNORECASE))
        explicit = item.get("corresponding_source_required", False)
        need(type(explicit) is bool, "Invalid source duty override")
        if required or explicit:
            need(item.get("correspondence") == "verified-build-inputs" and bool(item.get("archives")),
                 "Required corresponding build source is unresolved")
            review = item.get("review") or {}
            need(all(review.get(key) for key in ("reviewer", "reviewed_at", "basis")), "Source correspondence review missing")
        for archive in item.get("archives", []):
            reference(archive, base)
            supporting.add(archive["sha256"])
    package_ids = set()
    for item in packages:
        key = item.get("id")
        need(isinstance(key, str) and key and key not in package_ids, "Duplicate or absent runtime package identity")
        package_ids.add(key)
        need(item.get("notices"), "Runtime package notices absent")
        for notice in item["notices"]:
            reference(notice, base)
            supporting.add(notice["sha256"])
        if re.search(r"(?:A?L?GPL)[- v]", str(item.get("license_expression", "")), re.IGNORECASE):
            need(key in source_map and source_map[key].get("version") == item.get("version"),
                 "Package corresponding source/version missing")
    native_names = set()
    for item in closure.get("native_files", []):
        name = relative(item.get("path"))
        need(name not in native_names and name in observed, "Invalid native mapping identity")
        native_names.add(name)
        reference({"path": str(runtime / name), "sha256": item.get("sha256")}, base)
        mapping = item.get("source_mapping") or {}
        need(mapping.get("sha256") == item["sha256"] and bool(mapping.get("components")) and
             set(mapping["components"]).issubset(source_map), "Native bytes lack exact source-component mapping")
    for name in observed:
        path = runtime / name
        if not path.is_symlink() and path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) == b"\x7fELF":
                    need(name in native_names, "Unmapped ELF runtime file")
    return closure, runtime, project_files, package_ids | set(source_map), supporting


def model_asset_obligations(closure, descriptor):
    if descriptor.get("id") != "ai.mobilenet-v2":
        return {}
    expected = {"mobilenetv2.onnx": "model-weights", "labels.txt": "model-labels"}
    mappings = closure.get("asset_mappings")
    need(isinstance(mappings, list) and len(mappings) == 2 and {row.get("path") for row in mappings} == set(expected),
         "Model weights and labels each require an explicit exact-asset source/grant mapping")
    sources = {row["id"] for row in closure["sources"]}
    result = {}
    for row in mappings:
        source_id, name = row.get("source_id"), row["path"]
        need(source_id in sources and source_id not in result and row.get("role") == expected[name] and
             row.get("sha256") == descriptor["files"].get(name, {}).get("sha256") and
             HEX64.fullmatch(str(row.get("sha256", ""))),
             "Model assets require distinct source/grant obligations matching the approved file hashes")
        result[source_id] = {"asset_sha256": row["sha256"], "asset_role": row["role"]}
    return result


def verify_reader_descriptor(descriptor):
    if descriptor.get("id") == "ai.mobilenet-v2":
        need("reader_policy_version" in descriptor and "min_landlock_abi" in descriptor and
             descriptor["reader_policy_version"] is None and descriptor["min_landlock_abi"] is None,
             "Data-only model pack must not assert an executable reader policy")
        return
    minimum = 6 if descriptor.get("id") == "provider.onnx-nvidia" else 3
    need(type(descriptor.get("reader_policy_version")) is int and descriptor["reader_policy_version"] == 1 and
         type(descriptor.get("min_landlock_abi")) is int and descriptor["min_landlock_abi"] == minimum,
         "Executable component lacks the qualified reader policy descriptor")


def verify_reader_policy(reply, descriptor=None):
    """Verify evidence of the executed helper policy, not just a catalogue promise."""
    gpu = descriptor is not None and descriptor["id"] == "provider.onnx-nvidia"
    expected_id, version = (descriptor["id"], descriptor["version"]) if descriptor else ("core.cpu", "base")
    policy = reply.get("reader_isolation") or {}
    need(reply.get("component_id") == expected_id and reply.get("component_version") == version and
         (descriptor is None or reply.get("component_sha256") == descriptor["sha256"]),
         "Native reader result identifies a different executed helper")
    need(policy.get("enforced") is True and type(policy.get("policy_version")) is int and policy["policy_version"] == 1 and
         type(policy.get("landlock_abi")) is int and policy["landlock_abi"] >= (6 if gpu else 3) and
         policy.get("network_and_mutation_ipc") == "denied" and policy.get("external_metadata_writes") == "denied" and
         policy.get("own_task_proc_writes") is gpu and isinstance(policy.get("device_write_paths"), list) and
         isinstance(policy.get("scratch"), str) and Path(policy["scratch"]).is_absolute(),
         "Native reader result lacks enforced qualified filesystem and IPC isolation")
    paths = policy["device_write_paths"]
    need((not paths and not gpu) or (gpu and bool(paths) and all(isinstance(path, str) and
         re.fullmatch(r"/dev/nvidia(?:[0-9]+|ctl|-uvm|-uvm-tools)", path) for path in paths)),
         "Native reader result contains unexpected device-write authority")


def verify_native_readers(native, catalog_bytes, base):
    catalog = json.loads(catalog_bytes)["components"]
    descriptors = {}
    for component in native["components"]:
        descriptor, _ = record(component["manifest"], base)
        need(descriptor in catalog, "Native reader uses an untrusted component descriptor")
        descriptors[component["id"]] = descriptor
    for case in native["cases"]:
        if not case["id"].startswith("COMPONENT:"):
            continue
        component_id = case["id"].split(":", 1)[1]
        descriptor = descriptors[component_id]
        observation, observation_base = record(case["observation"], base)
        envelopes = observation.get("reader_results")
        need(isinstance(envelopes, list) and bool(envelopes), "Native component observation lacks raw reader results")
        fixtures, actual_helper_results, helper_reply_hashes = set(), 0, set()
        for reference_value in envelopes:
            envelope, envelope_base = record(reference_value, observation_base)
            need(envelope.get("kind") == "reader-result-observation", "Native reader result envelope is missing")
            reference(envelope.get("fixture"), envelope_base)
            fixture_sha = envelope["fixture"]["sha256"]
            reply, _ = record(envelope.get("reply"), envelope_base)
            if "input_sha256" in reply:
                need(reply["input_sha256"] == fixture_sha, "Native reader result identifies another fixture")
            cpu = reply.get("component_id") == "core.cpu"
            need(not cpu or component_id in {"ai.mobilenet-v2", "provider.onnx-nvidia"},
                 "CPU inference cannot stand in for an optional decoder")
            verify_reader_policy(reply, None if cpu else descriptor)
            if component_id == "ai.mobilenet-v2":
                need(cpu and reply.get("provider") == "CPUExecutionProvider" and
                     reply.get("model_sha256") == descriptor["files"]["mobilenetv2.onnx"]["sha256"] and
                     reply.get("labels_sha256") == descriptor["files"]["labels.txt"]["sha256"],
                     "Model observation does not identify actual CPU execution of approved model and labels")
            if component_id == "provider.onnx-nvidia":
                model = descriptors.get("ai.mobilenet-v2") or {}
                need(reply.get("model_sha256") == model.get("files", {}).get("mobilenetv2.onnx", {}).get("sha256") and
                     reply.get("labels_sha256") == model.get("files", {}).get("labels.txt", {}).get("sha256"),
                     "Provider observation uses another model or labels asset")
            if not cpu or component_id == "ai.mobilenet-v2":
                fixtures.add(fixture_sha)
                actual_helper_results += 1
                helper_reply_hashes.add(envelope["reply"]["sha256"])
        need(actual_helper_results > 0, "Fallback alone cannot qualify the advertised optional helper")
        for value in observation.get("formats", {}).values():
            reference(value.get("fixture"), observation_base)
            need(value["fixture"]["sha256"] in fixtures, "Native format coverage lacks its actual isolated reader result")
        if component_id == "provider.onnx-nvidia":
            result, _ = record(observation.get("gpu_result"), observation_base)
            verify_reader_policy(result, descriptor)
            need(observation["gpu_result"]["sha256"] in helper_reply_hashes,
                 "CUDA compute result is not the actual fixture-bound isolated helper reply")


def verify_review(value, base, artifact_sha, obligation_ids, *, asset_obligations=None):
    review, base = record(value, base)
    need(review.get("kind") == "distribution-obligations-review" and review.get("artifact_sha256") == artifact_sha,
         "Distribution review does not identify the exact delivery")
    need(review.get("unresolved") == [] and all(review.get(k) for k in ("reviewer", "reviewed_at")),
         "Distribution review is missing or unresolved")
    rows = review.get("obligations")
    need(isinstance(rows, list) and len(rows) == len(obligation_ids) and
         {item.get("id") for item in rows} == obligation_ids, "Distribution review omits or duplicates components")
    supporting = set()
    for item in rows:
        need(item.get("disposition") == "reviewed-for-distribution" and item.get("unresolved") == [] and
             bool(item.get("terms")), "Unresolved distribution obligation or missing terms")
        for terms in item["terms"]:
            need(reference(terms, base).stat().st_size > 0, "Empty preserved terms")
            supporting.add(terms["sha256"])
        decision, _ = record(item.get("decision"), base)
        need(decision.get("component_id") == item["id"] and decision.get("artifact_sha256") == artifact_sha and
             decision.get("unresolved") == [] and all(decision.get(k) for k in ("reviewer", "reviewed_at", "basis")),
             "Missing artifact-scoped review rationale; legal_clearance alone is insufficient")
        if item["id"] in (asset_obligations or {}):
            need(all(decision.get(key) == value for key, value in asset_obligations[item["id"]].items()),
                 "Model grant review does not identify the exact weights or labels asset")
    return supporting


def verify_publication(catalog_bytes, manifest_path, source_sha, source_tree):
    need(HEX40.fullmatch(source_sha) and HEX40.fullmatch(source_tree), "Full source commit/tree identities required")
    need(len(catalog_bytes) <= MAX_JSON, "Publication catalogue exceeds size bound")
    catalog = json.loads(catalog_bytes)
    need(catalog.get("publication_status") == "evidence-gated-release",
         "Catalogue is not publication-eligible (local qualification or unresolved rights)")
    need(catalog.get("schema_version") == 1 and isinstance(catalog.get("components"), list), "Invalid publication catalogue")
    need(manifest_path is not None, "A preserved distribution manifest is required")
    manifest_path = Path(manifest_path)
    manifest, base = record({"path": str(manifest_path.resolve()), "sha256": digest(manifest_path)}, manifest_path.parent)
    need(manifest.get("schema_version") == 1 and manifest.get("kind") == "imagesorter-publication-evidence" and
         manifest.get("source_sha") == source_sha and manifest.get("source_tree") == source_tree and
         manifest.get("catalog_sha256") == hashlib.sha256(catalog_bytes).hexdigest(), "Publication evidence identity mismatch")
    need(manifest.get("unresolved") == [], "Publication evidence has unresolved obligations")
    descriptors = {f'{item["id"]}@{item["version"]}': item for item in catalog["components"]}
    need(len(descriptors) == len(catalog["components"]), "Duplicate catalogue identity")
    for descriptor in descriptors.values():
        verify_reader_descriptor(descriptor)
    deliveries = manifest.get("deliveries")
    need(isinstance(deliveries, list) and len(deliveries) == len(CORE | set(descriptors)) and
         {item.get("id") for item in deliveries} == CORE | set(descriptors), "All three core deliveries and every catalogued pack require evidence")
    final_sources = None
    pack_sources = []
    supporting = set()
    publication_files = {}
    for item in deliveries:
        key = item["id"]
        artifact = reference(item.get("artifact"), base)
        artifact_sha = item["artifact"]["sha256"]
        need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", artifact.name) and artifact.name not in publication_files,
             "Publication filenames must be safe and unique")
        publication_files[artifact.name] = artifact_sha
        closure, runtime, sources, obligations, supplied = verify_closure(item.get("closure"), base, source_sha, source_tree, core=key in CORE)
        supporting.update(supplied)
        options = {}
        asset_obligations = {}
        if key == "appimage":
            _, closure_base = record(item["closure"], base)
            need("appimage-runtime" in obligations, "AppImage executable runtime terms/source review is missing")
            options = {"runtime_input": reference(item.get("appimage_runtime"), base),
                       "expected_runtime_sha": appimage_source_pin(closure["project"], closure_base)}
        verify_archive_delivery(artifact, runtime, closure["runtime_files"], kind=key if key in CORE else "pack", prefix=item.get("delivery_prefix", ""), **options)
        need(digest(artifact) == artifact_sha, "Delivery changed while its contents were verified")
        if key in CORE:
            catalogs = [p for p in runtime.rglob("component_catalog.json") if p.as_posix().endswith("imagesorter/resources/component_catalog.json")]
            need(len(catalogs) == 1 and catalogs[0].read_bytes() == catalog_bytes, "Final runtime catalogue differs from publication trust root")
            need(closure.get("delivery_sha256") == artifact_sha, "Runtime closure does not bind the delivered artifact")
            need(final_sources is None or final_sources == sources, "Core deliveries have different source inventories")
            final_sources = sources
        else:
            descriptor = descriptors[key]
            need(artifact_sha == descriptor.get("sha256"), "Pack archive differs from shipped catalogue")
            need(type(descriptor.get("archive_size")) is int and artifact.stat().st_size == descriptor["archive_size"] and
                 type(descriptor.get("installed_size")) is int and
                 sum(entry.get("size", -1) for entry in descriptor.get("files", {}).values()) == descriptor["installed_size"],
                 "Pack archive or installed byte counts differ from the descriptor")
            inventory = {entry["path"]: entry for entry in closure["runtime_files"]}
            need(set(inventory) == set(descriptor.get("files", {})), "Pack runtime differs from catalogue file inventory")
            for name, expected in descriptor["files"].items():
                path = runtime / name
                need(inventory[name].get("sha256") == expected.get("sha256") and path.stat().st_size == expected.get("size") and
                     stat.S_IMODE(path.stat().st_mode) == expected.get("mode"), "Pack runtime bytes/modes differ from catalogue")
            provenance_path = relative(descriptor.get("provenance", "provenance.json"))
            need(provenance_path in inventory, "Pack provenance is not in its trusted inventory")
            provenance = json.loads((runtime / provenance_path).read_text())
            need(provenance.get("schema_version") == 1 and provenance.get("component_id") == descriptor["id"] and
                 closure.get("component_identity") == {"descriptor": descriptor, "provenance": provenance,
                     "provenance_path": provenance_path, "provenance_sha256": inventory[provenance_path]["sha256"]},
                 "Pack closure lacks its actual explicit component descriptor/provenance identity")
            pack_sources.append((provenance, closure["project"], sources, item.get("source_adoption")))
            asset_obligations = model_asset_obligations(closure, descriptor)
        supporting.update(verify_review(item.get("review"), base, artifact_sha, obligations,
                                        asset_obligations=asset_obligations))
    for provenance, project, sources, relation in pack_sources:
        verify_pack_source_relation(provenance, project, sources, relation, final_sources, source_sha, source_tree)
    support = reference(manifest.get("support_archive"), base)
    need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", support.name) and support.name not in publication_files,
         "Support archive filename is unsafe or collides with a delivery")
    included = set()
    names = set()
    with tarfile.open(support, "r:*") as archive:
        for item in archive:
            name = relative(item.name)
            need(item.isfile() and name not in names and item.size <= 4 * 1024 ** 3,
                 "Support archive contains unsafe, duplicate or oversized material")
            names.add(name)
            value = hashlib.sha256()
            with archive.extractfile(item) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block)
            included.add(value.hexdigest())
    need(supporting.issubset(included), "Publication support archive omits required source or terms bytes")
    need(digest(support) == manifest["support_archive"]["sha256"], "Support archive changed during verification")
    publication_files[support.name] = manifest["support_archive"]["sha256"]
    return {"eligible": True, "legal_clearance": "not-asserted", "manifest_sha256": digest(manifest_path),
            "source_sha": source_sha, "source_tree": source_tree,
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(), "deliveries": len(deliveries),
            "publication_files": publication_files,
            "delivery_sha256": {item["id"]: item["artifact"]["sha256"] for item in deliveries}}


def verify_native_binding(native, catalog_bytes, publication, base):
    deliveries = {}
    for item in native["artifacts"]:
        build, build_base = record(item.get("build"), base)
        reference(build.get("delivery"), build_base)
        deliveries[item["kind"]] = build["delivery"]["sha256"]
    need(deliveries ==
         {key: value for key, value in publication["delivery_sha256"].items() if key in CORE},
         "Native qualification and publication identify different delivered artifacts")
    descriptors = json.loads(catalog_bytes)["components"]
    for component in native["components"]:
        descriptor, _ = record(component["manifest"], base)
        need(descriptor in descriptors and publication["delivery_sha256"].get(f'{descriptor["id"]}@{descriptor["version"]}') == descriptor["sha256"],
             "Native qualification and publication identify different component packs")


def verify_release(catalog_bytes, manifest_path, qualification_path, source_sha, source_tree):
    """Both independent gates, bound to the same final delivered bytes."""
    need(qualification_path is not None, "Complete native qualification is required before publication")
    if __package__:
        from .cutover import COMPONENT_IDS, GateError, verify_qualification
    else:
        from cutover import COMPONENT_IDS, GateError, verify_qualification
    try:
        native = verify_qualification(qualification_path, source_sha, source_tree, COMPONENT_IDS)
    except GateError as exc:
        raise PublicationError(f"Native qualification failed: {exc}") from exc
    result = verify_publication(catalog_bytes, manifest_path, source_sha, source_tree)
    verify_native_binding(native, catalog_bytes, result, Path(qualification_path).parent)
    verify_native_readers(native, catalog_bytes, Path(qualification_path).parent)
    result["native_qualification_sha256"] = digest(qualification_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--distribution", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--publish-directory", type=Path, help="Require every authorized delivery/support archive to be staged here")
    args = parser.parse_args(argv)
    try:
        result = verify_release(args.catalog.read_bytes(), args.distribution, args.qualification, args.source_sha, args.source_tree)
        if args.publish_directory:
            for name, expected in result["publication_files"].items():
                need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name), "Unsafe publication filename")
                reference({"path": str((args.publish_directory / name).resolve()), "sha256": expected}, Path.cwd())
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(f"Publication blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
