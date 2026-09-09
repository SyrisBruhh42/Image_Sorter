"""Pinned MobileNetV2 image preprocessing shared by CPU and optional providers."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps


def preprocess_image(image_path: str) -> np.ndarray:
    with Image.open(image_path) as source:
        if source.width * source.height > 50_000_000:
            raise ValueError("AI input exceeds the 50 megapixel safety limit")
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("Invalid AI input dimensions")
        if width < height:
            size = (256, round(height * 256.0 / width))
        else:
            size = (round(width * 256.0 / height), 256)
        image = image.resize(size, Image.Resampling.BILINEAR)
        left, top = (size[0] - 224) // 2, (size[1] - 224) // 2
        pixels = np.array(image.crop((left, top, left + 224, top + 224)), dtype=np.float32)
    pixels /= 255.0
    pixels -= .5
    pixels /= .5
    return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None, ...], dtype=np.float32)
