"""Safety envelope for shipped, enumerated diagnostics (not native qualification)."""
import json
import os
import subprocess
import sys

import pytest

from imagesorter.bootstrap import configure_profile
from imagesorter.native_scenarios import prepare, reader_observation
from imagesorter.worker_protocol import worker_environment


def test_diagnostics_refuse_existing_unmarked_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGESORTER_PROFILE_ROOT", str(tmp_path / "profile"))
    configure_profile(str(tmp_path / "profile"))
    settings = tmp_path / "profile" / "config" / "ImageSorter" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"real": "settings"}')
    before = settings.read_bytes()
    with pytest.raises(ValueError, match="unmarked"):
        prepare("core")
    assert settings.read_bytes() == before


def test_diagnostics_reject_unknown_scenario_before_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGESORTER_PROFILE_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="Unknown"):
        prepare("exec arbitrary python")
    assert not list(tmp_path.glob("native-diagnostics"))


def test_reader_envelope_retains_full_reply_and_exact_fixture_bytes(tmp_path):
    import hashlib
    from pathlib import Path
    fixture = tmp_path / "fixture.jpg"
    fixture.write_bytes(b"unchanged immutable fixture")
    reply = {"input_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
             "component_id": "core.cpu", "component_version": "base",
             "reader_isolation": {"enforced": True, "policy_version": 1},
             "extra_native_profiling": {"nodes": ["actual-node"]}}
    reference = reader_observation(tmp_path, "cpu-inference", fixture, reply, context={"parent_operation_id": "parent"})
    envelope = json.loads(Path(reference["path"]).read_text())
    assert envelope["fixture"]["sha256"] == reply["input_sha256"]
    assert json.loads(Path(envelope["reply"]["path"]).read_text()) == reply
    assert hashlib.sha256(Path(envelope["reply"]["path"]).read_bytes()).hexdigest() == envelope["reply"]["sha256"]
    assert envelope["context"]["parent_operation_id"] == "parent"
    with pytest.raises(ValueError, match="input digest"):
        reader_observation(tmp_path, "mismatch", fixture, {"input_sha256": "f" * 64})
    with pytest.raises(FileExistsError):
        reader_observation(tmp_path, "cpu-inference", fixture, reply)


def test_premature_diagnostic_close_is_not_a_pass(tmp_path):
    profile = tmp_path / "profile"
    environment = worker_environment()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    process = subprocess.run([sys.executable, "-m", "imagesorter.bootstrap", "--profile-root", str(profile),
                              "--diagnostic-scenario", "core", "--acceptance-exit-after-ready"],
                             env=environment, capture_output=True, text=True, timeout=20)
    assert process.returncode == 2, process.stderr
    report = json.loads(next((profile / "native-diagnostics").glob("*/observations.json")).read_text())
    assert report["complete"] is False
    assert any(case["status"] == "not_run" for case in report["cases"])


@pytest.mark.parametrize("scenario, expected", [("core", ["navigation", "file-set", "settings"]), ("hotkeys", ["hotkeys"]), ("recovery", ["recovery"])])
def test_core_driver_source_offscreen_is_not_native_qualification(tmp_path, scenario, expected):
    profile = tmp_path / "profile"
    environment = worker_environment()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment.pop("IMAGESORTER_PROFILE_ROOT", None)
    process = subprocess.run([sys.executable, "-m", "imagesorter.bootstrap", "--profile-root", str(profile),
                              "--diagnostic-receipt", str(profile / "state" / "diagnostic.json"),
                              "--diagnostic-scenario", scenario], env=environment, capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    assert "Exception ignored in atexit" not in process.stderr
    reports = list((profile / "native-diagnostics").glob("*/observations.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["native"] is False and report["complete"] is False
    assert [case["id"] for case in report["cases"]] == expected
    assert all(case["status"] == "passed" for case in report["cases"])
    assert report["operator_review_required"]
    assert os.stat(profile).st_mode & 0o077 == 0
