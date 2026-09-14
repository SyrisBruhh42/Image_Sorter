import hashlib
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from imagesorter.ai_tagger import AITagger, ModelDownloader, is_model_and_labels_valid
from imagesorter.hardware_scan import get_prioritized_providers

MOCK_MODEL_DATA = b"MOCK_ONNX_MODEL_12345"
MOCK_MODEL_HASH = hashlib.sha256(MOCK_MODEL_DATA).hexdigest()
MOCK_LABELS_DATA = b"tench\ngoldfish\ngreat white shark\n"
MOCK_LABELS_HASH = hashlib.sha256(MOCK_LABELS_DATA).hexdigest()


@pytest.fixture(autouse=True)
def patch_hashes(monkeypatch):
    monkeypatch.setattr("imagesorter.ai_tagger.MODEL_SHA256", MOCK_MODEL_HASH)
    monkeypatch.setattr("imagesorter.ai_tagger.LABELS_SHA256", MOCK_LABELS_HASH)


def test_ai_tagger_contract_attributes():
    assert getattr(AITagger, "API_VERSION", None) == 1


def test_preprocessing_geometry_and_normalization(tmp_path):
    # Create non-square 400x200 image
    img_path = tmp_path / "test_geo.png"
    img = Image.new("RGB", (400, 200), color=(255, 255, 255))
    img.save(str(img_path))

    tagger = AITagger(model_dir=str(tmp_path))
    tensor = tagger.preprocess(str(img_path))

    assert tensor is not None
    assert isinstance(tensor, np.ndarray)
    assert tensor.dtype == np.float32
    assert tensor.shape == (1, 3, 224, 224)
    # White image [255,255,255] / 255 -> 1.0; (1.0 - 0.5) / 0.5 = 1.0
    np.testing.assert_allclose(tensor, 1.0, atol=1e-4)


def test_preprocessing_exif_orientation(tmp_path):
    img_path = tmp_path / "exif_orient.jpg"
    img = Image.new("RGB", (100, 200), color="blue")

    # Exif orientation 6 means 90 degree CW rotation
    exif_dict = {"0th": {274: 6}}
    import piexif
    exif_bytes = piexif.dump(exif_dict)
    img.save(str(img_path), exif=exif_bytes)

    tagger = AITagger(model_dir=str(tmp_path))
    tensor = tagger.preprocess(str(img_path))

    assert tensor is not None
    assert tensor.shape == (1, 3, 224, 224)


def test_cpu_mode_enforcement(tmp_path):
    (tmp_path / "mobilenetv2.onnx").write_bytes(MOCK_MODEL_DATA)
    (tmp_path / "labels.txt").write_bytes(MOCK_LABELS_DATA)

    with patch("onnxruntime.InferenceSession") as mock_sess:
        mock_instance = MagicMock()
        mock_instance.get_providers.return_value = ["CPUExecutionProvider"]
        mock_sess.return_value = mock_instance

        tagger = AITagger(model_dir=str(tmp_path), hardware_acceleration=False)
        assert tagger.hardware_acceleration is False
        assert tagger.active_provider == "CPUExecutionProvider"

        # Verify InferenceSession was initialized strictly with CPUExecutionProvider
        mock_sess.assert_called_once()
        args, kwargs = mock_sess.call_args
        assert kwargs.get("providers") == ["CPUExecutionProvider"]


def test_provider_priority_ranking_unknown_providers():
    with patch("onnxruntime.get_available_providers", return_value=["AzureExecutionProvider", "CPUExecutionProvider", "CUDAExecutionProvider"]):
        providers = get_prioritized_providers()
        # CUDA should rank ahead of CPU, and AzureExecutionProvider should NOT outrank CPU
        assert providers == ["CUDAExecutionProvider", "CPUExecutionProvider", "AzureExecutionProvider"]


def test_configurable_thresholding_and_bounds(tmp_path):
    (tmp_path / "mobilenetv2.onnx").write_bytes(MOCK_MODEL_DATA)
    (tmp_path / "labels.txt").write_bytes(MOCK_LABELS_DATA)

    tagger = AITagger(model_dir=str(tmp_path))
    tagger.session = MagicMock()
    tagger.labels = ["tench", "goldfish", "great white shark"]

    # Logits yielding probabilities ~ [0.70, 0.20, 0.10]
    logits = np.array([[0.0, 10.0, 8.75, 8.0]], dtype=np.float32)
    tagger.session.run.return_value = [logits]
    tagger.session.get_inputs.return_value = [MagicMock(name="input")]

    with patch.object(tagger, "preprocess", return_value=np.zeros((1, 3, 224, 224), dtype=np.float32)):
        # Default threshold 0.5 -> only top prediction (~0.70)
        tags_default = tagger.get_tags("test.jpg", top_k=3, threshold=0.5)
        assert tags_default == ["tench"]

        # Lower threshold 0.15 -> top two predictions
        tags_low = tagger.get_tags("test.jpg", top_k=3, threshold=0.15)
        assert tags_low == ["tench", "goldfish"]


def test_non_finite_logits_and_probabilities(tmp_path):
    (tmp_path / "mobilenetv2.onnx").write_bytes(MOCK_MODEL_DATA)
    (tmp_path / "labels.txt").write_bytes(MOCK_LABELS_DATA)

    tagger = AITagger(model_dir=str(tmp_path))
    tagger.session = MagicMock()
    tagger.labels = ["tench", "goldfish", "great white shark"]

    # Logits containing NaN and Inf
    logits = np.array([[np.nan, np.inf, 10.0, -np.inf]], dtype=np.float32)
    tagger.session.run.return_value = [logits]
    tagger.session.get_inputs.return_value = [MagicMock(name="input")]

    with patch.object(tagger, "preprocess", return_value=np.zeros((1, 3, 224, 224), dtype=np.float32)):
        tags = tagger.get_tags("test.jpg", top_k=3, threshold=0.1)
        assert isinstance(tags, list)


def test_downloader_interruption_cancellation(tmp_path):
    downloader = ModelDownloader(model_dir=str(tmp_path))

    results = []
    downloader.finished.connect(lambda ok, msg: results.append((ok, msg)))

    with patch.object(downloader, "isInterruptionRequested", return_value=True):
        downloader.run()

    assert len(results) == 1
    assert results[0][0] is False
    assert "canceled" in results[0][1].lower()
    assert not is_model_and_labels_valid(str(tmp_path))
