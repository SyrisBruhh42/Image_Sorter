"""Real process-boundary tests: immutable readers, replay, cancellation and close."""
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from imagesorter.bootstrap import configure_profile
from imagesorter.mutation_client import MutationClient
from imagesorter.operation_journal import OperationJournal
from imagesorter.reader_job import _snapshot
from imagesorter.reader_process import ReaderCancelled, read_pixel_output, run_reader
from imagesorter.worker_protocol import worker_command, worker_environment


@pytest.fixture
def isolated_profile(tmp_path, monkeypatch):
    for category in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"XDG_{category}_HOME", str(tmp_path / category.lower()))
    monkeypatch.setenv("IMAGESORTER_PROFILE_ROOT", str(tmp_path / "profile"))
    return configure_profile(str(tmp_path / "profile"))


def test_stalled_read_process_is_bounded_and_reaped(isolated_profile):
    started = time.monotonic()
    with pytest.raises(ReaderCancelled, match="deadline"):
        run_reader({"action": "decode"}, timeout=0.15,
                   command=[sys.executable, "-c", "import time; time.sleep(30)"])
    assert time.monotonic() - started < 3
    assert not list((isolated_profile / "cache" / "ImageSorter").glob("reader-*"))


@pytest.mark.parametrize("role", ["decode", "infer", "settings"])
def test_main_close_reaps_real_term_ignoring_read_workers(qtbot, isolated_profile, tmp_path, monkeypatch, role):
    from PyQt6.QtCore import QTimer

    from imagesorter import reader_process
    from imagesorter.settings_manager import SettingsManager
    from imagesorter.ui_main import MainViewer
    from imagesorter.ui_settings import SettingsWindow

    source, marker = tmp_path / "original.bmp", tmp_path / "reader-pid"
    Image.new("RGB", (20, 10), "red").save(source)
    original = source.read_bytes()
    script = ("import os,signal,sys,time; from pathlib import Path; "
              "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
              "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)")

    def install_stall():
        monkeypatch.setattr(reader_process, "worker_command", lambda _role: [sys.executable, "-c", script, str(marker)])

    if role == "decode":
        install_stall()
    viewer = MainViewer(SettingsManager(filepath=str(tmp_path / "settings.json")), initial_paths=[str(source)])
    qtbot.addWidget(viewer)
    viewer.show()
    if role != "decode":
        qtbot.waitUntil(lambda: not viewer.viewer.original_pixmap.isNull(), timeout=5000)
        install_stall()
        if role == "infer":
            viewer.worker._start_enrichment("isolated-stalled-provider", str(source), viewer.settings.snapshot())
        else:
            dialog = SettingsWindow(viewer.settings, parent=viewer)
            qtbot.addWidget(dialog)
            dialog.show()
    qtbot.waitUntil(marker.exists, timeout=5000)
    pid = int(marker.read_text())
    ticks = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    started = time.monotonic()
    viewer.close()
    assert time.monotonic() - started < 0.2
    qtbot.waitUntil(lambda: not viewer.isVisible(), timeout=5000)
    elapsed = time.monotonic() - started
    timer.stop()
    assert elapsed < 3, f"Read-only {role} reaping exceeded three seconds: {elapsed:.3f}s"
    assert len(ticks) >= 5, "The GUI event loop must continue while TERM-ignoring readers are reaped"
    assert not Path(f"/proc/{pid}").exists()
    assert source.read_bytes() == original


def test_native_reader_uses_immutable_snapshot(isolated_profile, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (17, 13), "red").save(source)
    before = source.read_bytes()
    result, pixels = run_reader({"action": "decode", "filepath": str(source), "request_id": "immutable"})
    assert result["request_id"] == "immutable"
    assert (result["width"], result["height"], len(pixels)) == (17, 13, 17 * 13 * 4)
    assert result["input_sha256"] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before


def test_snapshot_refuses_symlink_and_fifo(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"original")
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="symbolic"):
        _snapshot(str(link), tmp_path)
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(ValueError, match="regular"):
            _snapshot(str(fifo), tmp_path)


def test_output_collection_refuses_fifo_symlink_and_oversized_file(tmp_path):
    ordinary = tmp_path / "pixels"
    ordinary.write_bytes(b"1234")
    assert read_pixel_output(ordinary, 4) == b"1234"
    with pytest.raises(ValueError, match="bounded regular"):
        read_pixel_output(ordinary, 3)
    link = tmp_path / "symlink"
    link.symlink_to(ordinary)
    with pytest.raises(OSError):
        read_pixel_output(link, 4)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    started = time.monotonic()
    with pytest.raises(ValueError, match="regular"):
        read_pixel_output(fifo, 4)
    assert time.monotonic() - started < 0.2
    assert ordinary.read_bytes() == b"1234"


@pytest.mark.parametrize("stream", [1, 2])
@pytest.mark.parametrize("exits_immediately", [False, True])
def test_reader_output_flood_stops_before_wall_deadline(isolated_profile, stream, exits_immediately):
    script = ("import json,os,sys,time; os.write(int(sys.argv[1]), b'x' * (2*1024*1024)); "
              "print(json.dumps({'protocol_version':1,'request_id':'flood','valid':False}),flush=True); "
              f"time.sleep({0 if exits_immediately else 30})")
    started = time.monotonic()
    with pytest.raises(ValueError, match="channel budget"):
        run_reader({"action": "validate_model", "request_id": "flood"}, timeout=10,
                   command=[sys.executable, "-c", script, str(stream)])
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("field,value", [("source_identity", [1, True, 4, 1, 1]),
                                         ("decoder_identity", ["core.qt", None]), ("target_size", [1, 2]),
                                         ("total_plays", True), ("total_plays", -1), ("total_plays", None)])
def test_reader_cache_identity_reply_is_strict(field, value):
    from imagesorter.reader_process import validate_reply
    request = {"action": "decode", "request_id": "id"}
    reply = {"request_id": "id", "input_sha256": "a" * 64, "width": 1, "height": 1, "stride": 4,
             "frame": 0, "frame_count": 1, "duration_ms": 0, "loop_count": 0,
             "source_identity": [1, 2, 4, 1, 1], "decoder_identity": ["core.qt", "base"]}
    validate_reply(request, reply, b"0000")
    reply[field] = value
    with pytest.raises(ValueError):
        validate_reply(request, reply, b"0000")


@pytest.mark.parametrize("defect", ["offset", "source", "decoder", "frame", "trailing", "duplicate", "bool"])
def test_reader_frame_batch_rejects_malformed_or_mixed_identities(defect):
    from imagesorter.reader_process import validate_reply
    identity = {"source_identity": [1, 2, 4, 1, 1], "decoder_identity": ["viewer.animation-multipage", "v1"]}
    request = {"action": "decode_frames", "request_id": "batch", "frames": [0, 1]}
    reply = {**identity, "request_id": "batch", "input_sha256": "a" * 64,
             "frames": [{**identity, "frame": frame, "frame_count": 2, "duration_ms": 80,
                         "loop_count": 0, "offset": frame * 4, "byte_length": 4,
                         "width": 1, "height": 1, "stride": 4} for frame in (0, 1)]}
    data = b"00001111"
    validate_reply(request, reply, data)
    if defect == "offset":
        reply["frames"][1]["offset"] = 0
    elif defect == "source":
        reply["frames"][1]["source_identity"] = [1, 9, 4, 1, 1]
    elif defect == "decoder":
        reply["frames"][1]["decoder_identity"] = ["viewer.animation-multipage", "v2"]
    elif defect == "frame":
        reply["frames"][1]["frame"] = 0
    elif defect == "trailing":
        data += b"extra"
    elif defect == "duplicate":
        request["frames"] = [0, 0]
    elif defect == "bool":
        request["frames"] = [False, 1]
    with pytest.raises(ValueError):
        validate_reply(request, reply, data)


def test_bootstrap_profile_precedes_qt_import(tmp_path):
    script = "from imagesorter.bootstrap import configure_profile; import sys; configure_profile(sys.argv[1]); assert 'PyQt6' not in sys.modules; assert 'onnxruntime' not in sys.modules"
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "profile")], env=worker_environment(), check=True, timeout=5)


def test_nested_component_reply_cannot_override_reader_request_id(isolated_profile):
    script = ("from imagesorter import reader_job; "
              "reader_job.execute=lambda request: {'request_id':'nested-component-id','valid':True}; "
              "raise SystemExit(reader_job.main())")
    result, _data = run_reader({"action": "validate_model", "request_id": "outer-reader-id"},
                              command=[sys.executable, "-c", script])
    assert result["request_id"] == "outer-reader-id" and result["valid"] is True


def test_service_replays_accepted_interruption_and_stops(qtbot, isolated_profile, tmp_path):
    journal_path = str(isolated_profile / "data" / "ImageSorter" / "operation_journal.db")
    Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.jpg"
    source.write_bytes(b"untouched")
    request = {"operation_id": "accepted-before-crash", "action": "copy", "source_path": str(source),
               "destination_path": str(tmp_path / "destination"), "task_options": {}}
    journal = OperationJournal(journal_path)
    journal.accept(request)
    client = MutationClient(journal_path=journal_path)
    receipts = []
    client.result.connect(receipts.append)
    client.submit(request)
    try:
        qtbot.waitUntil(lambda: bool(receipts), timeout=10000)
        assert receipts[0]["state"] == "cancelled"
        assert source.read_bytes() == b"untouched"
        assert not (tmp_path / "destination").exists()
    finally:
        client.disconnect()
        if client.process:
            client.process.wait(timeout=5)
    assert OperationJournal(journal_path).replay_results()[0]["result"] == receipts[0]


def test_mutator_disconnect_does_not_kill_active_engine(qtbot, isolated_profile, tmp_path, monkeypatch):
    """A stalled pre-publication engine survives GUI disconnect then cancels safely."""
    import imagesorter.mutation_client as module
    real_command = worker_command
    marker = tmp_path / "entered"
    script = (
        "import sys,time; from pathlib import Path; "
        "from imagesorter.operation_engine import OperationEngine; "
        "original=OperationEngine.execute; "
        "exec('def delayed(self, request, **kwargs):\\n Path(sys.argv[1]).write_text(\"entered\")\\n time.sleep(1)\\n return original(self,request,**kwargs)'); "
        "OperationEngine.execute=delayed; from imagesorter.mutation_service import main; main(sys.argv[2:])"
    )
    monkeypatch.setattr(module, "worker_command", lambda role: [sys.executable, "-c", script, str(marker)] if role == "mutation-service" else real_command(role))
    journal_path = isolated_profile / "data" / "ImageSorter" / "operation_journal.db"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "original.jpg"
    source.write_bytes(b"safe original")
    destination = tmp_path / "destination"
    destination.mkdir()
    client = MutationClient(journal_path=journal_path)
    client.submit({"operation_id": "drain", "action": "move", "source_path": str(source),
                   "destination_path": str(destination), "task_options": {}})
    qtbot.waitUntil(marker.exists, timeout=5000)
    started = time.monotonic()
    client.disconnect()
    assert time.monotonic() - started < 0.2
    assert client.process.poll() is None
    client.process.wait(timeout=5)
    assert source.read_bytes() == b"safe original"
    assert not list(destination.iterdir())
    assert OperationJournal(str(journal_path)).replay_results()[0]["result"]["state"] == "cancelled"


def test_trace_parser_counts_process_attributed_attempts(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("native_acceptance", Path(__file__).parents[1] / "scripts" / "native_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trace = tmp_path / "trace.123"
    trace.write_text('socket(AF_UNIX, SOCK_STREAM, 0) = 3\nsocket(AF_INET, SOCK_STREAM, 0) = 4\nopenat(AT_FDCWD, "model.onnx", O_RDONLY) = 5\n')
    result = module.summarize_trace([trace])
    assert result["process_ids"] == [123]
    assert len(result["network_attempts"]) == len(result["model_open_attempts"]) == 1
    assert result["network_attempts"][0]["pid"] == 123
    assert not module.valid_origin("/repository/src/imagesorter/__init__.py", "/venv/bin/imagesorter", "wheel")


def test_pending_admission_is_bounded(qtbot, isolated_profile):
    client = MutationClient()
    client.MAX_PENDING = 2
    for index in range(2):
        client.submit({"operation_id": str(index), "action": "copy", "source_path": "/not-read"})
    with pytest.raises(OverflowError, match="backlog"):
        client.submit({"operation_id": "overflow", "action": "copy", "source_path": "/not-read"})
    assert len(client.pending) == 2
    client.disconnect()


def test_runtime_identity_binds_libraries_but_excludes_portable_profile(tmp_path):
    from imagesorter.mutation_service import runtime_inventory
    (tmp_path / "ImageSorter").write_bytes(b"executable")
    internal = tmp_path / "_internal"
    internal.mkdir()
    library = internal / "library.so"
    library.write_bytes(b"version one")
    profile = tmp_path / "data"
    profile.mkdir()
    (profile / "private-image.jpg").write_bytes(b"not part of runtime")
    before = runtime_inventory(tmp_path, "ImageSorter", internal)
    assert not any(name.startswith("data/") for name in before["files"])
    library.write_bytes(b"version two")
    assert runtime_inventory(tmp_path, "ImageSorter", internal) != before
    (internal / "escape").symlink_to(profile)
    with pytest.raises(ValueError, match="escapes"):
        runtime_inventory(tmp_path, "ImageSorter", internal)


def test_large_history_replays_without_disconnect_and_accepts_work(qtbot, isolated_profile, tmp_path):
    """History larger than the socket backlog limit must be paced, not dropped."""
    from imagesorter.operation_contracts import OperationResult

    journal_path = isolated_profile / "data" / "ImageSorter" / "operation_journal.db"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal = OperationJournal(str(journal_path))
    for index in range(80):
        request = {"operation_id": f"old-{index}", "action": "copy", "source_path": "/not-read",
                   "destination_path": str(tmp_path), "task_options": {}}
        journal.accept(request)
        journal.finish_result(OperationResult(operation_id=request["operation_id"], action="copy",
            source_path="/not-read", state="failed", warning="x" * 60000).to_dict())
    journal.close()
    source = tmp_path / "new.jpg"
    source.write_bytes(b"new content")
    destination = tmp_path / "destination"
    destination.mkdir()
    client = MutationClient(journal_path=journal_path)
    history, results, statuses = [], [], []
    client.recovery.connect(lambda value: history.append(value) if value["type"] == "history" else None)
    client.result.connect(results.append)
    client.status.connect(statuses.append)
    client.submit({"operation_id": "new-during-replay", "action": "copy", "source_path": str(source),
                   "destination_path": str(destination), "task_options": {}})
    try:
        qtbot.waitUntil(lambda: len(history) >= 80 and bool(results), timeout=15000)
        old_ids = [row["result"]["operation_id"] for row in history if row["result"]["operation_id"].startswith("old-")]
        assert len(old_ids) == len(set(old_ids)) == 80
        assert results[0]["state"] == "completed"
        assert (destination / "new.jpg").read_bytes() == source.read_bytes()
        assert not statuses
    finally:
        client.disconnect()
        if client.process:
            client.process.wait(timeout=5)
