"""Cache hits bind current bytes, decoder activation, frame and preview identity."""
import os

from PIL import Image
from PyQt6.QtGui import QImage, QPixmap

from imagesorter import image_identity
from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_main import MainViewer


def setup_viewer(qtbot, tmp_path):
    source = tmp_path / "source.bmp"
    Image.new("RGB", (40, 30), "red").save(source)
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[str(source)])
    qtbot.addWidget(viewer)
    qtbot.waitUntil(lambda: viewer._get_pixmap_from_cache(str(source)) is not None, timeout=5000)
    return viewer, source


def test_same_size_rewrite_with_restored_mtime_invalidates_cache(qtbot, tmp_path):
    viewer, source = setup_viewer(qtbot, tmp_path)
    before = source.stat()
    old = viewer._cache_key(str(source))
    Image.new("RGB", (40, 30), "blue").save(source)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    assert viewer._cache_key(str(source)) != old
    assert viewer._get_pixmap_from_cache(str(source)) is None


def test_component_activation_frame_and_preview_are_distinct(qtbot, tmp_path, monkeypatch):
    viewer, source = setup_viewer(qtbot, tmp_path)
    monkeypatch.setattr(image_identity, "decoder_identity", lambda _path: ("viewer.animation-multipage", "v1"))
    pixmap = QPixmap(10, 10)
    assert viewer._add_pixmap_to_cache(str(source), pixmap)
    key = viewer._cache_key(str(source))
    assert key != viewer._cache_key(str(source), frame=1)
    assert key != viewer._cache_key(str(source), target_size=(100, 100))
    monkeypatch.setattr(image_identity, "decoder_identity", lambda _path: ("viewer.animation-multipage", "v2"))
    assert viewer._get_pixmap_from_cache(str(source)) is None


def test_late_decoded_pixels_cannot_enter_cache_after_replacement(qtbot, tmp_path):
    viewer, source = setup_viewer(qtbot, tmp_path)
    key = viewer._cache_key(str(source))
    viewer.clear_pixmap_cache()
    viewer._decode_contexts["late"] = key
    viewer.pending_decoder_requests["late"] = (str(source), viewer.load_generation)
    replacement = tmp_path / "replacement.bmp"
    Image.new("RGB", (40, 30), "blue").save(replacement)
    os.replace(replacement, source)
    pixels = QImage(40, 30, QImage.Format.Format_RGBA8888)
    pixels.fill(0xffff0000)
    viewer.on_image_ready({"request_id": "late", "generation": viewer.load_generation, "filepath": str(source),
                           "image": pixels, "error": None, "metadata": {"frame": 0, "source_identity": key.source,
                           "decoder_identity": key.decoder}})
    assert not viewer.pixmap_cache and viewer.cache_bytes == 0
    assert "late" not in viewer.pending_decoder_requests


def test_rejected_preloads_do_not_accumulate_metadata(qtbot, tmp_path):
    viewer, _source = setup_viewer(qtbot, tmp_path)
    viewer.clear_pixmap_cache()
    viewer.max_cache_bytes = 1
    pixels = QImage(40, 30, QImage.Format.Format_RGBA8888)
    for index in range(30):
        path = tmp_path / f"preload-{index}.bmp"
        Image.new("RGB", (40, 30), "red").save(path)
        key = viewer._cache_key(str(path))
        request_id = f"preload-{index}"
        viewer._decode_contexts[request_id] = key
        viewer.pending_decoder_requests[request_id] = (str(path), viewer.load_generation)
        viewer.on_image_ready({"request_id": request_id, "generation": viewer.load_generation, "filepath": str(path),
                               "image": pixels, "error": None, "metadata": {"frame": 0, "source_identity": key.source,
                               "decoder_identity": key.decoder}})
    assert not viewer.pixmap_cache and not viewer._frame_metadata
    assert viewer._uncached_key is None and viewer.cache_bytes == 0


def test_decoder_catalogue_reuse_still_observes_live_activation(tmp_path, monkeypatch):
    from imagesorter import component_manager, paths
    constructed, active = [], [tmp_path / "version-one"]

    class Manager:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        def active_path(self, _component_id):
            return active[0]

    monkeypatch.setattr(component_manager, "ComponentManager", Manager)
    monkeypatch.setattr(paths, "get_components_dir", lambda: tmp_path)
    image_identity._identity_manager.cache_clear()
    try:
        assert image_identity.decoder_identity("image.apng") == ("viewer.animation-multipage", "version-one")
        active[0] = tmp_path / "version-two"
        assert image_identity.decoder_identity("image.apng") == ("viewer.animation-multipage", "version-two")
        assert len(constructed) == 1
        active[0] = None
        assert image_identity.decoder_identity("image.apng") == ("core.qt", "base")
    finally:
        image_identity._identity_manager.cache_clear()
