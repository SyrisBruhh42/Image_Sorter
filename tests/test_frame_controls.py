"""Frame selection is explicit, stale-safe and independent of viewport state."""
import time
import uuid
from dataclasses import replace

import pytest
from PIL import Image
from PyQt6.QtGui import QImage, QPixmap

from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer


def test_frame_controls_paused_seek_stale_viewport_and_reload(qtbot, tmp_path, monkeypatch):
    source = tmp_path / "animation.png"
    Image.new("RGB", (120, 80), "red").save(source)
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[str(source)])
    qtbot.addWidget(viewer)
    qtbot.waitUntil(lambda: not viewer.viewer.original_pixmap.isNull(), timeout=3000)
    viewer._frame_metadata[viewer._cache_key(str(source))] = {"frame_count": 3, "loop_count": 1}
    viewer.show_image()
    assert viewer._frame_count == 3
    assert not viewer._frame_playing
    assert viewer.frame_play.text() == "Play"
    requests = []
    def request(path, **options):
        request_id = uuid.uuid4().hex
        requests.append((request_id, path, options))
        return request_id
    monkeypatch.setattr(viewer.loader, "add_task", request)
    viewer.viewer.scale(1.4, 1.4)
    expected_transform = viewer.viewer.transform()
    viewer._seek_frame(1)
    first = viewer._frame_request
    viewer._seek_frame(2)
    second = viewer._frame_request
    frame = QImage(120, 80, QImage.Format.Format_RGBA8888)
    frame.fill(0xff00ff00)
    def reply(request_id):
        context = viewer._decode_contexts[request_id]
        return {"request_id": request_id, "generation": viewer.load_generation,
                "filepath": str(source), "image": frame, "error": None,
                "metadata": {"duration_ms": 75, "frame": context.frame,
                             "source_identity": context.source, "decoder_identity": context.decoder}}
    viewer.on_image_ready(reply(first))
    assert viewer._frame_index == 0
    viewer.on_image_ready(reply(second))
    assert viewer._frame_index == 2
    assert viewer.viewer.transform() == expected_transform
    assert viewer.frame_seek.value() == 3
    assert requests[-1][2]["frame"] == 2
    viewer._frame_repeats = 1
    viewer._set_frame_playing(True)
    viewer._advance_frame()
    assert not viewer._frame_playing
    viewer.clear_pixmap_cache()
    assert viewer._frame_path is None and viewer._frame_pixmap is None


def test_apng_is_discoverable():
    from imagesorter.launch_requests import is_supported_image
    assert is_supported_image("fixture.apng")


def test_ready_frames_obey_timing_and_buffering_does_not_advance(qtbot, tmp_path, monkeypatch):
    source = tmp_path / "animation.bmp"
    Image.new("RGB", (120, 80), "red").save(source)
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[str(source)])
    qtbot.addWidget(viewer)
    qtbot.waitUntil(lambda: viewer._get_pixmap_from_cache(str(source)) is not None, timeout=5000)
    base = viewer._cache_key(str(source))
    viewer._frame_metadata[base] = {"frame_count": 3, "loop_count": 0, "duration_ms": 80}
    viewer.show_image()
    batches = []
    monkeypatch.setattr(viewer.loader, "add_task", lambda _path, **options: batches.append(options) or "batch")
    viewer._set_frame_playing(True)
    assert viewer._frame_buffering and not viewer._frame_timer.isActive()
    qtbot.wait(120)
    assert viewer._frame_index == 0
    assert batches[0]["frames"] == (1, 2)
    image = QImage(120, 80, QImage.Format.Format_RGBA8888)
    image.fill(0xff00ff00)
    frames = [{"frame": frame, "frame_count": 3, "duration_ms": 80, "loop_count": 0, "image": image,
               "source_identity": base.source, "decoder_identity": base.decoder} for frame in (1, 2)]
    viewer.viewer.scale(1.3, 1.3)
    transform = viewer.viewer.transform()
    started = time.monotonic()
    viewer.on_image_ready({"request_id": "batch", "generation": viewer.load_generation, "filepath": str(source),
                           "image": None, "error": None, "metadata": {"frames": frames}})
    assert not viewer._frame_buffering and viewer._frame_timer.isActive()
    qtbot.waitUntil(lambda: viewer._frame_index == 1, timeout=400)
    assert 0.06 <= time.monotonic() - started < 0.2
    assert viewer.viewer.transform() == transform
    assert len(batches) == 1
    viewer._set_frame_playing(False)
    assert not viewer._frame_timer.isActive()
    assert replace(base, frame=2) in viewer.pixmap_cache
    assert isinstance(viewer.pixmap_cache[replace(base, frame=2)], QPixmap)


def test_over_budget_page_pauses_without_repeated_batches(qtbot, tmp_path, monkeypatch):
    source = tmp_path / "pages.bmp"
    Image.new("RGB", (10, 10), "red").save(source)
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[str(source)])
    qtbot.addWidget(viewer)
    qtbot.waitUntil(lambda: viewer._get_pixmap_from_cache(str(source)) is not None, timeout=5000)
    base = viewer._cache_key(str(source))
    viewer._frame_metadata[base] = {"frame_count": 2, "loop_count": 1, "duration_ms": 80}
    viewer.show_image()
    viewer.max_cache_bytes = 1000
    batches = []
    monkeypatch.setattr(viewer.loader, "add_task", lambda _path, **options: batches.append(options) or "large-page")
    viewer._set_frame_playing(True)
    image = QImage(100, 100, QImage.Format.Format_RGBA8888)
    viewer.on_image_ready({"request_id": "large-page", "generation": viewer.load_generation,
                           "filepath": str(source), "error": None,
                           "metadata": {"frames": [{"frame": 1, "image": image,
                               "source_identity": base.source, "decoder_identity": base.decoder}]}})
    assert not viewer._frame_playing and not viewer._frame_timer.isActive()
    assert len(batches) == 1
    assert "cache budget" in viewer.statusBar().currentMessage()
    assert viewer.cache_bytes <= viewer.max_cache_bytes


def test_loop_contract_survives_first_frame_cache_eviction(qtbot, tmp_path):
    source = tmp_path / "loop.bmp"
    Image.new("RGB", (10, 10), "red").save(source)
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[str(source)])
    qtbot.addWidget(viewer)
    qtbot.waitUntil(lambda: viewer._get_pixmap_from_cache(str(source)) is not None, timeout=5000)
    base = viewer._cache_key(str(source))
    viewer._frame_metadata[base] = {"frame_count": 3, "loop_count": 1, "duration_ms": 80}
    viewer.show_image()
    viewer._frame_metadata.pop(base)
    viewer._frame_index, viewer._frame_repeats, viewer._frame_playing = 2, 1, True
    viewer._advance_frame()
    assert not viewer._frame_playing


@pytest.mark.parametrize("total_plays", [0, 1, 2, 3])
def test_normalized_play_count_is_not_raw_loop_repeat_count(qtbot, tmp_path, total_plays):
    source = tmp_path / "animation.bmp"
    Image.new("RGB", (10, 10), "red").save(source)
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[str(source)])
    qtbot.addWidget(viewer)
    qtbot.waitUntil(lambda: viewer._get_pixmap_from_cache(str(source)) is not None, timeout=5000)
    base = viewer._cache_key(str(source))
    for frame in (0, 1):
        key = replace(base, frame=frame)
        viewer._add_pixmap_to_cache(str(source), QPixmap(10, 10), key=key)
        viewer._frame_metadata[key] = {"frame_count": 2, "loop_count": 999,
                                       "total_plays": total_plays, "duration_ms": 5}
    viewer.show_image()
    viewer._set_frame_playing(True)
    if total_plays == 0:
        qtbot.waitUntil(lambda: viewer._frame_repeats >= 2, timeout=1000)
        assert viewer._frame_playing
        viewer._set_frame_playing(False)
    else:
        qtbot.waitUntil(lambda: not viewer._frame_playing, timeout=1000)
        assert viewer._frame_repeats == total_plays - 1
        assert viewer._frame_index == 1
