"""Unit and contract tests for launch request parsing and MainViewer launch contract v1."""

from __future__ import annotations

import os

from imagesorter.launch_requests import parse_launch_paths
from imagesorter.main import main


def test_parse_launch_paths_with_single_file(tmp_path):
    img_file = tmp_path / "test1.jpg"
    img_file.write_bytes(b"header")

    paths = parse_launch_paths([str(img_file)])
    assert len(paths) == 1
    assert os.path.samefile(paths[0], str(img_file))


def test_parse_launch_paths_ignores_flags_and_non_existent(tmp_path):
    img_file = tmp_path / "test1.png"
    img_file.write_bytes(b"header")

    paths = parse_launch_paths(["--help", "-v", str(tmp_path / "nonexistent.png"), str(img_file)])
    assert len(paths) == 1
    assert os.path.samefile(paths[0], str(img_file))


def test_parse_launch_paths_ignores_unsupported_extensions(tmp_path):
    txt_file = tmp_path / "doc.txt"
    txt_file.write_text("hello")

    paths = parse_launch_paths([str(txt_file)])
    assert len(paths) == 0


def test_parse_launch_paths_expands_directory_deterministically(tmp_path):
    img_b = tmp_path / "photo_b.png"
    img_a = tmp_path / "photo_a.jpg"
    sub_txt = tmp_path / "notes.txt"

    img_b.write_bytes(b"b")
    img_a.write_bytes(b"a")
    sub_txt.write_text("info")

    paths = parse_launch_paths([str(tmp_path)])
    assert len(paths) == 2
    assert os.path.basename(paths[0]) == "photo_a.jpg"
    assert os.path.basename(paths[1]) == "photo_b.png"


def test_parse_launch_paths_deduplicates_and_preserves_order(tmp_path):
    img_a = tmp_path / "a.webp"
    img_a.write_bytes(b"a")

    paths = parse_launch_paths([str(img_a), str(tmp_path), str(img_a)])
    assert len(paths) == 1
    assert os.path.samefile(paths[0], str(img_a))


def test_main_cli_routing_calls_viewer_with_initial_paths(monkeypatch, tmp_path):
    img1 = tmp_path / "launch.jpg"
    img1.write_bytes(b"test")

    captured_args = {}

    class MockMainViewer:
        def __init__(self, settings, initial_paths=None):
            captured_args["settings"] = settings
            captured_args["initial_paths"] = initial_paths

        def show(self):
            pass

    monkeypatch.setattr("imagesorter.main.MainViewer", MockMainViewer)
    monkeypatch.setattr("PyQt6.QtWidgets.QApplication.exec", lambda self: 0)

    res = main(["imagesorter", str(img1)])
    assert res == 0
    assert captured_args.get("initial_paths") is not None
    assert len(captured_args["initial_paths"]) == 1
    assert os.path.samefile(captured_args["initial_paths"][0], str(img1))
