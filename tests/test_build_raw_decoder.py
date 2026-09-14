import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "raw_builder_under_test", SCRIPTS / "build_raw_decoder.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def info():
    return {
        "rawpy": "0.27.1",
        "libraw": [0, 22, 2],
        "libraw_compiled": [0, 22, 2],
        "lcms2_encoded_version": 2190,
        "flags": dict(builder.FEATURES),
    }


def test_fixed_version_and_existing_features():
    builder.verify_capabilities(info())
    assert builder.SOURCES["libraw"][0] == "0.22.2"
    assert all(
        len(value[2]) == 64 and value[1].startswith("https://")
        for value in builder.SOURCES.values()
    )
    assert all(len(value) == 64 for value in builder.WHEELS.values())


@pytest.mark.parametrize("feature", list(builder.FEATURES))
def test_any_feature_drift_fails(feature):
    value = info()
    value["flags"][feature] = not value["flags"][feature]
    with pytest.raises(ValueError):
        builder.verify_capabilities(value)


@pytest.mark.parametrize("field", ["libraw", "libraw_compiled"])
def test_old_or_mismatched_library_fails(field):
    value = info()
    value[field] = [0, 22, 1]
    with pytest.raises(ValueError):
        builder.verify_capabilities(value)


def test_old_lcms_library_fails():
    value = info()
    value["lcms2_encoded_version"] = 2110
    with pytest.raises(ValueError):
        builder.verify_capabilities(value)


def archive(tmp_path, members):
    target = tmp_path / "source.tar.gz"
    with tarfile.open(target, "w:gz") as package:
        for name, kind, link in members:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.linkname = link
            item.size = 0
            package.addfile(item, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
    return target


def test_internal_source_symlink_allowed(tmp_path):
    source = archive(
        tmp_path,
        [
            ("source", tarfile.DIRTYPE, ""),
            ("source/bin/tool", tarfile.REGTYPE, ""),
            ("source/build/tool", tarfile.SYMTYPE, "../bin/tool"),
        ],
    )
    tree = builder.unpack(source, tmp_path / "extract")
    assert (tree / "build/tool").is_symlink()
    assert (tree / "build/tool").resolve().is_relative_to(tree)


@pytest.mark.parametrize(
    "name,kind,link",
    [
        ("../escape", tarfile.REGTYPE, ""),
        ("source/link", tarfile.SYMTYPE, "../../escape"),
        ("source/fifo", tarfile.FIFOTYPE, ""),
        ("source/hard", tarfile.LNKTYPE, "source/file"),
    ],
)
def test_unsafe_source_members_refused(tmp_path, name, kind, link):
    source = archive(tmp_path, [(name, kind, link)])
    with pytest.raises(ValueError):
        builder.unpack(source, tmp_path / "extract")


def test_recipe_does_not_claim_application_qualification():
    assert (
        b'"application_format_qualification": "not-performed"' in builder.RECIPE_BYTES
    )
    assert "OpenMP uses GCC libgomp" in builder.RELINK_TEXT
    assert "RAWPY_USE_SYSTEM_LIBRAW=1" in builder.RELINK_TEXT
