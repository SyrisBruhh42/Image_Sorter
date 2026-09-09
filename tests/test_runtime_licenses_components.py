"""Synthetic collector/guard interoperability; never real redistribution clearance."""

import copy
import email.message
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "component_runtime_licenses",
    Path(__file__).parents[1] / "scripts/collect_runtime_licenses.py",
)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def fixture(tmp_path, component_id="codec.camera-raw"):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sources = {
        name: hashlib.sha256(name.encode()).hexdigest()
        for name in collector.PACK_SOURCE_PATHS
    }
    project = {
        "dirty": False,
        "source_sha": "a" * 40,
        "source_tree": "b" * 40,
        "files": sources,
    }
    provenance = {
        "schema_version": 1,
        "component_id": component_id,
        "source": {"dirty": False, "head": project["source_sha"], "files": sources},
    }
    (runtime / "provenance.json").write_text(json.dumps(provenance))
    for name in (
        ("mobilenetv2.onnx", "labels.txt")
        if component_id == "ai.mobilenet-v2"
        else ("helper",)
    ):
        (runtime / name).write_bytes(b"synthetic " + name.encode())
    delivery = tmp_path / "pack.tar.gz"
    delivery.write_bytes(
        b"synthetic delivery: publication guard independently parses real archives"
    )
    descriptor = {
        "id": component_id,
        "version": "test",
        "protocol_version": 1,
        "provenance": "provenance.json",
        "sha256": collector.digest(delivery),
        "archive_size": delivery.stat().st_size,
        "files": {},
    }
    refresh(runtime, descriptor)
    return runtime, descriptor, project, delivery


def refresh(runtime, descriptor):
    descriptor["files"] = {
        path.name: {
            "sha256": collector.digest(path),
            "size": path.stat().st_size,
            "mode": path.stat().st_mode & 0o777,
        }
        for path in runtime.iterdir()
    }
    return [
        {"path": name, "sha256": row["sha256"]}
        for name, row in descriptor["files"].items()
    ]


def test_pack_identity_accepts_provenance_without_core_build_identity(tmp_path):
    runtime, descriptor, project, delivery = fixture(tmp_path)
    identity, unresolved = collector.component_identity(
        runtime, descriptor, refresh(runtime, descriptor), project, delivery
    )
    assert not unresolved
    assert identity["provenance"]["source"]["head"] == project["source_sha"]
    assert not (runtime / "build_identity.json").exists()


@pytest.mark.parametrize(
    "mutation", ["dirty", "head", "missing-helper", "wrong-helper", "vacuous"]
)
def test_pack_source_cannot_be_substituted_or_vacuously_matched(tmp_path, mutation):
    runtime, descriptor, project, delivery = fixture(tmp_path)
    path = runtime / "provenance.json"
    data = json.loads(path.read_text())
    if mutation == "dirty":
        data["source"]["dirty"] = True
    elif mutation == "head":
        data["source"]["head"] = "c" * 40
    elif mutation == "missing-helper":
        data["source"]["files"].pop("src/imagesorter/reader_sandbox.py")
    elif mutation == "wrong-helper":
        data["source"]["files"]["src/imagesorter/component_worker.py"] = "f" * 64
    else:
        data["source"]["files"] = {"README.md": "e" * 64}
    path.write_text(json.dumps(data))
    _, unresolved = collector.component_identity(
        runtime, descriptor, refresh(runtime, descriptor), project, delivery
    )
    assert "clean captured build-source" in unresolved[0]


@pytest.mark.parametrize(
    "mutation", ["hash", "size", "mode", "extra", "identity", "source-type", "protocol"]
)
def test_pack_descriptor_and_provenance_malformed_fail(tmp_path, mutation):
    runtime, descriptor, project, delivery = fixture(tmp_path)
    files = refresh(runtime, descriptor)
    if mutation in {"hash", "size", "mode"}:
        descriptor["files"]["helper"][{"hash": "sha256"}.get(mutation, mutation)] = {
            "hash": "0" * 64,
            "size": 0,
            "mode": 0,
        }[mutation]
    elif mutation == "extra":
        files.append({"path": "extra", "sha256": "d" * 64})
    elif mutation in {"identity", "source-type"}:
        path = runtime / "provenance.json"
        data = json.loads(path.read_text())
        data["component_id" if mutation == "identity" else "source"] = "wrong"
        path.write_text(json.dumps(data))
        files = refresh(runtime, descriptor)
    else:
        descriptor["protocol_version"] = True
    with pytest.raises(ValueError):
        collector.component_identity(runtime, descriptor, files, project, delivery)


def test_pack_delivery_required_and_exact(tmp_path):
    runtime, descriptor, project, delivery = fixture(tmp_path)
    files = refresh(runtime, descriptor)
    _, unresolved = collector.component_identity(
        runtime, descriptor, files, project, None
    )
    assert "delivery archive absent" in unresolved[0]
    delivery.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="delivery archive differs"):
        collector.component_identity(runtime, descriptor, files, project, delivery)


def asset_map(descriptor):
    return {
        "components": [{"id": "weights"}, {"id": "labels"}],
        "asset_mappings": [
            {
                "path": name,
                "sha256": descriptor["files"][name]["sha256"],
                "role": role,
                "source_id": source,
            }
            for name, role, source in [
                ("mobilenetv2.onnx", "model-weights", "weights"),
                ("labels.txt", "model-labels", "labels"),
            ]
        ],
    }


def test_model_mapping_retains_distinct_exact_asset_obligations(tmp_path):
    runtime, descriptor, _, _ = fixture(tmp_path, "ai.mobilenet-v2")
    source_map = asset_map(descriptor)
    rows, unresolved = collector.asset_inventory(runtime, source_map, descriptor)
    assert not unresolved
    assert rows == source_map["asset_mappings"]


@pytest.mark.parametrize("mutation", ["missing", "same-source", "role", "extra"])
def test_model_mapping_missing_or_collapsed_obligations_unresolved(tmp_path, mutation):
    runtime, descriptor, _, _ = fixture(tmp_path, "ai.mobilenet-v2")
    source_map = asset_map(descriptor)
    if mutation == "missing":
        source_map["asset_mappings"].pop()
    elif mutation == "same-source":
        source_map["asset_mappings"][1]["source_id"] = "weights"
    elif mutation == "role":
        source_map["asset_mappings"][1]["role"] = "ordinary-library"
    else:
        source_map["asset_mappings"].append(
            {
                "path": "provenance.json",
                "role": "data",
                "source_id": "labels",
                "sha256": descriptor["files"]["provenance.json"]["sha256"],
            }
        )
    _, unresolved = collector.asset_inventory(runtime, source_map, descriptor)
    assert "distinct exact-asset" in unresolved[0]


@pytest.mark.parametrize("mutation", ["hash", "source", "duplicate"])
def test_asset_mapping_false_bytes_or_identity_raise(tmp_path, mutation):
    runtime, descriptor, _, _ = fixture(tmp_path, "ai.mobilenet-v2")
    source_map = asset_map(descriptor)
    if mutation == "hash":
        source_map["asset_mappings"][0]["sha256"] = "0" * 64
    elif mutation == "source":
        source_map["asset_mappings"][0]["source_id"] = "not-in-sources"
    else:
        source_map["asset_mappings"].append(
            copy.deepcopy(source_map["asset_mappings"][0])
        )
    with pytest.raises(ValueError):
        collector.asset_inventory(runtime, source_map, descriptor)


def test_collect_explicit_pack_mode_succeeds_without_weakening_core(
    tmp_path, monkeypatch
):
    runtime, descriptor, project, delivery = fixture(tmp_path)
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setattr(collector, "project_snapshot", lambda *_: project)
    pack = collector.collect(
        site,
        runtime,
        tmp_path / "pack-output",
        [],
        {},
        tmp_path,
        delivery=delivery,
        component_descriptor=descriptor,
    )
    assert pack["engineering_complete"] is True
    assert pack["component_identity"]["descriptor"] == descriptor
    assert pack["build_identity"] is None
    core = collector.collect(site, runtime, tmp_path / "core-output", [], {}, tmp_path)
    assert core["engineering_complete"] is False
    assert any("embedded build identity" in row for row in core["unresolved"])


def test_requested_runtime_extras_are_resolved_not_omitted(tmp_path, monkeypatch):
    def distribution(name, requirements=(), extras=()):
        meta = email.message.Message()
        meta["Name"] = name
        for extra in extras:
            meta["Provides-Extra"] = extra
        return SimpleNamespace(
            metadata=meta, version="1.0", requires=list(requirements)
        )

    installed = [
        distribution("inference", ['driver>=1; extra == "cuda"'], ["cuda"]),
        distribution("driver"),
    ]
    monkeypatch.setattr(collector.metadata, "distributions", lambda **_: installed)
    assert set(collector.resolve_packages(tmp_path, ["inference"])) == {"inference"}
    assert set(collector.resolve_packages(tmp_path, ["inference[cuda]"])) == {
        "inference",
        "driver",
    }
    with pytest.raises(ValueError, match="Unknown runtime extras"):
        collector.resolve_packages(tmp_path, ["inference[typo]"])
