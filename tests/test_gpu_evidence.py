"""Canonical GPU execution evidence rejects enumeration and forged node counts."""
import copy

import pytest

from scripts import qualification_schema as SCHEMA


def reply():
    return {"ok": True, "provider": "CUDAExecutionProvider", "cuda_compute_events": 2,
            "actual_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
            "compute_nodes": [{"name": "conv_kernel_time", "provider": "CUDAExecutionProvider"},
                              {"name": "shape_kernel_time", "provider": "CPUExecutionProvider"},
                              {"name": "classifier_kernel_time", "provider": "CUDAExecutionProvider"}],
            "tensor_sha256": "a" * 64, "profile_sha256": "b" * 64}


def test_actual_mixed_cpu_cuda_nodes_are_accepted():
    SCHEMA.verify_gpu_execution(reply())


def test_actual_session_order_does_not_invent_a_constraint():
    value = reply()
    value["actual_providers"].reverse()
    SCHEMA.verify_gpu_execution(value)


@pytest.mark.parametrize(("field", "value"), [
    ("ok", None), ("ok", False), ("ok", 1), ("ok", "true"),
    ("error", "failed"), ("error", ""),
    ("fallback_reason", "CUDA failed; CPU was used"), ("fallback_reason", False),
    ("fallback_reason", {}),
    ("provider", "CPUExecutionProvider"), ("provider", True),
    ("actual_providers", None), ("actual_providers", "CUDAExecutionProvider"),
    ("actual_providers", []), ("actual_providers", [True]),
    ("actual_providers", ["CPUExecutionProvider"]),
    ("actual_providers", ["CUDAExecutionProvider", "CUDAExecutionProvider"]),
    ("actual_providers", ["CUDAExecutionProvider", "InventedExecutionProvider"]),
    ("actual_providers", ["CUDAExecutionProvider", "CPUExecutionProvider", "ExtraExecutionProvider"]),
    ("cuda_compute_events", None), ("cuda_compute_events", True),
    ("cuda_compute_events", 2.0), ("cuda_compute_events", "2"),
    ("cuda_compute_events", 0), ("cuda_compute_events", -1), ("cuda_compute_events", 1),
    ("cuda_compute_events", 3), ("compute_nodes", True),
    ("compute_nodes", "truthy"), ("compute_nodes", {}), ("compute_nodes", []),
    ("compute_nodes", [True]), ("compute_nodes", ["CUDAExecutionProvider"]),
    ("tensor_sha256", None), ("tensor_sha256", True), ("tensor_sha256", "a" * 63),
    ("tensor_sha256", "A" * 64), ("tensor_sha256", "g" * 64),
    ("profile_sha256", None), ("profile_sha256", 123), ("profile_sha256", "b" * 65),
    ("profile_sha256", "b" * 64 + "\n"),
])
def test_gpu_reply_rejects_malformed_or_inconsistent_field(field, value):
    document = reply()
    document[field] = value
    with pytest.raises(SCHEMA.QualificationError):
        SCHEMA.verify_gpu_execution(document)


@pytest.mark.parametrize("node", [
    {}, {"name": "x"}, {"name": "x", "provider": "CUDAExecutionProvider", "invented": True},
    {"name": "", "provider": "CUDAExecutionProvider"},
    {"name": "  ", "provider": "CUDAExecutionProvider"},
    {"name": " x ", "provider": "CUDAExecutionProvider"},
    {"name": "x" * 4097, "provider": "CUDAExecutionProvider"},
    {"name": "bad\x00name", "provider": "CUDAExecutionProvider"},
    {"name": True, "provider": "CUDAExecutionProvider"},
    {"name": "x", "provider": True},
    {"name": "x", "provider": "InventedExecutionProvider"},
])
def test_gpu_nodes_are_exact_named_actual_provider_objects(node):
    document = reply()
    document["compute_nodes"][0] = node
    with pytest.raises(SCHEMA.QualificationError):
        SCHEMA.verify_gpu_execution(document)


def test_duplicate_node_cannot_inflate_compute_count():
    document = reply()
    document["compute_nodes"][2] = copy.deepcopy(document["compute_nodes"][0])
    with pytest.raises(SCHEMA.QualificationError, match="duplicated"):
        SCHEMA.verify_gpu_execution(document)


def test_node_provider_must_belong_to_actual_session():
    document = reply()
    document["actual_providers"] = ["CUDAExecutionProvider"]
    with pytest.raises(SCHEMA.QualificationError, match="actual session"):
        SCHEMA.verify_gpu_execution(document)


@pytest.mark.parametrize("alias", ["node_providers", "session_providers"])
def test_legacy_or_conflicting_aliases_are_not_accepted(alias):
    document = reply()
    document[alias] = True
    with pytest.raises(SCHEMA.QualificationError, match="aliases"):
        SCHEMA.verify_gpu_execution(document)


def test_alias_only_legacy_fixture_is_not_execution_evidence():
    document = reply()
    document["node_providers"] = document.pop("compute_nodes")
    document["session_providers"] = document.pop("actual_providers")
    with pytest.raises(SCHEMA.QualificationError, match="aliases"):
        SCHEMA.verify_gpu_execution(document)


def test_provider_enumeration_without_compute_is_not_execution():
    document = reply()
    document.update(compute_nodes=[], cuda_compute_events=0)
    with pytest.raises(SCHEMA.QualificationError):
        SCHEMA.verify_gpu_execution(document)


def test_compute_list_is_bounded():
    document = reply()
    document["compute_nodes"] = [document["compute_nodes"][0]] * 100001
    with pytest.raises(SCHEMA.QualificationError):
        SCHEMA.verify_gpu_execution(document)
