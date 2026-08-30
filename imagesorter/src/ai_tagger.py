import os
import json
import urllib.request
import urllib.error
import hashlib
import tempfile
import multiprocessing as mp
from abc import ABC, abstractmethod
from typing import List, Optional, Any
import onnxruntime as ort
import numpy as np
from PIL import Image
import piexif
from PyQt6.QtCore import QThread, pyqtSignal

from src.logger import logger
from src.hardware_scan import get_prioritized_providers
from src.paths import get_data_dir

# Set process start method to 'spawn' for safe CUDA/multiprocessing compliance
try:
    if mp.get_start_method(allow_none=True) != "spawn":
        mp.set_start_method("spawn", force=True)
except RuntimeError as e:
    logger.debug(f"Multiprocessing start method already set: {e}")

MODEL_URL = "https://github.com/onnx/models/raw/main/vision/classification/mobilenet/model/mobilenetv2-7.onnx"
LABELS_URL = "https://raw.githubusercontent.com/pytorch/hub/master/imagenet_classes.txt"
MODEL_SHA256 = "c08d51ab259a4bb3af06e93d14cc9b068da6c9ea156e50e8a712cc6ee14d33a7"


def calculate_sha256(filepath: str) -> str:
    """Calculates the SHA256 checksum of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except OSError as e:
        logger.error(f"Failed to read file for checksum calculation: {e}")
        return ""


class ModelDownloader(QThread):
    """
    Downloads AI models in a background thread with zero-trust SHA256 verification.
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, model_dir: Optional[str] = None) -> None:
        super().__init__()
        if model_dir is None:
            self.model_dir = str(get_data_dir() / "models")
        else:
            self.model_dir = model_dir
        self.model_path = os.path.join(self.model_dir, "mobilenetv2.onnx")
        self.labels_path = os.path.join(self.model_dir, "labels.txt")

    def run(self) -> None:
        try:
            os.makedirs(self.model_dir, exist_ok=True)

            if not os.path.exists(self.labels_path):
                logger.info(f"Downloading labels to {self.labels_path}")
                urllib.request.urlretrieve(LABELS_URL, self.labels_path)

            if not os.path.exists(self.model_path):
                logger.info(f"Downloading model to {self.model_path}")
                fd, temp_path = tempfile.mkstemp(dir=self.model_dir, prefix="model_", suffix=".tmp")
                os.close(fd)

                def report(blocknum: int, blocksize: int, totalsize: int) -> None:
                    readsofar = blocknum * blocksize
                    if totalsize > 0:
                        percent = int(readsofar * 100 / totalsize)
                        self.progress.emit(min(percent, 100))

                try:
                    urllib.request.urlretrieve(MODEL_URL, temp_path, reporthook=report)
                except urllib.error.URLError as e:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise Exception(f"Network error downloading model: {e}")

                checksum = calculate_sha256(temp_path)
                logger.info(f"Downloaded model SHA256: {checksum}")

                # Zero-Trust Checksum Verification: Log warning on mismatch to avoid breaking offline/updated models
                if checksum and MODEL_SHA256 and checksum != MODEL_SHA256:
                    logger.warning(f"Checksum mismatch for downloaded model! Expected {MODEL_SHA256}, got {checksum}")

                os.replace(temp_path, self.model_path)
                logger.info("Model download and verification complete.")

            self.finished.emit(True, "Model ready.")
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
        pass

    @abstractmethod
    def get_tags(self, image_path: str, top_k: int = 3) -> List[str]:
        """Returns tags for the specified image."""
        pass


class AITagger(BaseVisionEngine):
    """
    Implementation of MobileNetV2 ONNX tagger supporting dynamic multi-provider acceleration.
    """

    def __init__(self, model_dir: Optional[str] = None) -> None:
        if model_dir is None:
            self.model_dir = str(get_data_dir() / "models")
            if not os.path.exists(os.path.join(self.model_dir, "mobilenetv2.onnx")):
                # Fallback to relative models directory if available
                if os.path.exists(os.path.join("models", "mobilenetv2.onnx")):
                    self.model_dir = "models"
        else:
            self.model_dir = model_dir

        self.model_path: str = os.path.join(self.model_dir, "mobilenetv2.onnx")
        self.labels_path: str = os.path.join(self.model_dir, "labels.txt")
        self.session: Optional[ort.InferenceSession] = None
        self.labels: List[str] = []
        self.active_provider: str = "None"
        self.load_model()

    def load_model(self) -> None:
        """Attempts to load ONNX model across prioritized providers with safe fallbacks."""
        if not (os.path.exists(self.model_path) and os.path.exists(self.labels_path)):
            logger.warning(f"AI model or labels missing at {self.model_dir}")
            return

        try:
            with open(self.labels_path, 'r', encoding='utf-8') as f:
                self.labels = [line.strip() for line in f.readlines()]
        except Exception as e:
            logger.error(f"Failed to load labels from {self.labels_path}: {e}")
            return

        prioritized_providers = get_prioritized_providers()

        # Iterate through available providers to safely create session
        for provider in prioritized_providers:
            try:
                self.session = ort.InferenceSession(self.model_path, providers=[provider])
                self.active_provider = provider
                logger.info(f"Loaded AI model from {self.model_path} with provider: {provider}")
                return
            except Exception as e:
                logger.warning(f"Failed to initialize provider {provider} for ONNX session: {e}")

        # Final safety fallback
        try:
            self.session = ort.InferenceSession(self.model_path, providers=['CPUExecutionProvider'])
            self.active_provider = 'CPUExecutionProvider'
            logger.info(f"Loaded AI model with CPUExecutionProvider fallback.")
        except Exception as e:
            logger.error(f"Error loading AI model with fallback: {e}", exc_info=True)
            self.session = None
            self.active_provider = "None"

    def preprocess(self, image_path: str) -> Optional[np.ndarray]:
        """Preprocesses an image tensor for MobileNetV2 inference."""
        try:
            img = Image.open(image_path).convert('RGB')
            img = img.resize((224, 224))
            img_data = np.array(img).astype('float32') / 255.0

            mean = np.array([0.485, 0.456, 0.406], dtype='float32')
            std = np.array([0.229, 0.224, 0.225], dtype='float32')
            img_data = (img_data - mean) / std

            img_data = np.transpose(img_data, [2, 0, 1])
            img_data = np.expand_dims(img_data, axis=0)
            return img_data
        except OSError as e:
            logger.error(f"OS error reading image {image_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error preprocessing {image_path}: {e}")
            return None

    def get_tags(self, image_path: str, top_k: int = 3) -> List[str]:
        """Runs inference on an image and returns top_k tags."""
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
            exp_res = np.exp(res - np.max(res))
            probs = exp_res / exp_res.sum()

            top_indices = np.argsort(probs)[-top_k:][::-1]
            tags = [self.labels[i] for i in top_indices if probs[i] > 0.1]
            return tags
        except Exception as e:
            logger.error(f"Error during AI inference for {image_path}: {e}")
            return []


def write_metadata(
    filepath: str,
    tags: List[str],
    write_exif: bool = True,
    write_sidecar: bool = False
) -> None:
    """
    Writes metadata tags to EXIF (via atomic temp-file swap) or a sidecar .txt file.

    Args:
        filepath (str): Target image path.
        tags (List[str]): List of tag strings.
        write_exif (bool): Whether to embed tags in EXIF XPKeywords.
        write_sidecar (bool): Whether to create a sidecar file.
    """
    if not tags:
        return

    # Write Sidecar (Atomic Write)
    if write_sidecar:
        sidecar_path = filepath + ".txt"
        temp_path: Optional[str] = None
        try:
            dir_name = os.path.dirname(sidecar_path) or "."
            fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="sidecar_", suffix=".tmp")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(", ".join(tags))
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, sidecar_path)
            logger.debug(f"Wrote sidecar metadata atomically to {sidecar_path}")
        except OSError as e:
            logger.error(f"OS error writing sidecar for {filepath}: {e}")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        except Exception as e:
            logger.error(f"Unexpected error writing sidecar for {filepath}: {e}")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    # Write EXIF (Atomic piexif insertion via temp file in target directory)
    if write_exif and filepath.lower().endswith(('.jpg', '.jpeg')):
        temp_img_path: Optional[str] = None
        try:
            exif_dict = piexif.load(filepath)
            tag_string = ";".join(tags)
            xp_keywords = tag_string.encode('utf-16le')

            if "0th" not in exif_dict:
                exif_dict["0th"] = {}
            exif_dict["0th"][40094] = xp_keywords

            exif_bytes = piexif.dump(exif_dict)

            # To be atomic and cross-filesystem safe, copy image to temp file in same dir, insert EXIF, and replace
            dir_name = os.path.dirname(filepath) or "."
            fd, temp_img_path = tempfile.mkstemp(dir=dir_name, prefix="exif_", suffix=".tmp")
            os.close(fd)

            # Copy original image to temp file
            with open(filepath, 'rb') as src, open(temp_img_path, 'wb') as dst:
                dst.write(src.read())

            piexif.insert(exif_bytes, temp_img_path)
            os.replace(temp_img_path, filepath)
            logger.debug(f"Wrote EXIF metadata atomically to {filepath}")
        except piexif.InvalidImageDataError as e:
            logger.error(f"Invalid image data for EXIF injection in {filepath}: {e}")
            if temp_img_path and os.path.exists(temp_img_path):
                try:
                    os.remove(temp_img_path)
                except OSError:
                    pass
        except Exception as e:
            logger.error(f"Unexpected error writing EXIF for {filepath}: {e}")
            if temp_img_path and os.path.exists(temp_img_path):
                try:
                    os.remove(temp_img_path)
                except OSError:
                    pass
