"""Publication refuses incomplete, unbound or self-declared rights evidence."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import publication_guard as guard
from scripts.cutover import PHASES, Controller, GateError

SHA = "1" * 40
_source = b"print('fixture')\n"
_blob = hashlib.sha1(f"blob {len(_source)}\0".encode() + _source).digest()
_tree = b"100644 source.py\0" + _blob
TREE = hashlib.sha1(f"tree {len(_tree)}\0".encode() + _tree).hexdigest()


def ref(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write_json(path, value):
    path.write_text(json.dumps(value))
    return ref(path)


def tar_files(path, content):
    with tarfile.open(path, "w:gz") as archive:
        for name, data in content.items():
            item = tarfile.TarInfo(name)
            item.mode, item.size = 0o600, len(data)
            archive.addfile(item, io.BytesIO(data))


@pytest.fixture
def closure(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    binary = runtime / "payload.py"
    binary.write_bytes(b"print('fixture')\n")
    binary.chmod(0o600)
    terms = tmp_path / "terms.txt"
    terms.write_bytes(b"Synthetic preserved test terms, not release evidence.")
    source = tmp_path / "source.tar.gz"
    tar_files(source, {"Image_Sorter/source.py": binary.read_bytes()})
    identity = {"dirty": False, "source_sha": SHA, "source_tree": TREE,
                "source_files": {"source.py": ref(binary)["sha256"]}}
    embedded = runtime / "build_identity.json"
    write_json(embedded, identity)
    embedded.chmod(0o600)
    value = {"schema_version": 1, "kind": "runtime-license-source-inventory",
             "engineering_complete": True, "unresolved": [], "legal_clearance": "not-asserted",
             "runtime_root": str(runtime), "runtime_files": [{"path": p.name, "sha256": ref(p)["sha256"]} for p in (binary, embedded)],
             "project": {"dirty": False, "source_sha": SHA, "source_tree": TREE, "archive": ref(source),
                         "files": {"source.py": ref(binary)["sha256"]}},
             "build_identity": identity,
             "packages": [{"id": "library", "version": "1", "license_expression": "MIT", "notices": [ref(terms)]}],
             "sources": [{"id": "library", "version": "1", "license_expression": "MIT", "archives": []}],
             "native_files": []}
    path = tmp_path / "closure.json"
    write_json(path, value)
    return value, path, runtime, binary, terms


def test_closure_rechecks_preserved_bytes_and_permissive_duties(closure):
    value, path, runtime, _binary, _terms = closure
    result = guard.verify_closure(ref(path), path.parent, SHA, TREE, core=True)
    assert result[0] == value and result[1] == runtime and result[3] == {"library"}


@pytest.mark.parametrize("change", ["unresolved", "engineering-false", "clearance-true", "dirty", "wrong-source",
                                    "source-archive-drift", "runtime-drift", "extra-runtime", "notice-drift",
                                    "gpl-without-source", "gpl-override-false", "unmapped-elf", "source-inventory-lie",
                                    "self-consistent-wrong-source-tree"])
def test_claims_cannot_override_source_or_runtime_defects(closure, change):
    value, path, runtime, binary, terms = closure
    if change == "unresolved":
        value["unresolved"] = ["model grant unresolved"]
    elif change == "engineering-false":
        value["engineering_complete"] = False
    elif change == "clearance-true":
        value["legal_clearance"] = True
    elif change == "dirty":
        value["project"]["dirty"] = True
    elif change == "wrong-source":
        value["project"]["source_sha"] = "9" * 40
    elif change == "source-archive-drift":
        Path(value["project"]["archive"]["path"]).write_bytes(b"changed")
    elif change == "runtime-drift":
        binary.write_bytes(b"changed")
    elif change == "extra-runtime":
        (runtime / "unmapped.so").write_bytes(b"extra")
    elif change == "notice-drift":
        terms.write_bytes(b"changed")
    elif change in {"gpl-without-source", "gpl-override-false"}:
        value["sources"][0].update(license_expression="GPL-3.0-only", corresponding_source_required=False)
    elif change == "source-inventory-lie":
        value["project"]["files"]["source.py"] = "0" * 64
    elif change == "self-consistent-wrong-source-tree":
        data = b"self-consistent altered source"
        source = Path(value["project"]["archive"]["path"])
        tar_files(source, {"Image_Sorter/source.py": data})
        value["project"]["archive"] = ref(source)
        value["project"]["files"]["source.py"] = hashlib.sha256(data).hexdigest()
    else:
        binary.write_bytes(b"\x7fELFunmapped")
        value["runtime_files"][0]["sha256"] = ref(binary)["sha256"]
    write_json(path, value)
    with pytest.raises(guard.PublicationError):
        guard.verify_closure(ref(path), path.parent, SHA, TREE, core=True)


@pytest.mark.parametrize("kind", ["wheel", "onedir", "pack"])
def test_actual_archive_members_must_match_runtime_not_just_a_claimed_digest(closure, tmp_path, kind):
    value, _path, runtime, binary, _terms = closure
    archive = tmp_path / ("delivery.whl" if kind == "wheel" else "delivery.tar.gz")
    if kind == "wheel":
        with zipfile.ZipFile(archive, "w") as output:
            for item in value["runtime_files"]:
                output.write(runtime / item["path"], item["path"])
    else:
        tar_files(archive, {item["path"]: (runtime / item["path"]).read_bytes() for item in value["runtime_files"]})
    guard.verify_archive_delivery(archive, runtime, value["runtime_files"], kind=kind)
    if kind == "wheel":
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(binary.name, b"different bytes")
    else:
        tar_files(archive, {binary.name: b"different bytes"})
    with pytest.raises(guard.PublicationError):
        guard.verify_archive_delivery(archive, runtime, value["runtime_files"], kind=kind)


@pytest.mark.parametrize("status", [None, "local-qualification-only", "local-qualification-only; redistribution pending", "approved", True])
def test_local_or_self_declared_catalogue_cannot_publish(tmp_path, status):
    catalog = json.dumps({"schema_version": 1, "components": [], "publication_status": status}).encode()
    with pytest.raises(guard.PublicationError, match="publication-eligible"):
        guard.verify_publication(catalog, tmp_path / "invented.json", SHA, TREE)


def test_complete_native_matrix_does_not_replace_distribution_manifest():
    catalog = json.dumps({"schema_version": 1, "components": [], "publication_status": "evidence-gated-release"}).encode()
    with pytest.raises(guard.PublicationError, match="distribution manifest"):
        guard.verify_publication(catalog, None, SHA, TREE)


def test_distribution_evidence_never_replaces_native_qualification():
    with pytest.raises(guard.PublicationError, match="native qualification"):
        guard.verify_release(b"{}", None, None, SHA, TREE)


def test_normal_ci_preserves_builds_but_cannot_upload_release_binaries():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    assert "python build_desktop.py --appimage" in workflow and "python -m build --wheel" in workflow
    assert "name: imagesorter-linux-receipts-" in workflow
    assert all(pattern not in workflow for pattern in ("dist/*.whl", "dist/*.AppImage", "dist/*.tar.gz"))
    release = (root / ".github/workflows/release.yml").read_text()
    assert "--qualification dist/native-qualification.json" in release
    assert "--distribution dist/publication-evidence.json" in release
    assert "needs: qualify-and-build" in release


@pytest.mark.parametrize("change", [None, "core-bytes", "pack-bytes", "different-descriptor"])
def test_native_and_distribution_must_bind_the_same_exact_artifacts(tmp_path, change):
    descriptor = {"id": "model", "version": "1", "sha256": "b" * 64, "files": {"model": {"sha256": "c" * 64}}}
    catalog = json.dumps({"components": [descriptor]}).encode()
    manifest = write_json(tmp_path / "component.json", descriptor)
    delivery = tmp_path / "delivery"
    delivery.write_bytes(b"delivered bytes, not the installed launcher")
    delivery_ref = ref(delivery)
    build = write_json(tmp_path / "build.json", {"delivery": delivery_ref})
    native = {"artifacts": [{"kind": kind, "sha256": "a" * 64, "build": build} for kind in guard.CORE],
              "components": [{"id": "model", "manifest": manifest}]}
    publication = {"delivery_sha256": {**{kind: delivery_ref["sha256"] for kind in guard.CORE}, "model@1": "b" * 64}}
    if change == "core-bytes":
        publication["delivery_sha256"]["wheel"] = "d" * 64
    elif change == "pack-bytes":
        publication["delivery_sha256"]["model@1"] = "d" * 64
    elif change == "different-descriptor":
        catalog = json.dumps({"components": [{**descriptor, "files": {"other": {"sha256": "e" * 64}}}]}).encode()
    if change is None:
        guard.verify_native_binding(native, catalog, publication, tmp_path)
    else:
        with pytest.raises(guard.PublicationError, match="different"):
            guard.verify_native_binding(native, catalog, publication, tmp_path)


def test_review_requires_preserved_terms_and_scoped_reason_not_clearance_boolean(tmp_path):
    artifact_sha = "a" * 64
    path = tmp_path / "review.json"
    value = {"kind": "distribution-obligations-review", "artifact_sha256": artifact_sha,
             "reviewer": "test reviewer", "reviewed_at": "2026-09-08", "unresolved": [],
             "legal_clearance": True, "obligations": [{"id": "model", "disposition": "reviewed-for-distribution",
                                                        "unresolved": [], "terms": []}]}
    write_json(path, value)
    with pytest.raises(guard.PublicationError, match="missing terms"):
        guard.verify_review(ref(path), tmp_path, artifact_sha, {"model"})


@pytest.mark.parametrize("change", [None, "missing", "labels-omitted", "generic-library", "changed-asset", "wrong-role"])
def test_model_assets_cannot_hide_behind_generic_runtime_library_reviews(change):
    descriptor = {"id": "ai.mobilenet-v2", "files": {"mobilenetv2.onnx": {"sha256": "a" * 64}, "labels.txt": {"sha256": "b" * 64}}}
    mappings = [{"path": "mobilenetv2.onnx", "sha256": "a" * 64, "role": "model-weights", "source_id": "exact-model"},
                {"path": "labels.txt", "sha256": "b" * 64, "role": "model-labels", "source_id": "exact-labels"}]
    closure = {"sources": [{"id": "exact-model"}, {"id": "exact-labels"}], "asset_mappings": mappings}
    if change == "missing":
        del closure["asset_mappings"]
    elif change == "labels-omitted":
        mappings.pop()
    elif change == "generic-library":
        mappings[1]["source_id"] = mappings[0]["source_id"]
    elif change == "changed-asset":
        mappings[1]["sha256"] = "c" * 64
    elif change == "wrong-role":
        mappings[1]["role"] = "runtime-code"
    if change is None:
        assert guard.model_asset_obligations(closure, descriptor) == {
            "exact-model": {"asset_sha256": "a" * 64, "asset_role": "model-weights"},
            "exact-labels": {"asset_sha256": "b" * 64, "asset_role": "model-labels"}}
    else:
        with pytest.raises(guard.PublicationError):
            guard.model_asset_obligations(closure, descriptor)


def test_model_review_requires_separately_bound_asset_digest_and_role(tmp_path):
    terms = tmp_path / "terms.txt"
    terms.write_text("Synthetic terms fixture only, not a grant for any real model")
    decision = {"component_id": "exact-model", "artifact_sha256": "a" * 64, "unresolved": [],
                "reviewer": "TEST", "reviewed_at": "2026-09-08", "basis": "Synthetic structural test only"}
    decision_path = tmp_path / "decision.json"
    review = {"kind": "distribution-obligations-review", "artifact_sha256": "a" * 64, "reviewer": "TEST",
              "reviewed_at": "2026-09-08", "unresolved": [], "obligations": [{"id": "exact-model",
                  "disposition": "reviewed-for-distribution", "unresolved": [], "terms": [ref(terms)],
                  "decision": write_json(decision_path, decision)}]}
    review_path = tmp_path / "review.json"
    expected = {"exact-model": {"asset_sha256": "b" * 64, "asset_role": "model-weights"}}
    write_json(review_path, review)
    with pytest.raises(guard.PublicationError, match="exact weights or labels"):
        guard.verify_review(ref(review_path), tmp_path, "a" * 64, {"exact-model"}, asset_obligations=expected)
    decision.update(expected["exact-model"])
    review["obligations"][0]["decision"] = write_json(decision_path, decision)
    write_json(review_path, review)
    assert guard.verify_review(ref(review_path), tmp_path, "a" * 64, {"exact-model"}, asset_obligations=expected) == {ref(terms)["sha256"]}


@pytest.mark.parametrize("component,minimum", [("codec.heif-avif", 3), ("codec.camera-raw", 3),
                                             ("viewer.animation-multipage", 3), ("provider.onnx-nvidia", 6)])
def test_policy_descriptor_must_match_the_qualified_component_contract(component, minimum):
    descriptor = {"id": component, "reader_policy_version": 1, "min_landlock_abi": minimum}
    guard.verify_reader_descriptor(descriptor)
    for bad in ({"reader_policy_version": None}, {"reader_policy_version": True}, {"min_landlock_abi": minimum - 1}):
        with pytest.raises(guard.PublicationError):
            guard.verify_reader_descriptor({**descriptor, **bad})
    guard.verify_reader_descriptor({"id": "ai.mobilenet-v2", "reader_policy_version": None, "min_landlock_abi": None})
    with pytest.raises(guard.PublicationError):
        guard.verify_reader_descriptor({"id": "ai.mobilenet-v2"})


@pytest.mark.parametrize("gpu", [False, True])
@pytest.mark.parametrize("change", [None, "disabled", "old-abi", "wrong-helper", "external-write", "socket-access", "device-escape"])
def test_actual_reader_policy_must_match_execution_not_just_descriptor(gpu, change):
    descriptor = {"id": "provider.onnx-nvidia" if gpu else "codec.heif-avif", "version": "1", "sha256": "a" * 64}
    policy = {"enforced": True, "policy_version": 1, "landlock_abi": 6 if gpu else 3,
              "network_and_mutation_ipc": "denied", "external_metadata_writes": "denied",
              "own_task_proc_writes": gpu, "device_write_paths": ["/dev/nvidia0"] if gpu else [], "scratch": "/disposable/scratch"}
    reply = {"component_id": descriptor["id"], "component_version": "1", "component_sha256": "a" * 64, "reader_isolation": policy}
    if change == "disabled":
        policy["enforced"] = False
    elif change == "old-abi":
        policy["landlock_abi"] -= 1
    elif change == "wrong-helper":
        reply["component_sha256"] = "b" * 64
    elif change == "external-write":
        policy["external_metadata_writes"] = "allowed"
    elif change == "socket-access":
        policy["network_and_mutation_ipc"] = "allowed"
    elif change == "device-escape":
        policy["device_write_paths"] = ["/dev/fb0"]
    if change is None:
        guard.verify_reader_policy(reply, descriptor)
    else:
        with pytest.raises(guard.PublicationError):
            guard.verify_reader_policy(reply, descriptor)


@pytest.mark.parametrize("change", [None, "missing-results", "different-fixture", "inferred-input", "different-helper",
                                  "missing-input", "input-none", "input-boolean", "input-number",
                                  "input-uppercase", "input-malformed", "input-short", "missing-input-other-fixture",
                                  "missing-ok", "failed-ok", "numeric-ok", "string-ok", "error-present"])
def test_native_format_evidence_binds_real_reply_policy_and_fixture(tmp_path, change):
    descriptor = {"id": "codec.heif-avif", "version": "1", "sha256": "a" * 64}
    fixture = tmp_path / "fixture.heif"
    fixture.write_bytes(b"synthetic fixture")
    reply = {"ok": True, "component_id": descriptor["id"], "component_version": "1", "component_sha256": "a" * 64,
             "input_sha256": ref(fixture)["sha256"],
             "reader_isolation": {"enforced": True, "policy_version": 1, "landlock_abi": 3,
                 "network_and_mutation_ipc": "denied", "external_metadata_writes": "denied",
                 "own_task_proc_writes": False, "device_write_paths": [], "scratch": "/disposable/scratch"}}
    if change == "inferred-input":
        reply["input_sha256"] = "b" * 64
    elif change in {"missing-input", "missing-input-other-fixture"}:
        reply.pop("input_sha256")
    elif change in {"input-none", "input-boolean", "input-number", "input-uppercase", "input-malformed", "input-short"}:
        reply["input_sha256"] = {"input-none": None, "input-boolean": True, "input-number": 123,
                                "input-uppercase": ref(fixture)["sha256"].upper(), "input-malformed": "z" * 64,
                                "input-short": ref(fixture)["sha256"][:-1]}[change]
    elif change == "missing-ok":
        reply.pop("ok")
    elif change in {"failed-ok", "numeric-ok", "string-ok"}:
        reply["ok"] = {"failed-ok": False, "numeric-ok": 1, "string-ok": "true"}[change]
    elif change == "error-present":
        reply["error"] = "Decoder failed"
    elif change == "different-helper":
        reply["component_version"] = "2"
    envelope = {"kind": "reader-result-observation", "fixture": ref(fixture),
                "reply": write_json(tmp_path / "reply.json", reply)}
    if change in {"different-fixture", "missing-input-other-fixture"}:
        other = tmp_path / "other.heif"
        other.write_bytes(b"other fixture")
        envelope["fixture"] = ref(other)
    observation = {"formats": {"heif": {"fixture": ref(fixture)}},
                   "reader_results": [write_json(tmp_path / "envelope.json", envelope)]}
    if change == "missing-input-other-fixture":
        observation["formats"]["heif"]["fixture"] = envelope["fixture"]
    if change == "missing-results":
        observation.pop("reader_results")
    native = {"components": [{"id": descriptor["id"], "manifest": write_json(tmp_path / "descriptor.json", descriptor)}],
              "cases": [{"id": "COMPONENT:" + descriptor["id"], "observation": write_json(tmp_path / "observation.json", observation)}]}
    catalog = json.dumps({"components": [descriptor]}).encode()
    if change is None:
        guard.verify_native_readers(native, catalog, tmp_path)
    else:
        with pytest.raises(guard.PublicationError):
            guard.verify_native_readers(native, catalog, tmp_path)


@pytest.mark.parametrize("change", [None, "no-helpers", "omitted-helper", "changed-helper", "dirty-build",
                                    "wrong-build-source", "missing-adoption", "wrong-final-adoption"])
def test_pack_adoption_binds_complete_helpers_without_source_hash_cycle(change):
    files = {name: "a" * 64 for name in guard.PACK_SOURCE_PATHS}
    project = {"source_sha": "3" * 40, "source_tree": "4" * 40}
    provenance = {"source": {"head": project["source_sha"], "dirty": False, "files": dict(files)}}
    helpers = {name: value for name, value in files.items() if name.startswith("src/")}
    final_sources = dict(helpers)
    relation = {"kind": "pack-source-adoption", "pack_source_sha": project["source_sha"],
                "pack_source_tree": project["source_tree"], "final_source_sha": SHA,
                "final_source_tree": TREE, "unchanged_helper_files": helpers}
    if change == "no-helpers":
        provenance["source"]["files"] = {"README.md": "a" * 64}
    elif change == "omitted-helper":
        del provenance["source"]["files"]["src/imagesorter/component_worker.py"]
    elif change == "changed-helper":
        final_sources["src/imagesorter/apng_frames.py"] = "b" * 64
    elif change == "dirty-build":
        provenance["source"]["dirty"] = True
    elif change == "wrong-build-source":
        provenance["source"]["head"] = "5" * 40
    elif change == "missing-adoption":
        relation = None
    elif change == "wrong-final-adoption":
        relation["final_source_sha"] = "6" * 40
    if change is None:
        guard.verify_pack_source_relation(provenance, project, files, relation, final_sources, SHA, TREE)
    else:
        with pytest.raises(guard.PublicationError):
            guard.verify_pack_source_relation(provenance, project, files, relation, final_sources, SHA, TREE)


def captured_project(tmp_path, name, content, head):
    directory = tmp_path / name
    directory.mkdir()
    for relative, data in content.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)
    subprocess.run(["git", "init", "--quiet", str(directory)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(directory), "add", "--", "."], check=True, capture_output=True)
    tree = subprocess.check_output(["git", "-C", str(directory), "write-tree"], text=True).strip()
    archive = tmp_path / (name + ".tar.gz")
    tar_files(archive, {"Image_Sorter/" + relative: data for relative, data in content.items()})
    return {"source_sha": head, "source_tree": tree, "dirty": False, "archive": ref(archive),
            "files": {relative: hashlib.sha256(data).hexdigest() for relative, data in content.items()}}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Complete delivery gate is Linux-only")
def test_complete_distribution_graph_passes_real_archive_checks_then_detects_missing_support(tmp_path):
    """Synthetic engineering evidence, never asserted to be actual release rights."""
    assert shutil.which("mksquashfs") and shutil.which("unsquashfs")
    runtime_input = tmp_path / "runtime-input"
    runtime_input.write_bytes(elf_prefix())
    source = {name: ("synthetic source: " + name).encode() for name in guard.PACK_SOURCE_PATHS}
    source["build_desktop.py"] = f'APPIMAGE_RUNTIME_X86_64_SHA256 = "{ref(runtime_input)["sha256"]}"\n'.encode()
    project_p = captured_project(tmp_path, "pack-source", source, "3" * 40)
    pack = tmp_path / "pack"
    pack.mkdir()
    provenance = {"schema_version": 1, "component_id": "model", "source": {"head": project_p["source_sha"], "dirty": False,
                             "files": project_p["files"]}}
    write_json(pack / "provenance.json", provenance)
    (pack / "provenance.json").chmod(0o600)
    pack_artifact = tmp_path / "model-pack.tar.gz"
    tar_files(pack_artifact, {"provenance.json": (pack / "provenance.json").read_bytes()})
    descriptor = {"id": "model", "version": "1", "sha256": ref(pack_artifact)["sha256"],
                  "reader_policy_version": 1, "min_landlock_abi": 3,
                  "archive_size": pack_artifact.stat().st_size, "installed_size": (pack / "provenance.json").stat().st_size,
                  "provenance": "provenance.json", "files": {"provenance.json": {
                      "sha256": ref(pack / "provenance.json")["sha256"], "size": (pack / "provenance.json").stat().st_size,
                      "mode": 0o600}}}
    catalog = json.dumps({"schema_version": 1, "publication_status": "evidence-gated-release", "components": [descriptor]}).encode()
    source["src/imagesorter/resources/component_catalog.json"] = catalog
    project_c = captured_project(tmp_path, "core-source", source, SHA)
    tree = project_c["source_tree"]
    runtime = tmp_path / "core-runtime"
    (runtime / "imagesorter/resources").mkdir(parents=True)
    (runtime / "imagesorter/resources/component_catalog.json").write_bytes(catalog)
    write_json(runtime / "build_identity.json", {"dirty": False, "source_sha": SHA, "source_tree": tree,
                                                "source_files": project_c["files"]})
    for path in runtime.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    core_files = {path.relative_to(runtime).as_posix(): path.read_bytes() for path in runtime.rglob("*") if path.is_file()}
    wheel, onedir, appimage = tmp_path / "core.whl", tmp_path / "core.tar.gz", tmp_path / "core.AppImage"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in core_files:
            archive.write(runtime / name, name)
    tar_files(onedir, core_files)
    squashfs = tmp_path / "core.squashfs"
    subprocess.run(["mksquashfs", str(runtime), str(squashfs), "-noappend", "-no-progress", "-processors", "1"], check=True, capture_output=True)
    appimage.write_bytes(runtime_input.read_bytes() + squashfs.read_bytes())
    terms = tmp_path / "synthetic-terms.txt"
    terms.write_bytes(b"Synthetic MIT fixture terms, not actual release rights evidence.")
    deliveries = []
    for key, artifact, root, project in [("wheel", wheel, runtime, project_c), ("onedir", onedir, runtime, project_c),
                                          ("appimage", appimage, runtime, project_c), ("model@1", pack_artifact, pack, project_p)]:
        obligations = ["library"] + (["appimage-runtime"] if key == "appimage" else [])
        closure = {"schema_version": 1, "kind": "runtime-license-source-inventory", "engineering_complete": True,
                   "unresolved": [], "legal_clearance": "not-asserted", "runtime_root": str(root),
                   "runtime_files": [{"path": path.relative_to(root).as_posix(), "sha256": ref(path)["sha256"]}
                                     for path in root.rglob("*") if path.is_file()], "project": project,
                   "delivery_sha256": ref(artifact)["sha256"],
                   "sources": [{"id": item, "version": "1", "license_expression": "MIT", "archives": []} for item in obligations],
                   "packages": [{"id": item, "version": "1", "license_expression": "MIT", "notices": [ref(terms)]} for item in obligations],
                   "native_files": []}
        if key in guard.CORE:
            closure["build_identity"] = json.loads((runtime / "build_identity.json").read_text())
        else:
            closure["component_identity"] = {"descriptor": descriptor, "provenance": provenance,
                "provenance_path": "provenance.json", "provenance_sha256": ref(pack / "provenance.json")["sha256"]}
        rows = []
        for obligation in obligations:
            decision = {"component_id": obligation, "artifact_sha256": ref(artifact)["sha256"], "unresolved": [],
                        "reviewer": "TEST FIXTURE", "reviewed_at": "2026-09-08", "basis": "Synthetic engineering test only"}
            rows.append({"id": obligation, "disposition": "reviewed-for-distribution", "unresolved": [], "terms": [ref(terms)],
                         "decision": write_json(tmp_path / (key + obligation + "-decision.json"), decision)})
        review = {"kind": "distribution-obligations-review", "artifact_sha256": ref(artifact)["sha256"],
                  "reviewer": "TEST FIXTURE", "reviewed_at": "2026-09-08", "unresolved": [], "obligations": rows}
        item = {"id": key, "artifact": ref(artifact), "closure": write_json(tmp_path / (key + "-closure.json"), closure),
                "review": write_json(tmp_path / (key + "-review.json"), review)}
        if key == "appimage":
            item["appimage_runtime"] = ref(runtime_input)
        if key not in guard.CORE:
            item["source_adoption"] = {"kind": "pack-source-adoption", "pack_source_sha": project_p["source_sha"],
                "pack_source_tree": project_p["source_tree"], "final_source_sha": SHA, "final_source_tree": tree,
                "unchanged_helper_files": {name: value for name, value in project_p["files"].items() if name.startswith("src/")}}
        deliveries.append(item)
    support = tmp_path / "support.tar.gz"
    support_content = {Path(project["archive"]["path"]).name: Path(project["archive"]["path"]).read_bytes() for project in (project_p, project_c)}
    support_content[terms.name] = terms.read_bytes()
    tar_files(support, support_content)
    manifest = {"schema_version": 1, "kind": "imagesorter-publication-evidence", "source_sha": SHA,
                "source_tree": tree, "catalog_sha256": hashlib.sha256(catalog).hexdigest(), "unresolved": [],
                "deliveries": deliveries, "support_archive": ref(support)}
    path = tmp_path / "publication.json"
    write_json(path, manifest)
    result = guard.verify_publication(catalog, path, SHA, tree)
    assert result["eligible"] and result["deliveries"] == 4 and len(result["publication_files"]) == 5
    tar_files(support, {terms.name: terms.read_bytes()})
    manifest["support_archive"] = ref(support)
    write_json(path, manifest)
    with pytest.raises(guard.PublicationError, match="omits required source"):
        guard.verify_publication(catalog, path, SHA, tree)


def elf_prefix():
    ident = b"\x7fELF\x02\x01\x01\0AI\x02" + b"\0" * 5
    return (struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 62, 1, 0, 0, 64, 0, 64, 56, 0, 64, 1, 0)
            + struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="AppImage publication qualification is Linux-only")
def test_independent_appimage_reader_checks_actual_squashfs_without_executing_artifact(closure, tmp_path):
    assert shutil.which("mksquashfs") and shutil.which("unsquashfs"), "Linux publication tests require squashfs-tools"
    value, _path, runtime, binary, _terms = closure
    squashfs = tmp_path / "payload.squashfs"
    subprocess.run(["mksquashfs", str(runtime), str(squashfs), "-noappend", "-no-progress", "-processors", "1"],
                   check=True, capture_output=True)
    prefix = tmp_path / "source-approved-runtime"
    prefix.write_bytes(elf_prefix())
    artifact = tmp_path / "fixture.AppImage"
    artifact.write_bytes(prefix.read_bytes() + squashfs.read_bytes())
    artifact.chmod(0o600)  # Intentionally non-executable: verifier must never run it.
    guard.verify_archive_delivery(artifact, runtime, value["runtime_files"], kind="appimage",
                                  runtime_input=prefix, expected_runtime_sha=ref(prefix)["sha256"])
    binary.write_bytes(b"unrelated runtime")
    with pytest.raises(guard.PublicationError, match="size or mode|bytes differ"):
        guard.verify_archive_delivery(artifact, runtime, value["runtime_files"], kind="appimage",
                                      runtime_input=prefix, expected_runtime_sha=ref(prefix)["sha256"])


def test_appimage_prefix_cannot_select_arbitrary_embedded_magic(tmp_path):
    artifact = tmp_path / "malformed.AppImage"
    artifact.write_bytes(elf_prefix() + b"junkhsqs")
    with pytest.raises(guard.PublicationError, match="actual runtime ELF boundary"):
        guard.appimage_offset(artifact)


@pytest.mark.parametrize("phase", PHASES)
def test_every_remote_phase_stops_before_identity_or_reconciliation_without_local_qualification(phase):
    controller = object.__new__(Controller)
    controller.plan = {"candidate_sha": SHA}
    controller.qualification = None
    controller.distribution = None
    controller.identity = lambda: pytest.fail("Remote preflight reached before local qualification")
    with pytest.raises(GateError, match="qualification"):
        controller.run(phase)


@pytest.mark.parametrize("phase", PHASES)
def test_every_remote_phase_requires_distribution_even_after_native_success(phase):
    controller = object.__new__(Controller)
    controller.plan = {"candidate_sha": SHA}
    controller.qualification = "native evidence already validated in this boundary test"
    controller.distribution = None
    controller.native_gate = lambda _sha: {"validated": True}
    controller.identity = lambda: pytest.fail("Remote preflight reached before distribution gate")
    with pytest.raises(GateError, match="distribution"):
        controller.run(phase)


@pytest.mark.parametrize("phase", PHASES)
def test_publication_uses_durable_actual_merge_identity_only_after_merge(phase):
    from types import SimpleNamespace
    controller = object.__new__(Controller)
    controller.plan = {"candidate_sha": SHA}
    merged = "8" * 40
    controller.journal = SimpleNamespace(events=[{"operation": "merge", "stage": "result", "merge_sha": merged}])
    post_merge = phase in {"verify-merged", "rename", "resolve", "prune", "finalize"}
    assert controller.publication_source(phase) == (merged if post_merge else SHA)
    if post_merge:
        controller.journal.events = [{"operation": "merge", "stage": "intent", "desired": "merge-commit"}]
        with pytest.raises(GateError, match="Resume merge"):
            controller.publication_source(phase)
