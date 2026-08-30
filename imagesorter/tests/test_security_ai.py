import os
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import piexif

from src.ai_tagger import ModelDownloader, write_metadata, AITagger, MODEL_SHA256


def test_model_downloader_sha256_mismatch(qtbot, tmp_path):
    downloader = ModelDownloader(model_dir=str(tmp_path))

    results = []
    downloader.finished.connect(lambda success, msg: results.append((success, msg)))

    # Mock urlretrieve to write corrupted data resulting in SHA-256 mismatch
    def mock_urlretrieve(url, filename, reporthook=None):
        with open(filename, "wb") as f:
            f.write(b"invalid corrupt model content")

    with patch("urllib.request.urlretrieve", side_effect=mock_urlretrieve):
        downloader.run()

    assert len(results) == 1
    success, msg = results[0]
    assert success is False
    assert "Cryptographic integrity failure: SHA-256 mismatch." in msg
    # Verify temporary file was unlinked and model file was never created
    assert not (tmp_path / "mobilenetv2.onnx").exists()
    assert len(list(tmp_path.glob("model_*.tmp"))) == 0


def test_exif_xpkeywords_null_termination(tmp_path):
    # Create valid JPEG image header
    img_file = tmp_path / "sample.jpg"
    img = Image.new("RGB", (50, 50), color="blue")
    img.save(str(img_file), format="JPEG")

    tags = ["enterprise", "triage", "ai"]
    write_metadata(str(img_file), tags, write_exif=True, write_sidecar=False)

    exif_dict = piexif.load(str(img_file))
    assert 40094 in exif_dict["0th"]
    raw_val = exif_dict["0th"][40094]
    xp_keywords_bytes = bytes(raw_val) if isinstance(raw_val, (tuple, list)) else raw_val

    # Verify XPKeywords byte string ends with UTF-16LE double null bytes (\x00\x00)
    assert xp_keywords_bytes.endswith(b"\x00\x00")
    decoded_tags = xp_keywords_bytes.decode("utf-16le").rstrip("\x00")
    assert decoded_tags == "enterprise;triage;ai"


def test_decompression_bomb_rejection(tmp_path):
    # Set maximum allowed pixels low to trigger decompression bomb protection
    with patch.object(Image, "MAX_IMAGE_PIXELS", 100):
        # Create image exceeding MAX_IMAGE_PIXELS threshold
        bomb_file = tmp_path / "bomb.png"
        img = Image.new("RGB", (20, 20), color="red")
        img.save(str(bomb_file))

        # Opening image directly should raise DecompressionBombError
        with pytest.raises(Image.DecompressionBombError):
            with Image.open(str(bomb_file)) as bomb_img:
                bomb_img.verify()

        # AITagger.preprocess should safely catch DecompressionBombError and return None without crash
        tagger = AITagger(model_dir=str(tmp_path))
        with patch("PIL.Image.open", side_effect=Image.DecompressionBombError("Decompression bomb detected")):
            tensor = tagger.preprocess(str(bomb_file))
            assert tensor is None
