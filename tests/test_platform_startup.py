from __future__ import annotations

import os
import sys

from imagesorter.main import configure_linux_platform, main


def test_help_argument_returns_zero(capsys):
    res = main(["imagesorter", "--help"])
    assert res == 0
    captured = capsys.readouterr()
    assert "Usage: imagesorter" in captured.out

    res_short = main(["imagesorter", "-h"])
    assert res_short == 0


def test_existing_qt_qpa_platform_preserved(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    configure_linux_platform()
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"

    monkeypatch.setenv("QT_QPA_PLATFORM", "")
    configure_linux_platform()
    assert os.environ.get("QT_QPA_PLATFORM") == ""


def test_linux_platform_selection_matrix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)

    # Both Wayland and X11
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    configure_linux_platform()
    assert os.environ.get("QT_QPA_PLATFORM") == "wayland;xcb"

    # Wayland only
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    configure_linux_platform()
    assert os.environ.get("QT_QPA_PLATFORM") == "wayland"

    # X11 only
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    configure_linux_platform()
    assert os.environ.get("QT_QPA_PLATFORM") == "xcb"

    # Neither
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    configure_linux_platform()
    assert "QT_QPA_PLATFORM" not in os.environ


def test_non_linux_platform_no_op(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    configure_linux_platform()
    assert "QT_QPA_PLATFORM" not in os.environ


def test_qt_init_failure_exit_and_stderr(capsys, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    class FailingApplication:
        @staticmethod
        def instance():
            return None

        def __init__(self, _args):
            raise RuntimeError("Cannot connect to server")

    monkeypatch.setattr("PyQt6.QtWidgets.QApplication", FailingApplication)
    exit_code = main(["imagesorter"])
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "Error: Qt application initialization failed" in captured.err
    assert "QT_QPA_PLATFORM=offscreen" in captured.err
    assert "DISPLAY or WAYLAND_DISPLAY" in captured.err


def test_desktop_file_name_and_icon_wiring(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    captured = {}

    class MockViewer:
        def __init__(self, settings, initial_paths=None):
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                captured["desktop_name"] = app.desktopFileName()
                captured["app_name"] = app.applicationName()
            captured["initial_paths"] = initial_paths

        def show(self):
            captured["show_called"] = True

    monkeypatch.setattr("imagesorter.main.MainViewer", MockViewer)
    monkeypatch.setattr("PyQt6.QtWidgets.QApplication.exec", lambda self: 0)

    exit_code = main(["imagesorter"])
    assert exit_code == 0
    assert captured.get("desktop_name") == "imagesorter"
    assert captured.get("app_name") == "Image Sorter"
    assert captured.get("show_called") is True
