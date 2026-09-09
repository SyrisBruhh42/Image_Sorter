"""Guard the decoder recipe's licence-relevant closure; no network/build in CI."""

import copy
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_heif_decoder", Path(__file__).parents[1] / "scripts/build_heif_decoder.py"
)
assert SPEC and SPEC.loader
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


def capability():
    return {
        "libheif": "1.23.3",
        "HEIF": "",
        "AVIF": "",
        "decoders": {"libde265": "libde265 HEVC decoder, version 1.1.2"},
        "encoders": {"mask": "mask"},
    }


def test_decoder_recipe_pins_sources_and_wheels():
    for _, commit, _, digest in recipe.SOURCES.values():
        assert len(commit) == 40 and len(digest) == 64
        int(commit, 16)
        int(digest, 16)
    for requirement, digest in recipe.WHEELS.items():
        assert "==" in requirement and len(digest) == 64
        int(digest, 16)
    assert {"X265", "X264", "AOM_ENCODER", "SvtEnc", "RAV1E"}.issubset(
        recipe.DISABLED_HEIF
    )


def test_decoder_capability_accepts_only_pinned_decoder_and_builtin_mask():
    recipe.verify_capabilities(capability())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("HEIF", "x265 HEVC encoder"),
        ("AVIF", "AOM AV1 encoder"),
        ("encoders", {"mask": "mask", "x265": "x265"}),
        ("decoders", {}),
        ("libheif", "unknown"),
    ],
)
def test_decoder_capability_rejects_drift(field, value):
    info = copy.deepcopy(capability())
    info[field] = value
    with pytest.raises(ValueError, match="capability"):
        recipe.verify_capabilities(info)


def test_source_unpack_rejects_links(tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("root/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
        tar.addfile(member)
    with pytest.raises(ValueError, match="link or special"):
        recipe.unpack(archive, tmp_path / "extract")


def test_source_unpack_rejects_traversal(tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("../../outside")
        member.size = 1
        tar.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(tarfile.OutsideDestinationError):
        recipe.unpack(archive, tmp_path / "extract")
    assert not (tmp_path.parent / "outside").exists()
