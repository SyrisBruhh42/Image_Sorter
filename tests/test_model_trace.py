"""Exact startup-file provenance never becomes a blanket .pth exemption."""
import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("model_trace_tests", Path(__file__).parents[1] / "scripts/model_trace.py")
classifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(classifier)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    return {"runtime_root": str(root), "runtime_files": [{"relative_path": path.relative_to(root).as_posix(), "sha256": digest(path)}
            for path in root.rglob("*") if path.is_file()]}


def distribution(site, pth, name="setuptools", version="84.0.0"):
    metadata = site / f"{name}-{version}.dist-info"
    metadata.mkdir()
    data = pth.read_bytes()
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    (metadata / "RECORD").write_text(f"{pth.name},sha256={encoded},{len(data)}\n")
    (metadata / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    return metadata


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "installed"
    site = root / "lib/python3.12/site-packages"
    site.mkdir(parents=True)
    pth = site / "distutils-precedence.pth"
    pth.write_text("import os; enabled = os.environ.get('SETUPTOOLS_USE_DISTUTILS', 'local') == 'local'\n")
    metadata = distribution(site, pth)
    return root, site, pth, metadata


def opened(path):
    return f'1788921030.000001 openat(AT_FDCWD</tmp>, "{path}", O_RDONLY|O_CLOEXEC) = 3<{path}>'


def test_exact_installed_record_identity_is_required(installed):
    root, _site, pth, metadata = installed
    allowed = classifier.attested_startup_files(snapshot(root))
    assert set(allowed) == {str(pth)}
    assert allowed[str(pth)]["record"]["sha256"] == digest(metadata / "RECORD")
    assert allowed[str(pth)]["metadata"]["sha256"] == digest(metadata / "METADATA")
    assert classifier.is_model_open(opened(pth)) is True
    assert classifier.is_model_open(opened(pth), allowed) is False


def test_same_basename_and_bytes_outside_installed_root_remain_model_attempt(installed, tmp_path):
    root, _site, pth, _metadata = installed
    allowed = classifier.attested_startup_files(snapshot(root))
    outside = tmp_path / pth.name
    outside.write_bytes(pth.read_bytes())
    assert classifier.is_model_open(opened(outside), allowed) is True


def test_changed_pth_is_detected_even_with_old_allowed_mapping(installed):
    root, _site, pth, _metadata = installed
    build = snapshot(root)
    allowed = classifier.attested_startup_files(build)
    pth.write_bytes(b"PK\x03\x04model checkpoint bytes")
    assert classifier.attested_startup_files(build) == {}
    assert classifier.is_model_open(opened(pth), allowed) is True


@pytest.mark.parametrize("filename", ["RECORD", "METADATA"])
def test_changed_ownership_evidence_revokes_exemption(installed, filename):
    root, _site, pth, metadata = installed
    build = snapshot(root)
    allowed = classifier.attested_startup_files(build)
    (metadata / filename).write_text("changed evidence\n")
    assert classifier.attested_startup_files(build) == {}
    assert classifier.is_model_open(opened(pth), allowed) is True


@pytest.mark.parametrize("content", [b"PK\x03\x04model bytes", b"\x80\x04pickle weights", b"tensor\0weights", b"import ???\n"])
def test_binary_or_invalid_pth_is_not_a_startup_file_even_if_recorded(installed, content):
    root, site, pth, metadata = installed
    pth.write_bytes(content)
    (metadata / "RECORD").unlink()
    encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
    (metadata / "RECORD").write_text(f"{pth.name},sha256={encoded},{len(content)}\n")
    assert classifier.attested_startup_files(snapshot(root)) == {}
    assert classifier.is_model_open(opened(pth), {}) is True


def test_ambiguous_distribution_ownership_is_not_exempt(installed):
    root, site, pth, _metadata = installed
    second = distribution(site, pth, "other-package", "1")
    assert classifier.attested_startup_files(snapshot(root)) == {}
    (second / "RECORD").write_text(f"{pth.name},sha256=wrong,1\n")
    assert classifier.attested_startup_files(snapshot(root)) == {}


def test_uninventoried_ownership_cannot_grant_itself_trust(installed):
    root, _site, _pth, _metadata = installed
    build = snapshot(root)
    build["runtime_files"] = [item for item in build["runtime_files"] if not item["relative_path"].endswith("/RECORD")]
    assert classifier.attested_startup_files(build) == {}


def test_symlinked_startup_file_is_not_exempt(installed, tmp_path):
    root, _site, pth, _metadata = installed
    outside = tmp_path / "original-startup.pth"
    pth.replace(outside)
    pth.symlink_to(outside)
    assert classifier.attested_startup_files(snapshot(root)) == {}


@pytest.mark.parametrize("filename", ["weights.onnx", "weights.pt", "weights.pth", "weights.safetensors", "a1_coverage.pth"])
def test_unknown_model_extensions_remain_detected(installed, filename):
    root, site, _pth, _metadata = installed
    assert classifier.is_model_open(opened(site / filename), classifier.attested_startup_files(snapshot(root))) is True


def test_observer_and_independent_verifier_share_exact_classification(installed, tmp_path):
    root, _site, pth, _metadata = installed
    scripts = Path(__file__).parents[1] / "scripts"
    def load(name):
        spec = importlib.util.spec_from_file_location(name, scripts / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    observer, verifier = load("native_acceptance"), load("qualification_schema")
    trace = tmp_path / "process-trace.123"
    trace.write_text(opened(pth) + '\n1788921031 +++ exited with 0 +++\n')
    allowed = classifier.attested_startup_files(snapshot(root))
    actual, _ = verifier.trace_facts([trace], allowed)
    assert observer.summarize_trace([trace], allowed) == actual
    assert actual["model_open_attempts"] == []
    assert len(observer.summarize_trace([trace])["model_open_attempts"]) == 1
