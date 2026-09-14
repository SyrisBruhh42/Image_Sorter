"""Batching must preserve bounded transport and individual frame provenance."""
from __future__ import annotations

import copy
import hashlib
import sys

import pytest
from PIL import Image

from imagesorter import component_worker
from imagesorter.component_manager import ComponentError
from imagesorter.component_runtime import _run, _validate_batch, decode_frames_component


def animation(tmp_path):
    path = tmp_path / "source.gif"
    frames = [Image.new("RGBA", (12, 8), color) for color in ("red", "green", "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[80, 100, 120], loop=2)
    return path


def batch(tmp_path):
    path = animation(tmp_path)
    return component_worker.decode_frames({"input_path": str(path), "frames": [2, 0, 1]}, "core.cpu")


def test_batch_preserves_order_pixels_and_frame_timing_with_one_deadline(tmp_path, monkeypatch):
    deadlines = []
    monkeypatch.setattr(component_worker, "_arm_deadline", deadlines.append)
    metadata, payload = batch(tmp_path)
    _validate_batch(metadata, payload, [2, 0, 1])
    assert deadlines == [30]
    assert [item["duration_ms"] for item in metadata["frames"]] == [120, 80, 100]
    assert [item["loop_count"] for item in metadata["frames"]] == [2, 2, 2]
    assert [item["total_plays"] for item in metadata["frames"]] == [3, 3, 3]
    assert payload[:4] == bytes((0, 0, 255, 255))
    assert payload[12 * 8 * 4:12 * 8 * 4 + 4] == bytes((255, 0, 0, 255))


@pytest.mark.parametrize("indices", [[], [0, 0], [True], [-1], [100000], list(range(9)), "0"])
def test_batch_rejects_invalid_indices_before_any_input_access(indices):
    with pytest.raises(ValueError, match="unique frame"):
        component_worker.decode_frames({"frames": indices}, "core.cpu")
    with pytest.raises(ComponentError, match="unique frame"):
        decode_frames_component("never-opened", "viewer.animation-multipage", indices)


@pytest.mark.parametrize("field,value", [
    ("payload_offset", 1), ("payload_offset", False), ("payload_length", 0),
    ("payload_length", 9999999), ("frame", 0), ("frame_count", 2),
    ("duration_ms", -1), ("loop_count", True), ("total_plays", True), ("total_plays", -1), ("original_size", [0, 1]),
    ("payload_sha256", "0" * 64),
])
def test_batch_rejects_forged_entry_boundaries_and_metadata(tmp_path, field, value):
    metadata, payload = batch(tmp_path)
    metadata["frames"][0][field] = value
    with pytest.raises(ComponentError):
        _validate_batch(metadata, payload, [2, 0, 1])


def test_batch_rejects_missing_entries_trailing_bytes_and_aggregate_overflow(tmp_path, monkeypatch):
    metadata, payload = batch(tmp_path)
    missing = copy.deepcopy(metadata)
    missing["frames"].pop()
    for result, data in ((missing, payload), (metadata, payload + b"unaccounted")):
        with pytest.raises(ComponentError):
            _validate_batch(result, data, [2, 0, 1])
    monkeypatch.setattr(component_worker, "MAX_BATCH_BYTES", 12 * 8 * 4)
    with pytest.raises(ValueError, match="payload limit"):
        batch(tmp_path)


def test_real_process_batch_retains_outer_identity_and_source(tmp_path):
    source = animation(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata, payload = _run([sys.executable, "-m", "imagesorter.component_worker", "--component-id", "core.cpu"],
        {"action": "decode_frames", "input_path": str(source), "frames": [0, 1, 2],
         "component_id": "core.cpu", "component_version": "base", "generation": 41,
         "request_id": "batch-regression"}, cwd=tmp_path, timeout=30)
    assert metadata["request_id"] == "batch-regression"
    assert metadata["generation"] == 41
    assert metadata["component_version"] == "base"
    assert len(payload) == 3 * 12 * 8 * 4
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_tiff_batch_allows_different_page_dimensions(tmp_path):
    path = tmp_path / "pages.tiff"
    Image.new("RGB", (12, 8), "red").save(path, save_all=True,
        append_images=[Image.new("RGB", (7, 14), "blue")])
    metadata, payload = component_worker.decode_frames({"input_path": str(path), "frames": [0, 1]}, "core.cpu")
    _validate_batch(metadata, payload, [0, 1])
    assert [item["original_size"] for item in metadata["frames"]] == [[12, 8], [7, 14]]
    assert [item["loop_count"] for item in metadata["frames"]] == [None, None]
    assert [item["total_plays"] for item in metadata["frames"]] == [1, 1]


@pytest.mark.parametrize("extension,loop,expected", [
    ("gif", None, 1), ("gif", 0, 0), ("gif", 1, 2), ("gif", 2, 3),
    ("png", 0, 0), ("png", 1, 1), ("png", 2, 2),
    ("webp", 0, 0), ("webp", 1, 1), ("webp", 2, 2), ("tiff", None, 1),
])
def test_total_plays_uses_detected_container_not_filename(tmp_path, extension, loop, expected):
    source = tmp_path / ("animation." + extension)
    options = {} if loop is None else {"loop": loop}
    Image.new("RGBA", (8, 8), "red").save(source, save_all=True,
        append_images=[Image.new("RGBA", (8, 8), "blue")], duration=80, **options)
    disguised = tmp_path / "not-the-format.jpg"
    source.rename(disguised)
    metadata, payload = component_worker.decode_frames({"input_path": str(disguised), "frames": [0, 1]}, "core.cpu")
    _validate_batch(metadata, payload, [0, 1])
    assert [item["total_plays"] for item in metadata["frames"]] == [expected, expected]
