"""Compatibility and replies cannot silently downgrade enforced leaf policy."""
from __future__ import annotations

import json
import sys

import pytest

from imagesorter.component_manager import ComponentError, ComponentManager
from imagesorter.component_runtime import _run
from imagesorter.reader_sandbox import ReaderIsolationError, validate_reader_isolation


def policy():
    return {"enforced": True, "policy_version": 1, "landlock_abi": 6,
            "network_and_mutation_ipc": "denied", "external_metadata_writes": "denied"}


@pytest.mark.parametrize("patch", [{"enforced": False}, {"policy_version": True}, {"policy_version": 2},
    {"landlock_abi": True}, {"landlock_abi": 5}, {"network_and_mutation_ipc": "allowed"},
    {"external_metadata_writes": "allowed"}])
def test_no_reply_policy_downgrade(patch):
    with pytest.raises(ReaderIsolationError, match="attest"):
        validate_reader_isolation({"reader_isolation": {**policy(), **patch}}, policy_version=1, minimum_abi=6)


def test_readonly_compatibility_probe_and_historical_pack_refusal(monkeypatch):
    descriptor = {"entrypoint": "helper", "reader_policy_version": 1, "min_landlock_abi": 6}
    monkeypatch.setattr("imagesorter.reader_sandbox.landlock_abi", lambda: 5)
    assert not ComponentManager._compatible(descriptor)
    assert "ABI 6+" in ComponentManager._compatibility_detail(descriptor)
    monkeypatch.setattr("imagesorter.reader_sandbox.landlock_abi", lambda: 6)
    assert ComponentManager._compatible(descriptor)
    assert not ComponentManager._compatible({"entrypoint": "helper"})


@pytest.mark.parametrize("reported", [None, {"enforced": False}, policy()])
def test_real_transport_checks_policy_before_accepting_success(tmp_path, reported):
    response = {"protocol_version": 1, "request_id": "contract", "component_id": "core.cpu",
                "component_version": "base", "ok": True, "payload_length": 0, "reader_isolation": reported}
    command = [sys.executable, "-c", "import sys;sys.stdin.readline();print(" + repr(json.dumps(response)) + ")"]
    request = {"request_id": "contract", "action": "infer", "component_id": "core.cpu",
               "reader_policy_version": 1, "min_landlock_abi": 3}
    if reported == policy():
        metadata, payload = _run(command, request, cwd=tmp_path, timeout=3)
        assert metadata["reader_isolation"] == reported and payload == b""
    else:
        with pytest.raises(ComponentError, match="attest"):
            _run(command, request, cwd=tmp_path, timeout=3)
