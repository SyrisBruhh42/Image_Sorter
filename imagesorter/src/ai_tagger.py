import os
import json
import urllib.request
import onnxruntime as ort
import numpy as np
from PIL import Image
import piexif
from PyQt6.QtCore import QThread, pyqtSignal

# Using a lightweight MobileNetV2 model for general classification
# Note: For a real release, you'd host this yourself or use a dedicated booru model.
MODEL_URL = "https://github.com/onnx/models/raw/main/vision/classification/mobilenet/model/mobilenetv2-7.onnx"
LABELS_URL = "https://raw.githubusercontent.com/pytorch/hub/master/imagenet_classes.txt"

class ModelDownloader(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, model_dir):
        super().__init__()
        self.model_dir = model_dir
        self.model_path = os.path.join(model_dir, "mobilenetv2.onnx")
        self.labels_path = os.path.join(model_dir, "labels.txt")

    def run(self):
        try:
            os.makedirs(self.model_dir, exist_ok=True)

            if not os.path.exists(self.labels_path):
                urllib.request.urlretrieve(LABELS_URL, self.labels_path)

            if not os.path.exists(self.model_path):
                # Download with basic progress
                def report(blocknum, blocksize, totalsize):
                    readsofar = blocknum * blocksize
                    if totalsize > 0:
                        percent = int(readsofar * 100 / totalsize)
                        self.progress.emit(min(percent, 100))

                urllib.request.urlretrieve(MODEL_URL, self.model_path, reporthook=report)

            self.finished.emit(True, "Model ready.")
        except Exception as e:
            self.finished.emit(False, str(e))

class AITagger:
    def __init__(self, model_dir="models"):
        self.model_path = os.path.join(model_dir, "mobilenetv2.onnx")
        self.labels_path = os.path.join(model_dir, "labels.txt")
        self.session = None
        self.labels = []
        self.load_model()

    def load_model(self):
        if os.path.exists(self.model_path) and os.path.exists(self.labels_path):
            try:
                # Basic CPU execution provider for maximum compatibility
                self.session = ort.InferenceSession(self.model_path, providers=['CPUExecutionProvider'])
                with open(self.labels_path, 'r') as f:
                    self.labels = [line.strip() for line in f.readlines()]
            except Exception as e:
                print(f"Error loading AI model: {e}")

    def preprocess(self, image_path):
        try:
            img = Image.open(image_path).convert('RGB')
            img = img.resize((224, 224))
            img_data = np.array(img).astype('float32')

            # Normalize for MobileNet
            img_data = img_data / 255.0
            mean = np.array([0.485, 0.456, 0.406])
            std = np.array([0.229, 0.224, 0.225])
            img_data = (img_data - mean) / std

            img_data = np.transpose(img_data, [2, 0, 1])
            img_data = np.expand_dims(img_data, axis=0)
            return img_data
        except Exception:
            return None

    def get_tags(self, image_path, top_k=3):
        if not self.session or not self.labels:
            return []

        input_data = self.preprocess(image_path)
        if input_data is None:
            return []

        input_name = self.session.get_inputs()[0].name
        raw_result = self.session.run(None, {input_name: input_data})

        # Softmax
        res = raw_result[0][0]
        exp_res = np.exp(res - np.max(res))
        probs = exp_res / exp_res.sum()

        top_indices = np.argsort(probs)[-top_k:][::-1]
        tags = [self.labels[i] for i in top_indices if probs[i] > 0.1]
        return tags

def write_metadata(filepath, tags, write_exif=True, write_sidecar=False):
    if not tags:
        return

    # Write Sidecar
    if write_sidecar:
        sidecar_path = filepath + ".txt"
        try:
            with open(sidecar_path, 'w', encoding='utf-8') as f:
                f.write(", ".join(tags))
        except Exception as e:
            print(f"Error writing sidecar: {e}")

    # Write EXIF (XPKeywords for Windows compatibility)
    if write_exif and filepath.lower().endswith(('.jpg', '.jpeg')):
        try:
            exif_dict = piexif.load(filepath)

            # 40094 is XPKeywords in EXIF IFD0. It requires UTF-16LE encoding.
            tag_string = ";".join(tags)
            xp_keywords = tag_string.encode('utf-16le')

            if "0th" not in exif_dict:
                exif_dict["0th"] = {}

            exif_dict["0th"][40094] = xp_keywords

            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, filepath)
        except Exception as e:
            print(f"Error writing EXIF: {e}")
