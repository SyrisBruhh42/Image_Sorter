import hashlib
import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "runtime_source_collector_test", SCRIPTS / "collect_appimage_runtime_sources.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_source_and_binary_pins_are_complete():
    assert module.RUNTIME_SIZE == 944632
    assert len(module.RUNTIME_SHA) == 64
    assert len(module.SOURCES) == 8
    assert all(
        value[1].startswith("https://") and len(value[2]) == 64
        for value in module.SOURCES.values()
    )
    assert module.SOURCES["libfuse"][0] == "3.15.0"
    assert module.SOURCES["squashfuse"][0] == "0.5.2"


def test_historical_packages_not_promoted_to_verified_inputs():
    assert "not-installed-package-lock" in module.SOURCES["alpine-aports"][3]
    assert "not-binary-package-attestation" in module.SOURCES["mimalloc"][3]
    assert "mimalloc" in module.RELINK
    assert "LGPL2.1" in module.RELINK
    assert "not proof" in module.RELINK


def source(root, members):
    path = root / "source.tar.gz"
    with tarfile.open(path, "w:gz") as package:
        for name, data in members:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            package.addfile(member, io.BytesIO(data))
    return path


def test_notices_copied_byte_for_byte_without_running_source(tmp_path):
    data = b"exact upstream licence\n"
    archive = source(tmp_path, [("root/LGPL2.txt", data), ("root/source.c", b"source")])
    rows = module.selected_materials(archive, tmp_path / "out", "libfuse")
    assert len(rows) == 1
    assert Path(rows[0]["path"]).read_bytes() == data
    assert rows[0]["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "name", ["root/../../LICENSE", "/root/LICENSE", "root\\LICENSE"]
)
def test_unsafe_material_paths_refused(tmp_path, name):
    archive = source(tmp_path, [(name, b"terms")])
    with pytest.raises(ValueError, match="Unsafe"):
        module.selected_materials(archive, tmp_path / "out", "libfuse")


def test_duplicate_material_path_refused(tmp_path):
    archive = source(tmp_path, [("root/LICENSE", b"a"), ("root/LICENSE", b"b")])
    with pytest.raises(ValueError, match="Duplicate"):
        module.selected_materials(archive, tmp_path / "out", "libfuse")
