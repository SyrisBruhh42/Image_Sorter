from __future__ import annotations

import hashlib
import os
import ssl
import tempfile
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

import numpy as np
import onnxruntime as ort
import psutil
from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from .hardware_scan import get_prioritized_providers
from .logger import logger
from .metadata_io import write_metadata
from .model_assets import LABELS_SHA256, LABELS_URL, MODEL_SHA256, MODEL_URL
from .paths import get_data_dir

__all__ = ["AITagger", "BaseVisionEngine", "ModelDownloader", "write_metadata"]



def get_model_dir(model_dir: str | None = None) -> str:
    """Returns the resolved directory path for AI model artifacts."""
    if model_dir is not None:
        return model_dir
    from .component_manager import ComponentManager
    active = ComponentManager().active_path("ai.mobilenet-v2")
    return str(active) if active else str(get_data_dir() / "components" / "unavailable" / "ai.mobilenet-v2")


def is_model_and_labels_valid(model_dir: str | None = None) -> bool:
    """
    Verifies that both the model file and labels file exist and match their expected SHA256 checksums.
    Does not perform model loading or network access.
    """
    target_dir = get_model_dir(model_dir)
    model_path = os.path.join(target_dir, "mobilenetv2.onnx")
    labels_path = os.path.join(target_dir, "labels.txt")

    if not (os.path.exists(model_path) and os.path.exists(labels_path)):
        return False

    if calculate_sha256(model_path) != MODEL_SHA256:
        return False

    return calculate_sha256(labels_path) == LABELS_SHA256


def calculate_sha256(filepath: str) -> str:
    """Calculates the SHA256 checksum of a file using 64KB chunks."""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(64 * 1024), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except OSError as e:
        logger.error(f"Failed to read file for checksum calculation: {e}")
        return ""


def _download_file_secure(
    url: str,
    dest_temp_path: str,
    progress_callback=None,
    timeout: float = 15.0,
    cancellation_check=None
) -> None:
    """Downloads a file using secure TLS 1.2+ HTTPS streaming context in 64KB chunks with interruption checks."""
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if hasattr(ctx, "minimum_version"):
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ImageSorter/0.1 (optional-model-downloader)"}
    )
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response, open(dest_temp_path, "wb") as out_file:
        total_size = int(response.headers.get("Content-Length", 0))
        read_so_far = 0
        while True:
            if cancellation_check and cancellation_check():
                raise InterruptedError("Download canceled by request.")
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            out_file.write(chunk)
            read_so_far += len(chunk)
            if progress_callback and total_size > 0:
                percent = int((read_so_far / total_size) * 100)
                progress_callback(min(percent, 100))


class ModelDownloader(QThread):
    """
    Downloads AI models in a background thread with zero-trust SHA256 verification and interruption handling.
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, model_dir: str | None = None) -> None:
        super().__init__()
        self.model_dir = get_model_dir(model_dir)
        self.model_path = os.path.join(self.model_dir, "mobilenetv2.onnx")
        self.labels_path = os.path.join(self.model_dir, "labels.txt")

    def run(self) -> None:
        try:
            os.makedirs(self.model_dir, exist_ok=True)

            labels_valid = os.path.exists(self.labels_path) and calculate_sha256(self.labels_path) == LABELS_SHA256
            if not labels_valid:
                if self.isInterruptionRequested():
                    self.finished.emit(False, "Download canceled.")
                    return
                logger.info(f"Downloading labels to {self.labels_path}")
                fd, temp_labels_path = tempfile.mkstemp(dir=self.model_dir, prefix="dl_labels_", suffix=".tmp")
                os.close(fd)
                try:
                    if self.isInterruptionRequested():
                        self.finished.emit(False, "Download canceled.")
                        return
                    _download_file_secure(
                        LABELS_URL,
                        temp_labels_path,
                        timeout=15.0,
                        cancellation_check=self.isInterruptionRequested
                    )
                    if self.isInterruptionRequested():
                        self.finished.emit(False, "Download canceled.")
                        return
                    checksum = calculate_sha256(temp_labels_path)
                    if checksum != LABELS_SHA256:
                        err_msg = f"Labels Cryptographic Integrity Failure: Expected {LABELS_SHA256}, got {checksum}"
                        logger.critical(err_msg)
                        self.finished.emit(False, err_msg)
                        return
                    os.replace(temp_labels_path, self.labels_path)
                except InterruptedError:
                    self.finished.emit(False, "Download canceled.")
                    return
                except Exception as e:
                    raise Exception(f"Network error downloading labels: {e}")
                finally:
                    if os.path.exists(temp_labels_path):
                        try:
                            os.remove(temp_labels_path)
                        except OSError:
                            pass

            model_valid = os.path.exists(self.model_path) and calculate_sha256(self.model_path) == MODEL_SHA256
            if not model_valid:
                if self.isInterruptionRequested():
                    self.finished.emit(False, "Download canceled.")
                    return
                logger.info(f"Downloading model to {self.model_path}")
                fd, temp_path = tempfile.mkstemp(dir=self.model_dir, prefix="dl_model_", suffix=".tmp")
                os.close(fd)

                try:
                    if self.isInterruptionRequested():
                        self.finished.emit(False, "Download canceled.")
                        return
                    _download_file_secure(
                        MODEL_URL,
                        temp_path,
                        progress_callback=lambda p: self.progress.emit(p),
                        timeout=15.0,
                        cancellation_check=self.isInterruptionRequested
                    )
                    if self.isInterruptionRequested():
                        self.finished.emit(False, "Download canceled.")
                        return
                    checksum = calculate_sha256(temp_path)
                    logger.info(f"Downloaded model SHA256: {checksum}")

                    if checksum != MODEL_SHA256:
                        err_msg = f"Model Cryptographic Integrity Failure: Expected {MODEL_SHA256}, got {checksum}"
                        logger.critical(err_msg)
                        self.finished.emit(False, err_msg)
                        return

                    os.replace(temp_path, self.model_path)
                    logger.info("Model download and verification complete.")
                except InterruptedError:
                    self.finished.emit(False, "Download canceled.")
                    return
                except Exception as e:
                    raise Exception(f"Network error downloading model: {e}")
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass

            if self.isInterruptionRequested():
                self.finished.emit(False, "Download canceled.")
                return

            if is_model_and_labels_valid(self.model_dir):
                self.finished.emit(True, "Model ready.")
            else:
                self.finished.emit(False, "Model or labels verification failed after download.")
        except Exception as e:
            logger.error(f"Model download failed: {e}", exc_info=True)
            self.finished.emit(False, str(e))


class BaseVisionEngine(ABC):
    """
    Abstract contract for decoupled vision engines (e.g., MobileNet, CLIP, FAISS).
    """

    @abstractmethod
    def load_model(self) -> None:
        """Loads the vision model into memory."""

    @abstractmethod
    def get_tags(self, image_path: str, top_k: int = 3, *, threshold: float = 0.5) -> list[str]:
        """Returns tags for the specified image."""


class AITagger(BaseVisionEngine):
    """
    Implementation of MobileNetV2 ONNX tagger supporting dynamic multi-provider acceleration.
    """
    API_VERSION: int = 1

    def __init__(self, model_dir: str | None = None, *, hardware_acceleration: bool = True) -> None:
        self.model_dir: str = get_model_dir(model_dir)
        self.hardware_acceleration: bool = hardware_acceleration
        self.model_path: str = os.path.join(self.model_dir, "mobilenetv2.onnx")
        self.labels_path: str = os.path.join(self.model_dir, "labels.txt")
        self.session: ort.InferenceSession | None = None
        self.labels: list[str] = []
        self.active_provider: str = "None"
        self.load_model()

    def load_model(self) -> None:
        """Attempts to load ONNX model across prioritized providers with safe fallbacks."""
        if not is_model_and_labels_valid(self.model_dir):
            logger.warning(f"AI model or labels missing or invalid at {self.model_dir}")
            self.session = None
            self.labels = []
            self.active_provider = "None"
            return

        try:
            with open(self.labels_path, 'r', encoding='utf-8') as f:
                self.labels = [line.strip() for line in f]
        except Exception as e:
            logger.error(f"Failed to load labels from {self.labels_path}: {e}")
            return

        physical_cores = psutil.cpu_count(logical=False) or 1
        intra_threads = max(1, min(physical_cores, 4))

        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = intra_threads
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.enable_cpu_mem_arena = True
        sess_options.enable_mem_pattern = True

        if not self.hardware_acceleration:
            providers = ['CPUExecutionProvider']
        else:
            prioritized_providers = get_prioritized_providers()
            top_provider = prioritized_providers[0] if prioritized_providers else "CPUExecutionProvider"
            providers = [top_provider, 'CPUExecutionProvider'] if top_provider != 'CPUExecutionProvider' else ['CPUExecutionProvider']

        try:
            self.session = ort.InferenceSession(self.model_path, sess_options, providers=providers)
            self.active_provider = self.session.get_providers()[0] if self.session.get_providers() else "CPUExecutionProvider"
            logger.info(f"Loaded AI model from {self.model_path} with active provider {self.active_provider}")
            return
        except Exception as e:
            logger.warning(f"Failed to initialize ONNX session with providers {providers}: {e}")

        # Final safety fallback to CPU only
        try:
            self.session = ort.InferenceSession(self.model_path, sess_options, providers=['CPUExecutionProvider'])
            self.active_provider = 'CPUExecutionProvider'
            logger.info("Loaded AI model with CPUExecutionProvider fallback.")
        except Exception as e:
            logger.error(f"Error loading AI model with fallback: {e}", exc_info=True)
            self.session = None
            self.active_provider = "None"

    def preprocess(self, image_path: str) -> np.ndarray | None:
        """
        Preprocesses an image tensor for MobileNetV2 inference using HuggingFace pinned spec:
        - EXIF transpose orientation
        - Shortest-edge resize to 256 using bilinear resampling
        - Center crop 224x224
        - Pixel rescale [0.0, 1.0] (/ 255.0)
        - Mean/std normalization [0.5, 0.5, 0.5]
        - Output float32 contiguous tensor layout [1, 3, 224, 224]
        """
        try:
            from .ai_preprocessing import preprocess_image
            return preprocess_image(image_path)
        except Image.DecompressionBombError as e:
            logger.error(f"Decompression bomb detected in {image_path}: {e}")
            return None
        except OSError as e:
            logger.error(f"OS error reading image {image_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error preprocessing {image_path}: {e}")
            return None

    def get_tags(self, image_path: str, top_k: int = 3, *, threshold: float = 0.5) -> list[str]:
        """Runs inference on an image and returns top_k tags above confidence threshold."""
        if not self.session or not self.labels:
            logger.warning("Attempted to get tags, but model/labels are not loaded.")
            return []

        input_data = self.preprocess(image_path)
        if input_data is None:
            return []

        try:
            input_name = self.session.get_inputs()[0].name
            raw_result = self.session.run(None, {input_name: input_data})

            res = raw_result[0][0]
            res = np.nan_to_num(res, nan=-1e9, posinf=-1e9, neginf=-1e9)

            if len(res) == len(self.labels) + 1:
                # Explicitly strip background class at index 0
                res = res[1:]
            elif len(res) > len(self.labels):
                res = res[1 : 1 + len(self.labels)]

            max_res = np.max(res)
            exp_res = np.exp(res - max_res)
            exp_res = np.nan_to_num(exp_res, nan=0.0, posinf=0.0, neginf=0.0)
            sum_exp = exp_res.sum()
            if sum_exp <= 0:
                return []
            probs = exp_res / sum_exp

            k = max(1, min(top_k, len(self.labels)))
            top_indices = np.argsort(probs)[-k:][::-1]
            tags = [self.labels[i] for i in top_indices if probs[i] >= threshold]
            return tags
        except Exception as e:
            logger.error(f"Error during AI inference for {image_path}: {e}")
            return []
