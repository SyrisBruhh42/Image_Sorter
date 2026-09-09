"""Precision attestation and exactly one host-owned fallback, without GPU hardware."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from imagesorter import component_runtime, component_worker
from imagesorter.component_manager import ComponentError

PRECISION = {
    "policy_version": 1,
    "requested_use_tf32": "0",
    "observed_use_tf32_before": "0",
    "observed_use_tf32_after": "0",
    "internal_fallback_disabled": True,
}


@pytest.fixture
def worker_boundary(tmp_path, monkeypatch):
    """Replace only ORT's session boundary; run actual model checks/preprocessing."""
    monkeypatch.chdir(tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "mobilenetv2.onnx").write_bytes(b"bounded fake session model")
    (model / "labels.txt").write_text("cat\ndog\nbird\n")
    source = tmp_path / "input.png"
    Image.new("RGB", (16, 12), (20, 80, 160)).save(source)
    request = {"input_path": str(source), "model_dir": str(model), "threshold": 0,
               "model_sha256": hashlib.sha256((model / "mobilenetv2.onnx").read_bytes()).hexdigest(),
               "labels_sha256": hashlib.sha256((model / "labels.txt").read_bytes()).hexdigest()}
    state = {"sessions": [], "events": [], "before_option": "0", "after_option": "0",
             "before_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
             "after_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"], "compute_events": 1}

    class Session:
        def __init__(self, model_value, *, sess_options, providers, provider_options, enable_fallback=True):
            state["events"].append("construct")
            self.model = model_value
            self.options = sess_options
            self.providers = providers
            self.provider_options = provider_options
            self.constructor_fallback_enabled = enable_fallback
            assert enable_fallback is False, "Constructor retry was not disabled before session creation"
            self.gpu = providers[0] == "CUDAExecutionProvider"
            self.ran = False
            self.fallback_disabled = False
            state["sessions"].append(self)
            if state.get("creation_error"):
                raise RuntimeError("constructor failure")

        def disable_fallback(self):
            state["events"].append("disable_fallback")
            if state.get("disable_error"):
                raise RuntimeError("cannot disable fallback")
            self.fallback_disabled = True

        def get_providers(self):
            if not self.gpu:
                return ["CPUExecutionProvider"]
            return state["after_providers" if self.ran else "before_providers"]

        def get_provider_options(self):
            state["events"].append("options_after" if self.ran else "options_before")
            if state.get("missing_options"):
                return {}
            return {"CUDAExecutionProvider": {"use_tf32": state["after_option" if self.ran else "before_option"]}}

        def get_inputs(self):
            return [SimpleNamespace(name="input")]

        def run(self, _outputs, inputs):
            state["events"].append("run")
            assert not self.gpu or self.fallback_disabled, "Hidden runtime fallback was not disabled before compute"
            self.ran = True
            if state.get("run_error"):
                raise RuntimeError("actual provider failed")
            if isinstance(self.model, bytes):
                return [np.asarray([[1., 2.], [3., 4.]], dtype=np.float32)]
            assert inputs["input"].shape == (1, 3, 224, 224)
            return [np.asarray([[.7, .2, .1]], dtype=np.float32)]

        def end_profiling(self):
            path = Path(self.options.profile_file_prefix + ".json")
            path.write_text(json.dumps([
                {"name": "actual_kernel", "args": {"provider": "CUDAExecutionProvider"}}
                for _ in range(state["compute_events"])
            ]))
            return str(path)

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=lambda: SimpleNamespace(), InferenceSession=Session))
    monkeypatch.setattr(component_worker, "probe", lambda _cid: {"ready": True})
    return state, request


def test_worker_explicit_precision_and_no_internal_fallback(worker_boundary):
    state, request = worker_boundary
    result, payload = component_worker.infer(request, "provider.onnx-nvidia")
    session = state["sessions"][0]
    assert session.providers == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert session.provider_options == [{"use_tf32": "0"}, {}]
    assert session.constructor_fallback_enabled is False
    assert state["events"][:4] == ["construct", "disable_fallback", "options_before", "run"]
    assert "options_after" in state["events"]
    assert result["cuda_precision"] == PRECISION and result["provider"] == "CUDAExecutionProvider"
    assert type(result["provider"]) is str and result["cuda_compute_events"] == 1 and payload == b""


@pytest.mark.parametrize("value", [None, 0, False, True, "1", "false", {}, []])
def test_worker_rejects_unconfirmed_precision_before_compute(worker_boundary, value):
    state, request = worker_boundary
    state["before_option"] = value
    with pytest.raises(ValueError, match="full-precision"):
        component_worker.infer(request, "provider.onnx-nvidia")
    assert "run" not in state["events"]


@pytest.mark.parametrize("change", ["after_option", "after_providers", "before_providers", "missing_options"])
def test_worker_rejects_effective_state_change_or_constructor_fallback(worker_boundary, change):
    state, request = worker_boundary
    state[change] = "1" if change == "after_option" else (["CPUExecutionProvider"] if "providers" in change else True)
    with pytest.raises(ValueError, match="full-precision"):
        component_worker.infer(request, "provider.onnx-nvidia")
    assert state["events"].count("run") == (1 if change.startswith("after") else 0)
    assert len(state["sessions"]) == 1


@pytest.mark.parametrize("failure", ["creation_error", "disable_error", "run_error"])
def test_worker_session_errors_never_retry_inside_helper(worker_boundary, failure):
    state, request = worker_boundary
    state[failure] = True
    with pytest.raises(RuntimeError):
        component_worker.infer(request, "provider.onnx-nvidia")
    assert len(state["sessions"]) == 1 and state["events"].count("run") <= 1


def test_worker_precision_does_not_replace_actual_compute_evidence(worker_boundary):
    state, request = worker_boundary
    state["compute_events"] = 0
    with pytest.raises(ValueError, match="No profiled CUDA"):
        component_worker.infer(request, "provider.onnx-nvidia")


def test_cpu_has_no_cuda_precision_claim(worker_boundary):
    state, request = worker_boundary
    result, _ = component_worker.infer(request, "core.cpu")
    assert result["provider"] == "CPUExecutionProvider" and "cuda_precision" not in result
    assert state["sessions"][0].provider_options == [{}]
    assert state["sessions"][0].constructor_fallback_enabled is False


def test_activation_compute_uses_the_same_explicit_precision_policy(worker_boundary):
    state, _ = worker_boundary
    result = component_worker._gpu_compute_probe()
    assert result["cuda_precision"] == PRECISION and result["cuda_compute_events"] == 1
    assert state["sessions"][0].provider_options == [{"use_tf32": "0"}, {}]
    assert state["sessions"][0].constructor_fallback_enabled is False
    assert state["events"][:4] == ["construct", "disable_fallback", "options_before", "run"]


def test_activation_rejects_internal_fallback(worker_boundary):
    state, _ = worker_boundary
    state["after_providers"] = ["CPUExecutionProvider"]
    with pytest.raises(ValueError, match="full-precision"):
        component_worker._gpu_compute_probe()


@pytest.fixture
def host_boundary(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    model = tmp_path / "model"
    model.mkdir()
    for name in ("mobilenetv2.onnx", "labels.txt"):
        (model / name).write_bytes(b"temporary inference boundary fixture")
    source = tmp_path / "source.png"
    source.write_bytes(b"temporary input snapshot")
    descriptor = {"entrypoint": "helper", "version": "qualified", "sha256": "a" * 64}

    class Manager:
        def __init__(self):
            self.root = root

        def active_path(self, cid):
            return root / "provider" if cid == "provider.onnx-nvidia" else None

        @contextlib.contextmanager
        def acquire(self, cid):
            assert cid == "provider.onnx-nvidia"
            yield root / "provider", descriptor

    gpu = {"tags": [{"tag": "cat", "score": .7}], "provider": "CUDAExecutionProvider",
           "actual_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
           "cuda_compute_events": 1, "cuda_precision": copy.deepcopy(PRECISION)}
    cpu = {"tags": [{"tag": "cat", "score": .7}], "provider": "CPUExecutionProvider"}
    state = {"gpu": gpu, "cpu": cpu, "calls": [], "descriptor": descriptor}

    def run(command, request, **kwargs):
        cid = request["component_id"]
        state["calls"].append(cid)
        # Preserve the actual snapshot/lease workflow while replacing just the
        # subprocess result. The files are real, immutable temporary snapshots.
        assert Path(request["input_path"]).read_bytes() == source.read_bytes()
        assert Path(request["model_dir"], "mobilenetv2.onnx").read_bytes() == (model / "mobilenetv2.onnx").read_bytes()
        value = state["gpu"] if cid == "provider.onnx-nvidia" else state["cpu"]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value), b""

    monkeypatch.setattr(component_runtime, "_run", run)
    state.update(manager=Manager(), model=model, source=source)
    return state


def infer_host(state, **kwargs):
    return component_runtime.infer_component(str(state["source"]), str(state["model"]),
        manager=state["manager"], **kwargs)


def test_host_accepts_exact_precision_without_retry(host_boundary):
    result = infer_host(host_boundary)
    assert host_boundary["calls"] == ["provider.onnx-nvidia"]
    assert result["cuda_precision"] == PRECISION and result["fallback_reason"] is None


INVALID_PRECISION = [None, {}, [], dict(PRECISION, policy_version=True), dict(PRECISION, policy_version=2),
    dict(PRECISION, requested_use_tf32=0), dict(PRECISION, observed_use_tf32_before="1"),
    dict(PRECISION, observed_use_tf32_after=False), dict(PRECISION, internal_fallback_disabled=1),
    dict(PRECISION, internal_fallback_disabled=False), dict(PRECISION, extra="unbounded"),
    {key: value for key, value in PRECISION.items() if key != "observed_use_tf32_after"}]


@pytest.mark.parametrize("precision", INVALID_PRECISION)
def test_host_rejects_legacy_or_malformed_precision_with_one_cpu_fallback(host_boundary, precision):
    host_boundary["descriptor"]["version"] = "rolled-back-legacy"
    if precision is None:
        del host_boundary["gpu"]["cuda_precision"]
    else:
        host_boundary["gpu"]["cuda_precision"] = precision
    result = infer_host(host_boundary)
    assert host_boundary["calls"] == ["provider.onnx-nvidia", "core.cpu"]
    assert result["provider"] == "CPUExecutionProvider" and "cuda_precision" not in result
    assert "full-precision" in result["fallback_reason"]


@pytest.mark.parametrize("field,value", [("provider", "CPUExecutionProvider"),
    ("actual_providers", ["CPUExecutionProvider"]), ("actual_providers", ["CUDAExecutionProvider"] * 2),
    ("actual_providers", [True]), ("cuda_compute_events", True), ("cuda_compute_events", 0)])
def test_host_precision_object_cannot_substitute_effective_cuda(host_boundary, field, value):
    host_boundary["gpu"][field] = value
    result = infer_host(host_boundary)
    assert host_boundary["calls"] == ["provider.onnx-nvidia", "core.cpu"]
    assert result["provider"] == "CPUExecutionProvider"


def test_host_cpu_fallback_failure_is_terminal_without_more_attempts(host_boundary):
    host_boundary["gpu"] = ComponentError("GPU rejected precision")
    host_boundary["cpu"] = ComponentError("CPU unavailable")
    with pytest.raises(ComponentError, match="CPU unavailable"):
        infer_host(host_boundary)
    assert host_boundary["calls"] == ["provider.onnx-nvidia", "core.cpu"]


def test_cpu_only_selection_never_attempts_gpu(host_boundary):
    result = infer_host(host_boundary, hardware=False)
    assert host_boundary["calls"] == ["core.cpu"]
    assert "cuda_precision" not in result and result["fallback_reason"] is None
