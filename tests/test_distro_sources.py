"""Selection is exact and scoped to shipped-package evidence, not host inventory."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "distro_sources", Path(__file__).parents[1] / "scripts/acquire_distro_sources.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def inventory(root, text="LGPL-2.1-or-later"):
    path = root / "copyright"
    path.write_text(text)
    return {
        "shipped_os_packages": [
            {
                "source_package": "example",
                "source_version": "1:2.0-3ubuntu1",
                "notice": {
                    "path": "copyright",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
            }
        ]
    }


def test_only_preserved_shipped_copyleft_source_selected(tmp_path):
    data = inventory(tmp_path)
    assert module.selected_sources(data, tmp_path, None) == {
        "example": "1:2.0-3ubuntu1"
    }
    data = inventory(tmp_path, "MIT permissive notice")
    assert not module.selected_sources(data, tmp_path, None)


def test_explicit_selection_cannot_add_unshipped_package(tmp_path):
    data = inventory(tmp_path, "MIT permissive notice")
    assert module.selected_sources(data, tmp_path, ["example"]) == {
        "example": "1:2.0-3ubuntu1"
    }
    with pytest.raises(ValueError, match="not represented"):
        module.selected_sources(data, tmp_path, ["unrelated"])


def test_changed_notice_and_unsafe_package_rejected(tmp_path):
    data = inventory(tmp_path)
    (tmp_path / "copyright").write_text("changed")
    with pytest.raises(ValueError, match="hash/path"):
        module.selected_sources(data, tmp_path, None)
    data["shipped_os_packages"][0]["source_package"] = "../../escape"
    with pytest.raises(ValueError, match="Invalid exact"):
        module.selected_sources(data, tmp_path, ["../../escape"])


def test_conflicting_source_versions_not_silently_overwritten(tmp_path):
    data = inventory(tmp_path)
    other = dict(data["shipped_os_packages"][0], source_version="2.1")
    data["shipped_os_packages"].append(other)
    with pytest.raises(ValueError, match="Multiple shipped"):
        module.selected_sources(data, tmp_path, None)


def source_set(root):
    archive = root / "example_2.0.orig.tar.gz"
    archive.write_bytes(b"preserved exact source")
    line = f" {module.sha(archive)} {archive.stat().st_size} {archive.name}\n"
    descriptor = root / "example_2.0.dsc"
    descriptor.write_text(
        "Source: example\nVersion: 1:2.0-3ubuntu1\nChecksums-Sha256:\n" + line
    )
    return archive, descriptor, line


def test_source_descriptor_identity_hash_and_size_are_checked(tmp_path):
    archive, descriptor, line = source_set(tmp_path)
    assert module.verify_source_set(tmp_path, "example", "1:2.0-3ubuntu1") == 1
    with pytest.raises(ValueError, match="identity"):
        module.verify_source_set(tmp_path, "example", "2.0")
    archive.write_bytes(b"different")
    with pytest.raises(ValueError, match="checksum/size"):
        module.verify_source_set(tmp_path, "example", "1:2.0-3ubuntu1")


def test_duplicate_source_descriptor_member_is_refused(tmp_path):
    archive, descriptor, line = source_set(tmp_path)
    descriptor.write_text(descriptor.read_text() + line)
    with pytest.raises(ValueError, match="Unsafe"):
        module.verify_source_set(tmp_path, "example", "1:2.0-3ubuntu1")


def test_source_descriptor_cannot_follow_external_symlink(tmp_path):
    archive, descriptor, line = source_set(tmp_path)
    moved = tmp_path / "original-source"
    archive.rename(moved)
    archive.symlink_to(moved)
    with pytest.raises(ValueError, match="checksum/size"):
        module.verify_source_set(tmp_path, "example", "1:2.0-3ubuntu1")
