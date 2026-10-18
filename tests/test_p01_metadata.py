from __future__ import annotations

import os

import piexif
import pytest
from PIL import Image

from imagesorter.metadata_io import merge_sidecar_content, sanitize_tags, write_metadata
from imagesorter.queue_worker import _compute_provenance


def test_sanitize_tags():
    tags = ["  cat  ", "dog\n", "a" * 100, "\x00invalid", ""]
    clean = sanitize_tags(tags)
    assert clean == ["cat", "dog", "a" * 64, "invalid"]


def test_merge_sidecar_content():
    existing = "Preexisting human note, landscape"
    new_tags = ["landscape", "sunset", "nature"]
    merged = merge_sidecar_content(existing, new_tags)
    assert merged == "Preexisting human note, landscape, sunset, nature"


def test_write_metadata_sidecar_preservation(tmp_path):
    img_path = tmp_path / "photo.jpg"
    img_path.write_text("dummy image bytes")

    sidecar_path = tmp_path / "photo.jpg.txt"
    sidecar_path.write_text("User note about this photo, tag1")

    write_metadata(str(img_path), ["tag1", "tag2", "tag3"], write_exif=False, write_sidecar=True)

    assert sidecar_path.exists()
    content = sidecar_path.read_text(encoding="utf-8")
    assert content == "User note about this photo, tag1, tag2, tag3"


def test_write_metadata_sidecar_path_traversal_defense(tmp_path, monkeypatch):
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    img_path = img_dir / "photo.jpg"
    img_path.write_text("dummy image")

    escaped_sidecar = str(tmp_path / "escaped.txt")
    orig_realpath = os.path.realpath

    def mock_realpath(path):
        if str(path).endswith(".txt") and "photo.jpg" in str(path):
            return escaped_sidecar
        return orig_realpath(path)

    monkeypatch.setattr(os.path, "realpath", mock_realpath)

    with pytest.raises(ValueError, match="Path traversal detected"):
        write_metadata(str(img_path), ["tag"], write_exif=False, write_sidecar=True)


def test_exif_tagging_and_final_provenance_integrity(tmp_path):
    # Create real valid JPEG image
    img_path = tmp_path / "real_photo.jpg"
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(str(img_path), "JPEG")

    # Initial provenance
    prov_before = _compute_provenance(str(img_path))

    # Write EXIF metadata
    tags = ["architecture", "building", "city"]
    write_metadata(str(img_path), tags, write_exif=True, write_sidecar=False)

    # Verify EXIF XPKeywords tag present
    exif_dict = piexif.load(str(img_path))
    assert piexif.ImageIFD.XPKeywords in exif_dict["0th"]
    xp_val = exif_dict["0th"][piexif.ImageIFD.XPKeywords]
    xp_bytes = bytes(xp_val) if isinstance(xp_val, (tuple, list)) else xp_val
    tag_str = xp_bytes.decode("utf-16le").rstrip("\x00")
    assert tag_str == "architecture;building;city"

    # Final provenance reflects updated file state
    prov_after = _compute_provenance(str(img_path))
    assert prov_after["sha256"] != prov_before["sha256"]
    assert prov_after["size"] == os.path.getsize(str(img_path))


def test_malformed_exif_header_is_preserved_and_refused(tmp_path):
    img_path = tmp_path / "corrupt_header.jpg"
    img = Image.new("RGB", (50, 50), color="red")
    img.save(str(img_path), "JPEG")

    # Corrupt EXIF header area with garbage bytes
    with open(img_path, "r+b") as f:
        f.seek(2)
        f.write(b"\xff\xe1\x00\x10CorruptExifHeader!!")

    original_bytes = img_path.read_bytes()

    with pytest.raises(OSError, match="image was not modified|transaction failed"):
        write_metadata(
            str(img_path), ["tag1", "tag2"], write_exif=True, write_sidecar=False
        )
    assert img_path.read_bytes() == original_bytes


def test_exif_keywords_merge_without_losing_other_fields(tmp_path):
    img_path = tmp_path / "metadata.jpg"
    Image.new("RGB", (20, 20), color="blue").save(img_path, "JPEG")
    initial = {
        "0th": {
            piexif.ImageIFD.Artist: b"Photographer",
            piexif.ImageIFD.XPKeywords: ("existing\x00").encode("utf-16le"),
        },
        "Exif": {},
        "GPS": {},
        "Interop": {},
        "1st": {},
        "thumbnail": None,
    }
    piexif.insert(piexif.dump(initial), str(img_path))

    write_metadata(str(img_path), ["existing", "new"], True, False)

    result = piexif.load(str(img_path))
    assert result["0th"][piexif.ImageIFD.Artist] == b"Photographer"
    keywords = bytes(result["0th"][piexif.ImageIFD.XPKeywords])
    assert keywords.decode("utf-16le").rstrip("\x00") == "existing;new"


def test_native_adapter_does_not_bypass_standalone_write_admission(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from imagesorter import file_safety as safe
    from imagesorter import platform_capabilities
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"original")
    sidecar = tmp_path / "photo.jpg.txt"
    sidecar.write_bytes(b"original notes")
    monkeypatch.setattr(platform_capabilities.sys, "platform", "darwin")
    adapter = Mock(side_effect=AssertionError("adapter reached before admission guard"))
    monkeypatch.setattr(safe, "_native_backend", adapter)
    with pytest.raises(RuntimeError, match="macOS"):
        write_metadata(str(image), ["new tag"], write_exif=False, write_sidecar=True)
    adapter.assert_not_called()
    assert image.read_bytes() == b"original"
    assert sidecar.read_bytes() == b"original notes"
    assert sorted(path.name for path in tmp_path.iterdir() if path.is_file()) == ["photo.jpg", "photo.jpg.txt"]


def test_metadata_unknown_observation_refuses_original_replacement(tmp_path, monkeypatch):
    from imagesorter import file_safety as safe
    from imagesorter.darwin_metadata import MetadataUnavailable
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"original")
    sidecar = tmp_path / "photo.jpg.txt"
    sidecar.write_bytes(b"original notes")
    # Existing direct helper contract, injected at the observation seam; this
    # does not enable the Darwin platform or purport to qualify native writes.
    monkeypatch.setattr(safe, "probe_directory", lambda _parent: None)
    monkeypatch.setattr(safe, "fingerprint", lambda _path: (_ for _ in ()).throw(MetadataUnavailable("unknown ACL")))
    with pytest.raises(MetadataUnavailable, match="unknown ACL"):
        write_metadata(str(image), ["tag"], write_exif=False, write_sidecar=True)
    assert image.read_bytes() == b"original"
    assert sidecar.read_bytes() == b"original notes"
