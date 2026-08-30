import os
import json
import ssl
import shutil
import urllib.request
import urllib.error
import hashlib
import tempfile
import multiprocessing as mp
from abc import ABC, abstractmethod
from typing import List, Optional, Any
import psutil
import onnxruntime as ort
import numpy as np
from PIL import Image
import piexif
from PyQt6.QtCore import QThread, pyqtSignal

from .logger import logger
from .hardware_scan import get_prioritized_providers
from .paths import get_data_dir

# Set process start method to 'spawn' for safe CUDA/multiprocessing compliance
try:
    if mp.get_start_method(allow_none=True) != "spawn":
        mp.set_start_method("spawn", force=True)
except RuntimeError as e:
    logger.debug(f"Multiprocessing start method already set: {e}")

MODEL_URL = "https://huggingface.co/onnx-community/mobilenet_v2_1.0_224-ONNX/resolve/f7f884d9505b4c69f8a260d9967ff7791bafa498/onnx/model.onnx"
MODEL_SHA256 = "2e731702ec8374128edfc9f7d344c44287e7791bb3c7ae25a628c2c2dec83ce6"
LABELS_URL = "https://raw.githubusercontent.com/pytorch/hub/a6fc887fbbbda0dd37c440bf8a145f1da6707d6b/imagenet_classes.txt"
LABELS_SHA256 = "1f386e0d1cb6e28b9c2dac651c3dea6801e98ad1b41a14ce6bb1a093d72069f5"


def get_model_dir(model_dir: Optional[str] = None) -> str:
    """Returns the resolved directory path for AI model artifacts."""
    if model_dir is not None:
        return model_dir
    default_dir = str(get_data_dir() / "models")
    model_name = "mobilenetv2.onnx"
    labels_name = "labels.txt"
    if not (os.path.exists(os.path.join(default_dir, model_name)) and os.path.exists(os.path.join(default_dir, labels_name))):
        rel_dir = "models"
        if os.path.exists(os.path.join(rel_dir, model_name)) and os.path.exists(os.path.join(rel_dir, labels_name)):
            return rel_dir
    return default_dir


def is_model_and_labels_valid(model_dir: Optional[str] = None) -> bool:
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

    if calculate_sha256(labels_path) != LABELS_SHA256:
        return False

    return True


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
    timeout: float = 15.0
) -> None:
    """Downloads a file using secure TLS 1.2+ HTTPS streaming context in 64KB chunks."""
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if hasattr(ctx, "minimum_version"):
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ImageSorter-Enterprise/1.0 (Cross-Platform; x86_64)"}
    )
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response, open(dest_temp_path, "wb") as out_file:
        total_size = int(response.headers.get("Content-Length", 0))
        read_so_far = 0
        while True:
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
    Downloads AI models in a background thread with zero-trust SHA256 verification.
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, model_dir: Optional[str] = None) -> None:
        super().__init__()
        self.model_dir = get_model_dir(model_dir)
        self.model_path = os.path.join(self.model_dir, "mobilenetv2.onnx")
        self.labels_path = os.path.join(self.model_dir, "labels.txt")

    def run(self) -> None:
        try:
            os.makedirs(self.model_dir, exist_ok=True)

            labels_valid = os.path.exists(self.labels_path) and calculate_sha256(self.labels_path) == LABELS_SHA256
            if not labels_valid:
                logger.info(f"Downloading labels to {self.labels_path}")
                fd, temp_labels_path = tempfile.mkstemp(dir=self.model_dir, prefix="dl_labels_", suffix=".tmp")
                os.close(fd)
                try:
                    _download_file_secure(LABELS_URL, temp_labels_path, timeout=15.0)
                    checksum = calculate_sha256(temp_labels_path)
                    if checksum != LABELS_SHA256:
                        err_msg = f"Labels Cryptographic Integrity Failure: Expected {LABELS_SHA256}, got {checksum}"
                        logger.critical(err_msg)
                        self.finished.emit(False, err_msg)
                        return
                    os.replace(temp_labels_path, self.labels_path)
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
                logger.info(f"Downloading model to {self.model_path}")
                fd, temp_path = tempfile.mkstemp(dir=self.model_dir, prefix="dl_model_", suffix=".tmp")
                os.close(fd)

                try:
                    _download_file_secure(
                        MODEL_URL,
                        temp_path,
                        progress_callback=lambda p: self.progress.emit(p),
                        timeout=15.0
                    )
                    checksum = calculate_sha256(temp_path)
                    logger.info(f"Downloaded model SHA256: {checksum}")

                    if checksum != MODEL_SHA256:
                        err_msg = f"Model Cryptographic Integrity Failure: Expected {MODEL_SHA256}, got {checksum}"
                        logger.critical(err_msg)
                        self.finished.emit(False, err_msg)
                        return

                    os.replace(temp_path, self.model_path)
                    logger.info("Model download and verification complete.")
                except Exception as e:
                    raise Exception(f"Network error downloading model: {e}")
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass

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
        self.model_dir: str = get_model_dir(model_dir)
        self.model_path: str = os.path.join(self.model_dir, "mobilenetv2.onnx")
        self.labels_path: str = os.path.join(self.model_dir, "labels.txt")
        self.session: Optional[ort.InferenceSession] = None
        self.labels: List[str] = []
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
                self.labels = [line.strip() for line in f.readlines()]
        except Exception as e:
            logger.error(f"Failed to load labels from {self.labels_path}: {e}")
            return

        prioritized_providers = get_prioritized_providers()
        top_provider = prioritized_providers[0] if prioritized_providers else "CPUExecutionProvider"
        providers = [top_provider, 'CPUExecutionProvider'] if top_provider != 'CPUExecutionProvider' else ['CPUExecutionProvider']

        physical_cores = psutil.cpu_count(logical=False) or 1
        intra_threads = max(1, min(physical_cores, 4))

        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = intra_threads
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.enable_cpu_mem_arena = True
        sess_options.enable_mem_pattern = True

        try:
            self.session = ort.InferenceSession(self.model_path, sess_options, providers=providers)
            self.active_provider = self.session.get_providers()[0] if self.session.get_providers() else top_provider
            logger.info(f"Loaded AI model from {self.model_path} with providers: {providers}")
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

    def preprocess(self, image_path: str) -> Optional[np.ndarray]:
        """Preprocesses an image tensor for MobileNetV2 inference."""
        Image.MAX_IMAGE_PIXELS = 50_000_000
        try:
            with Image.open(image_path) as img:
                img_rgb = img.convert('RGB')
                img_resized = img_rgb.resize((224, 224))
                arr = np.array(img_resized, dtype=np.float32)

            arr /= 255.0
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            arr -= mean
            arr /= std

            arr = np.transpose(arr, (2, 0, 1))
            tensor = np.ascontiguousarray(arr[np.newaxis, ...], dtype=np.float32)
            return tensor
        except Image.DecompressionBombError as e:
            logger.error(f"Decompression bomb detected in {image_path}: {e}")
            return None
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
            res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
            if len(res) == len(self.labels) + 1:
                # Ignore output index 0 (background class) when model has 1,001 outputs and 1,000 labels
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

            top_indices = np.argsort(probs)[-top_k:][::-1]
            tags = [self.labels[i] for i in top_indices if probs[i] > 0.1]
            return tags
        except Exception as e:
            logger.error(f"Error during AI inference for {image_path}: {e}")
            return []


def _sanitize_tags(tags: List[str]) -> List[str]:
    """Sanitizes metadata tags: strips control chars, restricts to printable chars, limits to 64 chars per tag and max 30 tags."""
    sanitized: List[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        cleaned = "".join(ch for ch in tag if ch.isprintable() and ch not in ("\r", "\n", "\x00"))
        cleaned = cleaned.strip()
        if cleaned:
            sanitized.append(cleaned[:64])
        if len(sanitized) >= 30:
            break
    return sanitized


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
    sanitized_tags = _sanitize_tags(tags)
    if not sanitized_tags:
        return

    # Write Sidecar (Atomic Write with Path Traversal Boundary Check)
    if write_sidecar:
        real_image_path = os.path.realpath(filepath)
        parent_dir = os.path.dirname(real_image_path)
        sidecar_path = os.path.join(parent_dir, os.path.basename(real_image_path) + ".txt")
        real_sidecar = os.path.realpath(sidecar_path)

        if os.path.commonpath([parent_dir, real_sidecar]) != parent_dir:
            err_msg = f"Path traversal detected: {sidecar_path} escapes {parent_dir}"
            logger.error(err_msg)
            raise ValueError(err_msg)

        temp_path: Optional[str] = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=parent_dir, prefix="sidecar_", suffix=".tmp")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(", ".join(sanitized_tags))
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
            tag_string = ";".join(sanitized_tags)
            xp_keywords = (tag_string + "\x00").encode('utf-16le')

            exif_dict = None
            try:
                exif_dict = piexif.load(filepath)
                if "0th" not in exif_dict:
                    exif_dict["0th"] = {}
                exif_dict["0th"][piexif.ImageIFD.XPKeywords] = xp_keywords
                exif_bytes = piexif.dump(exif_dict)
            except Exception as load_or_dump_err:
                logger.warning(
                    f"Malformed camera EXIF header in {filepath} ({load_or_dump_err}); "
                    "falling back to pristine 0th IFD."
                )
                pristine_exif = {
                    "0th": {
                        piexif.ImageIFD.XPKeywords: xp_keywords
                    },
                    "Exif": {},
                    "GPS": {},
                    "Interop": {},
                    "1st": {},
                    "thumbnail": None
                }
                exif_bytes = piexif.dump(pristine_exif)

            if len(exif_bytes) > 32768:
                logger.error(f"EXIF payload ({len(exif_bytes)} bytes) exceeds 32KB limit for {filepath}")
                return

            dir_name = os.path.dirname(os.path.realpath(filepath)) or "."
            fd, temp_img_path = tempfile.mkstemp(dir=dir_name, prefix="exif_", suffix=".tmp")
            os.close(fd)

            # Copy original image to temp file
            with open(filepath, 'rb') as src, open(temp_img_path, 'wb') as dst:
                dst.write(src.read())

            piexif.insert(exif_bytes, temp_img_path)

            # Preserve POSIX permissions and mtime before atomic swap
            try:
                shutil.copystat(filepath, temp_img_path)
            except OSError as cs_err:
                logger.warning(f"Could not copy file stat for {filepath}: {cs_err}")

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
