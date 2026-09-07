"""Tests for P05 image decoding engine (src/imagesorter/image_decoding.py)."""

import time

import piexif
from PIL import Image, ImageCms

from imagesorter.image_decoding import decode_image, inspect_image_header


def test_exif_orientation_auto_transform(tmp_path):
    """Verifies that EXIF orientation 6 (90-deg CW rotation) auto-transforms a 40x20 image to 20x40."""
    img_path = tmp_path / "orientation_6.jpg"
    img = Image.new("RGB", (40, 20), color="red")
    exif_dict = {"0th": {piexif.ImageIFD.Orientation: 6}}
    exif_bytes = piexif.dump(exif_dict)
    img.save(str(img_path), format="JPEG", exif=exif_bytes)

    qimg, error = decode_image(str(img_path))
    assert error is None
    assert qimg is not None
    # 40x20 with orientation=6 rotates 90 degrees CW to 20x40
    assert qimg.width() == 20
    assert qimg.height() == 40


def test_corrupt_and_empty_images(tmp_path):
    """Verifies diagnostic error messages for non-existent, empty, and corrupt files."""
    # Non-existent file
    qimg, err = decode_image(str(tmp_path / "nonexistent.jpg"))
    assert qimg is None
    assert "File does not exist" in err

    # Zero-byte empty file
    empty_file = tmp_path / "empty.jpg"
    empty_file.write_bytes(b"")
    qimg, err = decode_image(str(empty_file))
    assert qimg is None
    assert "empty" in err.lower()

    # Corrupt header file
    corrupt_file = tmp_path / "corrupt.jpg"
    corrupt_file.write_bytes(b"NOT A JPEG HEADER DATA STREAM")
    qimg, err = decode_image(str(corrupt_file))
    assert qimg is None
    assert "cannot read" in err.lower() or "unsupported" in err.lower()


def test_oversized_allocation_limit(tmp_path):
    """Verifies pre-decoding memory allocation checks reject oversized headers."""
    img_path = tmp_path / "sample.png"
    img = Image.new("RGB", (1000, 1000), color="blue")
    img.save(str(img_path))

    # Set allocation limit below 1000x1000x4 (4,000,000 bytes)
    qimg, err = decode_image(str(img_path), max_allocation_bytes=1_000_000)
    assert qimg is None
    assert "exceed maximum allocation threshold" in err


def test_target_size_aspect_ratio_preservation(tmp_path):
    """Verifies non-square preview downscaling while preserving aspect ratio."""
    img_path = tmp_path / "landscape.jpg"
    img = Image.new("RGB", (1920, 1080), color="green")
    img.save(str(img_path))

    # Target bounding box 400x400
    qimg, err = decode_image(str(img_path), target_size=(400, 400))
    assert err is None
    assert qimg is not None
    # 1920x1080 scaled inside 400x400 box -> 400x225
    assert qimg.width() == 400
    assert qimg.height() == 225


def test_unicode_path_handling(tmp_path):
    """Verifies unicode directory and file path support."""
    unicode_dir = tmp_path / "测试目录_café_📸"
    unicode_dir.mkdir()
    img_path = unicode_dir / "图像_bild_123.jpg"

    img = Image.new("RGB", (100, 100), color="yellow")
    img.save(str(img_path))

    qimg, err = decode_image(str(img_path))
    assert err is None
    assert qimg is not None
    assert qimg.width() == 100
    assert qimg.height() == 100


def test_color_profile_conversion(tmp_path):
    """Verifies embedded color profiles are processed safely and converted to sRGB."""
    img_path = tmp_path / "icc_profile.jpg"
    img = Image.new("RGB", (50, 50), color="purple")
    prof = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(prof).tobytes()
    img.save(str(img_path), format="JPEG", icc_profile=icc_bytes)

    qimg, err = decode_image(str(img_path))
    assert err is None
    assert qimg is not None
    assert qimg.colorSpace().isValid()


def test_explicit_unsupported_formats(tmp_path):
    """Verifies clean error reporting for unsupported RAW and container extensions."""
    raw_path = tmp_path / "sample.cr2"
    raw_path.write_bytes(b"CR2 DUMMY")

    qimg, err = decode_image(str(raw_path))
    assert qimg is None
    assert "CR2" in err and "optional" in err

    info = inspect_image_header(str(raw_path))
    assert info["valid"] is False
    assert "CR2" in info["error"]


def test_header_inspection(tmp_path):
    """Verifies inspect_image_header returns valid metadata without full decode."""
    img_path = tmp_path / "test_hdr.jpg"
    img = Image.new("RGB", (320, 240), color="white")
    img.save(str(img_path))

    info = inspect_image_header(str(img_path))
    assert info["valid"] is True
    assert info["width"] == 320
    assert info["height"] == 240
    assert info["format"] in ("jpeg", "jpg")


def test_decoding_latency_measurement(tmp_path):
    """Measures representative decoding latency for a 2K image."""
    img_path = tmp_path / "bench_2k.jpg"
    img = Image.new("RGB", (2560, 1440), color="cyan")
    img.save(str(img_path), quality=90)

    start_time = time.perf_counter()
    qimg, err = decode_image(str(img_path), target_size=(800, 600))
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    assert err is None
    assert qimg is not None
    assert elapsed_ms > 0
    # Confirm result size is downscaled
    assert qimg.width() == 800
    assert qimg.height() == 450
