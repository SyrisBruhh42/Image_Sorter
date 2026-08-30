import os
import shutil
import tempfile
import numpy as np
import pytest
from PIL import Image
import piexif

from src.ai_tagger import (
    calculate_sha256,
    ModelDownloader,
    AITagger,
    write_metadata,
    _sanitize_tags,
    MODEL_SHA256
)

def test_calculate_sha256(tmp_path):
    test_file = tmp_path / "test_sha.bin"
    data = b"Hello, Zero-Trust AI Vision!"
    test_file.write_bytes(data)

    checksum = calculate_sha256(str(test_file))
    assert len(checksum) == 64
    assert checksum != ""


def test_downloader_checksum_mismatch(tmp_path, monkeypatch):
    downloader = ModelDownloader(model_dir=str(tmp_path))

    # Mock secure download to produce dummy file with mismatch checksum
    def dummy_download(url, dest_temp_path, progress_callback=None, timeout=15.0):
        with open(dest_temp_path, "wb") as f:
            f.write(b"tampered content")

    monkeypatch.setattr("src.ai_tagger._download_file_secure", dummy_download)

    finished_signals = []
    downloader.finished.connect(lambda success, msg: finished_signals.append((success, msg)))
    downloader.run()

    assert len(finished_signals) == 1
    success, msg = finished_signals[0]
    assert success is False
    assert "Cryptographic Integrity Failure" in msg
    # Ensure model path was NOT created
    assert not os.path.exists(downloader.model_path)


def test_ai_tagger_load_model_checksum_mismatch(tmp_path):
    model_path = tmp_path / "mobilenetv2.onnx"
    labels_path = tmp_path / "labels.txt"
    model_path.write_bytes(b"invalid model content")
    labels_path.write_text("cat\ndog\n")

    tagger = AITagger(model_dir=str(tmp_path))
    assert tagger.session is None


def test_preprocess_decompression_bomb(tmp_path, monkeypatch):
    tagger = AITagger(model_dir=str(tmp_path))
    bomb_path = tmp_path / "bomb.png"
    bomb_path.write_bytes(b"dummy")

    def mock_open(*args, **kwargs):
        raise Image.DecompressionBombError("Decompression bomb detected")

    monkeypatch.setattr("PIL.Image.open", mock_open)
    tensor = tagger.preprocess(str(bomb_path))
    assert tensor is None


def test_get_tags_nan_logits(tmp_path, monkeypatch):
    tagger = AITagger(model_dir=str(tmp_path))
    tagger.labels = ["cat", "dog", "car"]

    class DummySession:
        def get_inputs(self):
            class DummyInput:
                name = "input"
            return [DummyInput()]
        def run(self, output_names, input_feed):
            return [np.array([[np.nan, np.inf, 1.0]], dtype=np.float32)]

    tagger.session = DummySession()
    monkeypatch.setattr(tagger, "preprocess", lambda path: np.ones((1, 3, 224, 224), dtype=np.float32))

    tags = tagger.get_tags("dummy.jpg", top_k=2)
    assert isinstance(tags, list)


def test_sanitize_tags():
    raw_tags = ["  good_tag \n ", "bad\x00tag\r", "a" * 100] + [f"tag_{i}" for i in range(40)]
    sanitized = _sanitize_tags(raw_tags)
    assert len(sanitized) <= 30
    assert "good_tag" in sanitized
    assert all(len(t) <= 64 for t in sanitized)


def test_write_metadata_exif_and_copystat(tmp_path):
    img_path = tmp_path / "test.jpg"
    img = Image.new('RGB', (100, 100), color='blue')
    img.save(img_path)

    orig_mtime = os.path.getmtime(img_path)
    tags = ["cat", "landscape"]

    write_metadata(str(img_path), tags, write_exif=True, write_sidecar=True)

    # Verify sidecar
    sidecar_path = str(img_path) + ".txt"
    assert os.path.exists(sidecar_path)
    with open(sidecar_path, 'r', encoding='utf-8') as f:
        content = f.read()
    assert "cat, landscape" in content

    # Verify EXIF double-null UTF-16LE encoding
    exif_dict = piexif.load(str(img_path))
    raw_xp = exif_dict["0th"][piexif.ImageIFD.XPKeywords]
    raw_bytes = bytes(raw_xp) if isinstance(raw_xp, (tuple, list)) else raw_xp
    expected_xp = ("cat;landscape" + "\x00").encode("utf-16le")
    assert raw_bytes == expected_xp

    # Verify copystat preserved modification timestamp within sub-second precision
    new_mtime = os.path.getmtime(img_path)
    assert abs(new_mtime - orig_mtime) < 2.0


def test_write_metadata_sidecar_path_traversal(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    img_path = parent / "test.jpg"
    img = Image.new('RGB', (10, 10))
    img.save(img_path)

    # Passing a relative filepath trying to escape
    escaping_path = str(parent / ".." / "parent" / "test.jpg")
    write_metadata(escaping_path, ["safe_tag"], write_exif=False, write_sidecar=True)
    assert os.path.exists(str(img_path) + ".txt")
