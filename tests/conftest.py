"""Tests isolate profile state and honor asynchronous close before fixture cleanup."""
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_test_xdg(tmp_path, monkeypatch):
    for category in ("CONFIG", "DATA", "CACHE", "STATE"):
        directory = tmp_path / "xdg" / category.lower()
        directory.mkdir(parents=True)
        monkeypatch.setenv(f"XDG_{category}_HOME", str(directory))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    qtbot = item.funcargs.get("qtbot")
    if qtbot is None:
        return
    from PyQt6.QtWidgets import QApplication
    module = sys.modules.get("imagesorter.ui_main")
    if module is not None:
        viewers = [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, module.MainViewer)]
        for viewer in viewers:
            viewer.close()
        for viewer in viewers:
            qtbot.waitUntil(lambda window=viewer: not window.loader.isRunning() and not window.worker.readers_running(), timeout=5000)
    clients_module = sys.modules.get("imagesorter.mutation_client")
    if clients_module is not None:
        clients = list(clients_module._clients)
        for client in clients:
            client.disconnect()
        for client in clients:
            if client.process is not None:
                client.process.wait(timeout=10)
