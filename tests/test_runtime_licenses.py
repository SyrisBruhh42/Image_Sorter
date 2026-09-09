"""Licence evidence controls without downloads or binary builds."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "runtime_licenses",
    Path(__file__).parents[1] / "scripts/collect_runtime_licenses.py",
)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("MIT", False),
        ("BSD-2-Clause", False),
        ("GPL-3.0-only", True),
        ("LGPL v3", True),
        ("LGPL-2.1-or-later", True),
        ("AGPL-3.0-only", True),
    ],
)
def test_source_duties_follow_applicable_license(expression, expected):
    assert (
        collector.requires_corresponding_source({"license_expression": expression})
        is expected
    )


def test_source_duty_override_cannot_silently_disable_gpl():
    assert collector.requires_corresponding_source(
        {"license_expression": "GPL-3.0-only", "corresponding_source_required": False}
    )
    assert collector.requires_corresponding_source(
        {"license_expression": "MIT", "corresponding_source_required": True}
    )
    with pytest.raises(ValueError, match="boolean"):
        collector.requires_corresponding_source(
            {"corresponding_source_required": "false"}
        )


def component(expression="MIT"):
    return {
        "id": "example",
        "version": "1.0",
        "license_expression": expression,
        "relationship": "separate runtime library",
        "archives": [],
    }


def test_permissive_source_absence_is_not_extra_obligation(tmp_path):
    records, unresolved = collector.source_materials(
        {"components": [component()]}, tmp_path
    )
    assert not unresolved
    assert records[0]["corresponding_source_required"] is False


def test_gpl_source_missing_and_version_match_alone_cannot_pass(tmp_path):
    entry = component("GPL-3.0-only")
    entry["correspondence"] = "upstream-version-matched"
    _, unresolved = collector.source_materials({"components": [entry]}, tmp_path)
    assert len(unresolved) == 2


def test_verified_source_bytes_preserved_and_checked(tmp_path):
    source = tmp_path / "source-materials.txt"
    source.write_text("preserved test source and rebuild instructions")
    entry = component("LGPL-3.0-only")
    entry.update(
        correspondence="verified-build-inputs",
        review={
            "reviewer": "unit-test fixture",
            "reviewed_at": "2026-09-08T00:00:00Z",
            "basis": "synthetic fixture, not release evidence",
        },
    )
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    entry["archives"] = [{"filename": source.name, "path": str(source), "sha256": sha}]
    records, unresolved = collector.source_materials(
        {"components": [entry]}, tmp_path / "out"
    )
    assert not unresolved
    copied = tmp_path / "out" / records[0]["archives"][0]["path"]
    assert copied.read_bytes() == source.read_bytes()
    entry["archives"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        collector.source_materials({"components": [entry]}, tmp_path / "other")


def test_source_manifest_rejects_duplicate_and_traversal_ids(tmp_path):
    with pytest.raises(ValueError, match="Duplicate or unsafe"):
        collector.source_materials({"components": [component(), component()]}, tmp_path)
    entry = component()
    entry["id"] = "../../escape"
    with pytest.raises(ValueError, match="Duplicate or unsafe"):
        collector.source_materials({"components": [entry]}, tmp_path)


def test_runtime_reference_rejects_escape(tmp_path):
    with pytest.raises(ValueError, match="Unsafe inventory"):
        collector.relative_file(tmp_path, "../secret")


def test_captured_native_origin_requires_identical_bytes(tmp_path):
    source = tmp_path / "library.so"
    source.write_bytes(b"original")
    toc = tmp_path / "Analysis.toc"
    toc.write_text(repr([("library.so", str(source), "BINARY")]))
    rows = [
        {
            "path": "_internal/library.so",
            "sha256": "0" * 64,
            "matching_installed_files": [],
        }
    ]
    assert collector.preserve_host_origins(rows, toc, tmp_path / "out") == []
    assert "captured_build_input" not in rows[0]


def test_build_toc_is_data_not_executable_code(tmp_path):
    toc = tmp_path / "Analysis.toc"
    toc.write_text("__import__('os').getcwd()")
    with pytest.raises(ValueError):
        collector.preserve_host_origins([], toc, tmp_path / "out")
