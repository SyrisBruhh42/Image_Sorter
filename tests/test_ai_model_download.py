import os
import hashlib
from unittest.mock import patch, MagicMock
import numpy as np
import pytest
from PyQt6.QtWidgets import QMessageBox

from imagesorter.ai_tagger import (
    ModelDownloader,
    AITagger,
    is_model_and_labels_valid,
    MODEL_SHA256,
    LABELS_SHA256,
)
from imagesorter.ui_settings import SettingsWindow
from imagesorter.settings_manager import SettingsManager

VALID_MODEL_CONTENT = b"MOCK_VALID_ONNX_MODEL_CONTENT_12345"
VALID_MODEL_HASH = hashlib.sha256(VALID_MODEL_CONTENT).hexdigest()

VALID_LABELS_CONTENT = b"tench\ngoldfish\ngreat white shark\n"
VALID_LABELS_HASH = hashlib.sha256(VALID_LABELS_CONTENT).hexdigest()


@pytest.fixture(autouse=True)
def patch_hashes(monkeypatch):
    """Patch expected model and label SHA-256 hashes to match test mock content."""
    monkeypatch.setattr("imagesorter.ai_tagger.MODEL_SHA256", VALID_MODEL_HASH)
    monkeypatch.setattr("imagesorter.ai_tagger.LABELS_SHA256", VALID_LABELS_HASH)


def test_fresh_download_success_path(qtbot, tmp_path):
    model_dir = str(tmp_path)
    downloader = ModelDownloader(model_dir=model_dir)

    results = []
    downloader.finished.connect(lambda success, msg: results.append((success, msg)))

    def mock_download(url, dest_temp_path, progress_callback=None, timeout=15.0):
        with open(dest_temp_path, "wb") as f:
            if "model" in url:
                f.write(VALID_MODEL_CONTENT)
            else:
                f.write(VALID_LABELS_CONTENT)

    with patch("imagesorter.ai_tagger._download_file_secure", side_effect=mock_download):
        downloader.run()

    assert len(results) == 1
    success, msg = results[0]
    assert success is True
    assert msg == "Model ready."
    assert is_model_and_labels_valid(model_dir) is True
    assert (tmp_path / "mobilenetv2.onnx").read_bytes() == VALID_MODEL_CONTENT
    assert (tmp_path / "labels.txt").read_bytes() == VALID_LABELS_CONTENT


def test_download_http_failure_path(qtbot, tmp_path):
    model_dir = str(tmp_path)
    downloader = ModelDownloader(model_dir=model_dir)

    results = []
    downloader.finished.connect(lambda success, msg: results.append((success, msg)))

    def mock_download_fail(url, dest_temp_path, progress_callback=None, timeout=15.0):
        raise OSError("Connection refused / 404 Not Found")

    with patch("imagesorter.ai_tagger._download_file_secure", side_effect=mock_download_fail):
        downloader.run()

    assert len(results) == 1
    success, msg = results[0]
    assert success is False
    assert "Network error" in msg
    assert is_model_and_labels_valid(model_dir) is False
    assert len(list(tmp_path.glob("*.tmp"))) == 0


def test_model_checksum_mismatch_path(qtbot, tmp_path):
    model_dir = str(tmp_path)
    downloader = ModelDownloader(model_dir=model_dir)

    results = []
    downloader.finished.connect(lambda success, msg: results.append((success, msg)))

    def mock_download_corrupt_model(url, dest_temp_path, progress_callback=None, timeout=15.0):
        with open(dest_temp_path, "wb") as f:
            if "model" in url:
                f.write(b"CORRUPTED_MODEL_BYTES")
            else:
                f.write(VALID_LABELS_CONTENT)

    with patch("imagesorter.ai_tagger._download_file_secure", side_effect=mock_download_corrupt_model):
        downloader.run()

    assert len(results) == 1
    success, msg = results[0]
    assert success is False
    assert "Model Cryptographic Integrity Failure" in msg
    assert not (tmp_path / "mobilenetv2.onnx").exists()
    assert (tmp_path / "labels.txt").read_bytes() == VALID_LABELS_CONTENT
    assert len(list(tmp_path.glob("*.tmp"))) == 0


def test_labels_checksum_mismatch_path(qtbot, tmp_path):
    model_dir = str(tmp_path)
    downloader = ModelDownloader(model_dir=model_dir)

    results = []
    downloader.finished.connect(lambda success, msg: results.append((success, msg)))

    def mock_download_corrupt_labels(url, dest_temp_path, progress_callback=None, timeout=15.0):
        with open(dest_temp_path, "wb") as f:
            f.write(b"CORRUPTED_LABELS_CONTENT")

    with patch("imagesorter.ai_tagger._download_file_secure", side_effect=mock_download_corrupt_labels):
        downloader.run()

    assert len(results) == 1
    success, msg = results[0]
    assert success is False
    assert "Labels Cryptographic Integrity Failure" in msg
    assert not (tmp_path / "labels.txt").exists()
    assert len(list(tmp_path.glob("*.tmp"))) == 0


def test_corrupt_existing_model_path_revalidation_and_cleanup(qtbot, tmp_path):
    model_dir = str(tmp_path)
    # Write corrupt existing model file and valid labels file
    (tmp_path / "mobilenetv2.onnx").write_bytes(b"EXISTING_CORRUPT_MODEL_BYTES")
    (tmp_path / "labels.txt").write_bytes(VALID_LABELS_CONTENT)

    assert is_model_and_labels_valid(model_dir) is False

    tagger = AITagger(model_dir=model_dir)
    assert tagger.session is None
    assert tagger.labels == []

    # ModelDownloader should detect existing model is invalid and download replacement
    downloader = ModelDownloader(model_dir=model_dir)
    results = []
    downloader.finished.connect(lambda success, msg: results.append((success, msg)))

    def mock_download_success(url, dest_temp_path, progress_callback=None, timeout=15.0):
        with open(dest_temp_path, "wb") as f:
            f.write(VALID_MODEL_CONTENT)

    with patch("imagesorter.ai_tagger._download_file_secure", side_effect=mock_download_success):
        downloader.run()

    assert len(results) == 1
    assert results[0][0] is True
    assert (tmp_path / "mobilenetv2.onnx").read_bytes() == VALID_MODEL_CONTENT
    assert is_model_and_labels_valid(model_dir) is True


def test_missing_labels_path(qtbot, tmp_path):
    model_dir = str(tmp_path)
    # Only valid model exists, labels missing
    (tmp_path / "mobilenetv2.onnx").write_bytes(VALID_MODEL_CONTENT)

    assert is_model_and_labels_valid(model_dir) is False

    tagger = AITagger(model_dir=model_dir)
    assert tagger.session is None

    downloader = ModelDownloader(model_dir=model_dir)
    results = []
    downloader.finished.connect(lambda success, msg: results.append((success, msg)))

    def mock_download_labels_only(url, dest_temp_path, progress_callback=None, timeout=15.0):
        with open(dest_temp_path, "wb") as f:
            f.write(VALID_LABELS_CONTENT)

    with patch("imagesorter.ai_tagger._download_file_secure", side_effect=mock_download_labels_only) as mock_dl:
        downloader.run()
        # Verify only labels were downloaded since existing model was already valid
        assert mock_dl.call_count == 1

    assert results[0][0] is True
    assert is_model_and_labels_valid(model_dir) is True


def test_ui_settings_validation_and_control_states(qtbot, tmp_path):
    config_path = tmp_path / "settings.json"
    settings_mgr = SettingsManager(str(config_path))

    # Persist enabled=True when files are missing/invalid
    settings_mgr.set("ai_tagger", "enabled", True)

    with patch("imagesorter.ui_settings.is_model_and_labels_valid", side_effect=lambda dir=None: is_model_and_labels_valid(str(tmp_path))):
        with patch("imagesorter.ui_settings.get_model_dir", return_value=str(tmp_path)):
            win = SettingsWindow(settings_mgr)
            qtbot.addWidget(win)

            # UI should uncheck and disable the checkbox because files are invalid
            assert win.chk_ai_enable.isChecked() is False
            assert win.chk_ai_enable.isEnabled() is False
            assert win.btn_download_model.text() == "Download Model"
            assert win.btn_download_model.isEnabled() is True

            # Simulate failed download
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                 patch.object(QMessageBox, "critical") as mock_crit, \
                 patch.object(ModelDownloader, "start", lambda self: self.run()):
                with patch("imagesorter.ai_tagger._download_file_secure", side_effect=OSError("Failed")):
                    win.download_ai_model()

            assert mock_crit.called
            assert win.btn_download_model.isEnabled() is True
            assert win.chk_ai_enable.isEnabled() is False

            # Write valid model and labels into tmp_path
            (tmp_path / "mobilenetv2.onnx").write_bytes(VALID_MODEL_CONTENT)
            (tmp_path / "labels.txt").write_bytes(VALID_LABELS_CONTENT)

            # Re-initialize or refresh status
            win.refresh_ai_model_status()
            assert win.btn_download_model.text() == "Model Downloaded"
            assert win.btn_download_model.isEnabled() is False
            assert win.chk_ai_enable.isEnabled() is True


def test_background_class_index_zero_mapping(tmp_path):
    model_dir = str(tmp_path)
    (tmp_path / "mobilenetv2.onnx").write_bytes(VALID_MODEL_CONTENT)
    (tmp_path / "labels.txt").write_bytes(b"label_0\nlabel_1\nlabel_2\n")

    monkeypatch_labels_hash = hashlib.sha256(b"label_0\nlabel_1\nlabel_2\n").hexdigest()

    with patch("imagesorter.ai_tagger.LABELS_SHA256", monkeypatch_labels_hash):
        assert is_model_and_labels_valid(model_dir) is True

        tagger = AITagger(model_dir=model_dir)
        tagger.session = MagicMock()
        tagger.labels = ["label_0", "label_1", "label_2"]

        # Mock session output with 4 elements (1 background + 3 label logits)
        # Index 0: background class (value 100.0)
        # Index 1: label_0 (value 0.1)
        # Index 2: label_1 (value 10.0) -> Highest among labels
        # Index 3: label_2 (value 0.5)
        mock_output = np.array([[100.0, 0.1, 10.0, 0.5]], dtype=np.float32)
        tagger.session.run.return_value = [mock_output]
        tagger.session.get_inputs.return_value = [MagicMock(name="input_tensor")]

        with patch.object(tagger, "preprocess", return_value=np.zeros((1, 3, 224, 224), dtype=np.float32)):
            tags = tagger.get_tags("dummy_path.jpg", top_k=2)

        # Background class (index 0) must be ignored; top tag must be label_1
        assert tags[0] == "label_1"
        assert "background" not in tags
