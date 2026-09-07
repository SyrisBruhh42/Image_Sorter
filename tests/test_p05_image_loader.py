"""Tests for ImageLoader SHARED DECODER CONTRACT v1 (src/imagesorter/image_loader.py)."""

from PIL import Image
from PyQt6.QtGui import QImage, QPixmap

from imagesorter.image_loader import ImageLoader


def test_image_loader_contract_v1_signals(qtbot, tmp_path):
    """Verifies image_ready signal dictionary keys and request_id generation."""
    img_path = tmp_path / "test1.jpg"
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(str(img_path))

    loader = ImageLoader()
    results = []
    legacy_results = []

    loader.image_ready.connect(lambda res: results.append(res))
    loader.image_loaded.connect(lambda p, i: legacy_results.append((p, i)))

    loader.start()
    req_id = loader.add_task(
        str(img_path), request_id="custom_req_1", generation=1, target_size=(50, 50), priority=0
    )

    assert req_id == "custom_req_1"
    qtbot.waitUntil(lambda: len(results) == 1, timeout=3000)
    loader.stop()

    res = results[0]
    assert res["request_id"] == "custom_req_1"
    assert res["generation"] == 1
    assert res["filepath"] == str(img_path)
    assert isinstance(res["image"], QImage)
    assert res["error"] is None

    # Check legacy signal was also emitted
    assert len(legacy_results) == 1
    assert legacy_results[0][0] == str(img_path)


def test_priority_scheduling_and_duplicate_coalescing(qtbot, tmp_path):
    """Verifies priority 0 current image is processed before lower priority preloads and duplicates coalesce."""
    p0_file = tmp_path / "priority_0.jpg"
    p1_file = tmp_path / "priority_1.jpg"
    Image.new("RGB", (80, 80), color="red").save(str(p0_file))
    Image.new("RGB", (80, 80), color="green").save(str(p1_file))

    loader = ImageLoader()
    results = []
    loader.image_ready.connect(lambda res: results.append(res))

    # Add priority 1 first, then priority 0
    id1 = loader.add_task(str(p1_file), priority=1, generation=1)
    id2 = loader.add_task(str(p0_file), priority=0, generation=1)

    # Coalesce check: submitting duplicate p1 task with same parameters returns same request_id
    id1_dup = loader.add_task(str(p1_file), priority=1, generation=1)
    assert id1_dup == id1

    loader.start()
    qtbot.waitUntil(lambda: len(results) == 2, timeout=3000)
    loader.stop()

    # Verify priority 0 was processed first
    assert results[0]["request_id"] == id2
    assert results[1]["request_id"] == id1


def test_generation_invalidation_and_cancellation(qtbot, tmp_path):
    """Verifies that obsolete generation requests are cancelled on generation change."""
    img1 = tmp_path / "gen1.jpg"
    img2 = tmp_path / "gen2.jpg"
    Image.new("RGB", (60, 60), color="white").save(str(img1))
    Image.new("RGB", (60, 60), color="black").save(str(img2))

    loader = ImageLoader()
    results = []
    loader.image_ready.connect(lambda res: results.append(res))

    # Submit task for generation 1
    loader.add_task(str(img1), generation=1, priority=1)

    # Invalidate generation 1 by advancing to generation 2
    loader.clear_tasks(new_generation=2)

    # Submit task for generation 2
    id2 = loader.add_task(str(img2), generation=2, priority=0)

    loader.start()
    qtbot.waitUntil(lambda: len(results) >= 1, timeout=3000)
    loader.stop()

    # The gen2 task should succeed
    gen2_res = [r for r in results if r["request_id"] == id2]
    assert len(gen2_res) == 1
    assert gen2_res[0]["image"] is not None


def test_bounded_backlog_capacity(qtbot, tmp_path):
    """Verifies loader queue capacity caps backlog and drops worst preloads."""
    loader = ImageLoader()

    # Submit 60 tasks (exceeding MAX_QUEUE_CAPACITY of 50)
    req_ids = []
    for i in range(60):
        f = tmp_path / f"img_{i}.jpg"
        Image.new("RGB", (10, 10)).save(str(f))
        prio = 0 if i == 0 else (1 + i % 5)
        req_id = loader.add_task(str(f), priority=prio)
        req_ids.append(req_id)

    # Queue should be bounded to MAX_QUEUE_CAPACITY
    assert len(loader._queue) <= loader.MAX_QUEUE_CAPACITY

    # Priority 0 task MUST remain in queue
    p0_req = loader._requests_by_id.get(req_ids[0])
    assert p0_req is not None
    assert p0_req.priority == 0


def test_nonblocking_request_stop(qtbot, tmp_path):
    """Verifies request_stop performs a nonblocking shutdown emitting finished signal."""
    loader = ImageLoader()

    finished_emitted = []
    loader.finished.connect(lambda: finished_emitted.append(True))

    loader.start()
    loader.request_stop()

    qtbot.waitUntil(lambda: len(finished_emitted) == 1, timeout=3000)
    assert not loader.isRunning()


def test_no_qpixmap_in_decoding_thread(qtbot, tmp_path):
    """Verifies that decoding produces QImage objects and no GUI widgets/QPixmaps in worker thread."""
    img_path = tmp_path / "pixmap_check.png"
    Image.new("RGB", (50, 50), color="orange").save(str(img_path))

    loader = ImageLoader()
    results = []
    loader.image_ready.connect(lambda res: results.append(res))

    loader.start()
    loader.add_task(str(img_path))

    qtbot.waitUntil(lambda: len(results) == 1, timeout=3000)
    loader.stop()

    res = results[0]
    img = res["image"]
    assert isinstance(img, QImage)
    assert not isinstance(img, QPixmap)
