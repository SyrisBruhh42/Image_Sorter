from __future__ import annotations

import os

import piexif
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

    import pytest
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


def test_malformed_exif_header_fallback(tmp_path):
    img_path = tmp_path / "corrupt_header.jpg"
    img = Image.new("RGB", (50, 50), color="red")
    img.save(str(img_path), "JPEG")

    # Corrupt EXIF header area with garbage bytes
    with open(img_path, "r+b") as f:
        f.seek(2)
        f.write(b"\xff\xe1\x00\x10CorruptExifHeader!!")

    # Should handle malformed header gracefully without crash
    write_metadata(str(img_path), ["tag1", "tag2"], write_exif=True, write_sidecar=False)
    assert img_path.exists()
