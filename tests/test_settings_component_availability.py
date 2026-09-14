"""Source-first settings and component availability preserve verified local use."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from imagesorter.component_manager import (
    DOWNLOAD_UNAVAILABLE,
    ComponentError,
    ComponentManager,
)
from imagesorter.settings_manager import SettingsManager
from imagesorter.ui_components import ComponentsPanel
from imagesorter.ui_settings import SettingsWindow
from tests.test_component_manager import CID, manager, pack


@pytest.fixture
def settings_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("imagesorter.reader_process.run_reader", lambda *_a, **_kw: ({"valid": False}, b""))
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    window = SettingsWindow(settings)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.check_worker is None, timeout=3000)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_kw: None)
    return settings, window


@pytest.mark.parametrize("threshold", [0.0, 0.5, 0.875, 1.0])
def test_confidence_control_persists_in_existing_setting(settings_window, threshold):
    settings, window = settings_window
    assert window.confidence_spin.value() == 0.5
    assert window.confidence_spin.minimum() == 0.0
    assert window.confidence_spin.maximum() == 1.0
    window.confidence_spin.setValue(threshold)
    window.chk_show_tags.setChecked(False)
    window.save_settings()
    reloaded = SettingsManager(filepath=settings.filepath)
    assert reloaded.get("ai_tagger", "threshold") == threshold
    assert reloaded.get("ui", "show_tags") is False
    assert window.chk_show_tags.text() == "Show AI status in image details"


def test_saved_threshold_and_status_load_without_falsey_zero(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("imagesorter.reader_process.run_reader", lambda *_a, **_kw: ({"valid": False}, b""))
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    settings.apply_changes({"ai_tagger": {"threshold": 0.0}, "ui": {"show_tags": False}})
    window = SettingsWindow(settings)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.check_worker is None, timeout=3000)
    assert window.confidence_spin.value() == 0.0
    assert window.chk_show_tags.isChecked() is False


def test_settings_tooltip_events_obey_toggle_including_components(settings_window, monkeypatch):
    _, window = settings_window

    class TooltipWitness(QWidget):
        received = 0

        def event(self, event):
            if event.type() == QEvent.Type.ToolTip:
                self.received += 1
                return True
            return super().event(event)

    for parent in (window.tab_general, window.components_panel.table.viewport()):
        widget = TooltipWitness(parent)
        monkeypatch.setattr(QApplication, "keyboardModifiers", lambda: Qt.KeyboardModifier.NoModifier)
        window.chk_tooltips.setChecked(False)
        QApplication.sendEvent(widget, QEvent(QEvent.Type.ToolTip))
        assert widget.received == 0
        monkeypatch.setattr(QApplication, "keyboardModifiers", lambda: Qt.KeyboardModifier.AltModifier)
        QApplication.sendEvent(widget, QEvent(QEvent.Type.ToolTip))
        assert widget.received == 1
        monkeypatch.setattr(QApplication, "keyboardModifiers", lambda: Qt.KeyboardModifier.NoModifier)
        window.chk_tooltips.setChecked(True)
        QApplication.sendEvent(widget, QEvent(QEvent.Type.ToolTip))
        assert widget.received == 2


def test_model_management_ignores_legacy_arbitrary_path(settings_window, monkeypatch):
    settings, window = settings_window
    settings.set("ai_tagger", "model_path", "/arbitrary/untrusted-model.onnx")
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: pytest.fail("Unexpected network"))
    assert window.btn_download_model.text() == "Manage AI Model…"
    window.btn_download_model.click()
    assert window.tabs.currentWidget() is window.components_panel
    assert window.components_panel.component.currentText() == CID
    assert window.components_panel.process is None
    assert window.components_panel.download_label.text() == DOWNLOAD_UNAVAILABLE
    assert "Custom ONNX model paths are not supported" in window.btn_download_model.toolTip()
    window.save_settings()
    assert settings.get("ai_tagger", "model_path") == "/arbitrary/untrusted-model.onnx"


def test_unpublished_catalog_keeps_all_local_component_identities():
    catalog_path = Path(__file__).parents[1] / "src/imagesorter/resources/component_catalog.json"
    catalog = json.loads(catalog_path.read_text())
    expected = {
        "ai.mobilenet-v2": "53f4f7e75d1bd41ab0f6c20654c117fb1afa66e1a69655a840cf8261f925c008",
        "codec.camera-raw": "5399844ca83c76c7ae6d273578edd5d61a03730bcc3a557ccc4f713e05383e43",
        "codec.heif-avif": "5c5ff1b6b3a1b680a24f22f7f1556530c7e8310050c527e2de70ce78bf195706",
        "provider.onnx-nvidia": "a3ed7113c510962e43b1f6b4acd6cedc33bbf9d32791955343be4c7dd4b459f2",
        "viewer.animation-multipage": "997d645822920d20b1a552a91eb3b683ca08c9ee8dd26c6ca5720ccf448d9e6e",
    }
    assert {row["id"]: row["sha256"] for row in catalog["components"]} == expected
    assert all(row["url"] is None and row["version"] == "1.0.0+20260909.r10" for row in catalog["components"])
    for row in catalog["components"]:
        ComponentManager._validate_descriptor(row)
    assert catalog["publication_status"].startswith("local-qualification-only")


def test_null_download_url_retains_install_verify_upgrade_rollback_remove(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: pytest.fail("Unexpected network"))
    old_archive, old = pack(tmp_path, "1")
    new_archive, new = pack(tmp_path, "2", payload=b"new verified content")
    old["url"] = new["url"] = None
    store = manager(tmp_path, [new, old])
    row = next(row for row in store.status() if row["id"] == CID)
    assert row["available"] is True and row["download_available"] is False
    with pytest.raises(ComponentError, match=DOWNLOAD_UNAVAILABLE):
        store.install(CID)
    assert store.pending_jobs() == []
    assert not list(store.jobs_dir.glob("*.json"))
    # Import a previously installed version, then adopt the newer local descriptor.
    old_store = manager(tmp_path, [old])
    original = old_store.install(CID, old_archive)
    store = manager(tmp_path, [new, old])
    store.install(CID, new_archive)
    store.verify(CID)
    store.enable(CID, False)
    assert store.active_path(CID) is None
    store.enable(CID)
    store.rollback(CID)
    assert store.active_path(CID) == original
    store.verify(CID)
    store.remove(CID)
    assert store.active_path(CID) is None


def test_components_disable_only_unavailable_download_action(qtbot, tmp_path, monkeypatch):
    archive, descriptor = pack(tmp_path)
    descriptor["url"] = None
    store = manager(tmp_path, [descriptor])
    monkeypatch.setattr("imagesorter.ui_components.ComponentManager", lambda: store)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: pytest.fail("Unexpected network"))
    panel = ComponentsPanel()
    qtbot.addWidget(panel)
    panel.component.setCurrentText(CID)
    assert panel.download_label.text() == DOWNLOAD_UNAVAILABLE
    assert not panel.run_button.isEnabled()
    assert panel.import_button.isEnabled() and panel.legacy_button.isEnabled()
    monkeypatch.setattr(panel, "start", lambda arguments: pytest.fail("Unavailable download was started"))
    panel.apply()  # The direct slot is also guarded, independently of the button.
    assert panel.process is None
    for action in ("enable", "disable", "verify", "rollback", "remove", "recover"):
        panel.action.setCurrentIndex(panel.action.findData(action))
        assert panel.run_button.isEnabled()
    requests = []
    monkeypatch.setattr(panel, "start", requests.append)
    monkeypatch.setattr("imagesorter.ui_components.QFileDialog.getOpenFileName", lambda *_a, **_kw: (str(archive), ""))
    panel.import_pack()
    assert requests == [["install", CID, "--archive", str(archive)]]
    panel.action.setCurrentIndex(panel.action.findData("install"))
    panel._finished(0, None)
    assert not panel.run_button.isEnabled()
    assert panel.status_label.text() == "Component operation completed and verified."


def test_published_descriptor_exposes_download_without_fetching(qtbot, tmp_path, monkeypatch):
    _, descriptor = pack(tmp_path)
    descriptor["url"] = "https://example.test/qualified-pack.tar.gz"
    store = manager(tmp_path, [descriptor])
    monkeypatch.setattr("imagesorter.ui_components.ComponentManager", lambda: store)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: pytest.fail("Unexpected network"))
    panel = ComponentsPanel()
    qtbot.addWidget(panel)
    panel.component.setCurrentText(CID)
    assert panel.run_button.isEnabled()
    assert panel.component_status[CID]["download_available"] is True
    assert panel.download_label.text() == "Verified download available."


def test_successful_component_change_rechecks_model_without_reopening(settings_window, qtbot, monkeypatch):
    _, window = settings_window
    assert not window.chk_ai_enable.isEnabled()
    monkeypatch.setattr("imagesorter.reader_process.run_reader", lambda *_a, **_kw: ({"valid": True}, b""))
    window.components_panel._finished(0, None)
    qtbot.waitUntil(lambda: window.check_worker is None, timeout=3000)
    assert window.chk_ai_enable.isEnabled()
    assert window.btn_download_model.isEnabled()


def test_main_viewer_delegates_reopened_settings_tooltip_toggle(qtbot, tmp_path, monkeypatch):
    from imagesorter.ui_main import MainViewer

    monkeypatch.setattr("imagesorter.reader_process.run_reader", lambda *_a, **_kw: ({"valid": False}, b""))
    monkeypatch.setattr(QApplication, "keyboardModifiers", lambda: Qt.KeyboardModifier.NoModifier)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_kw: None)
    settings = SettingsManager(filepath=str(tmp_path / "settings.json"))
    viewer = MainViewer(settings)
    qtbot.addWidget(viewer)
    first = SettingsWindow(settings, viewer)
    qtbot.addWidget(first)
    qtbot.waitUntil(lambda: first.check_worker is None, timeout=3000)
    first.chk_tooltips.setChecked(False)
    first.save_settings()
    assert settings.get("ui", "tooltips_enabled") is False
    reopened = SettingsWindow(settings, viewer)
    qtbot.addWidget(reopened)
    qtbot.waitUntil(lambda: reopened.check_worker is None, timeout=3000)

    class TooltipWitness(QWidget):
        received = 0

        def event(self, event):
            if event.type() == QEvent.Type.ToolTip:
                self.received += 1
                return True
            return super().event(event)

    witness = TooltipWitness(reopened.tab_general)
    QApplication.sendEvent(witness, QEvent(QEvent.Type.ToolTip))
    assert witness.received == 0
    reopened.chk_tooltips.setChecked(True)
    QApplication.sendEvent(witness, QEvent(QEvent.Type.ToolTip))
    assert witness.received == 1
    assert settings.get("ui", "tooltips_enabled") is False  # Dialog preview has not saved yet.


def test_retired_url_preserves_previously_installed_packs_without_rewriting(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: pytest.fail("Unexpected network"))
    archive1, one = pack(tmp_path, "1")
    archive2, two = pack(tmp_path, "2", payload=b"new content")
    one["url"] = "https://example.test/previous-1.tar.gz"
    two["url"] = "https://example.test/previous-2.tar.gz"
    original_store = manager(tmp_path, [one])
    first = original_store.install(CID, archive1)
    original_store = manager(tmp_path, [two, one])
    second = original_store.install(CID, archive2)
    manifests = {path: (path / ".installed.json").read_bytes() for path in (first, second)}
    retired = manager(tmp_path, [{**two, "url": None}, {**one, "url": None}])
    retired.verify(CID)
    with retired.acquire(CID) as (path, descriptor):
        assert path == second and descriptor["url"] is None
    with retired.acquire_path(CID, second) as (path, descriptor):
        assert path == second and descriptor["sha256"] == two["sha256"]
    retired.enable(CID, False)
    assert retired.active_path(CID) is None
    retired.enable(CID)
    assert retired.install(CID, archive2) == second  # Same pack can still be imported.
    retired.rollback(CID)
    assert retired.active_path(CID) == first
    retired.verify(CID)
    assert all((path / ".installed.json").read_bytes() == data for path, data in manifests.items())
    retired.remove(CID)
    assert not first.exists() and not second.exists()


@pytest.mark.parametrize("field, replacement", [
    ("sha256", "0" * 64),
    ("abi", "altered ABI"),
    ("provenance", "different-provenance.json"),
    ("entrypoint", "data.bin"),
    ("reader_policy_version", 1),
    ("unexpected_identity_field", "untrusted"),
])
def test_retired_url_exception_does_not_authorize_other_manifest_changes(tmp_path, field, replacement):
    archive, descriptor = pack(tmp_path)
    descriptor["url"] = "https://example.test/previous.tar.gz"
    original = manager(tmp_path, [descriptor])
    installed = original.install(CID, archive)
    retired = manager(tmp_path, [{**descriptor, "url": None}])
    manifest = installed / ".installed.json"
    altered = json.loads(manifest.read_text())
    altered[field] = replacement
    manifest.write_text(json.dumps(altered))
    with pytest.raises(ComponentError):
        retired.verify(CID)
    assert (installed / "data.bin").read_bytes() == b"verified bytes"


@pytest.mark.parametrize("threshold", [0.0, 0.875, 1.0])
def test_saved_confidence_reaches_enrichment_request(settings_window, tmp_path, monkeypatch, threshold):
    from imagesorter.queue_worker import EnrichmentReader

    settings, window = settings_window
    window.confidence_spin.setValue(threshold)
    window.save_settings()
    reloaded = SettingsManager(filepath=settings.filepath)
    requests = []

    def reader_boundary(request, **_kwargs):
        requests.append(request)
        return {"tags": [], "provider": "CPUExecutionProvider"}, b""

    monkeypatch.setattr("imagesorter.queue_worker.run_reader", reader_boundary)
    reader = EnrichmentReader("saved-confidence", str(tmp_path / "temporary-fixture.png"), reloaded.snapshot())
    reader.run()
    assert len(requests) == 1
    assert requests[0]["action"] == "infer"
    assert requests[0]["threshold"] == threshold
    assert requests[0]["model_dir"] is None


def test_components_panel_buttons_accessibility(qtbot):
    panel = ComponentsPanel()
    qtbot.addWidget(panel)
    assert panel.run_button.accessibleName() == "Apply component action button"
    assert panel.run_button.accessibleDescription() == "Applies the selected action to the chosen optional component."
    assert panel.import_button.accessibleName() == "Import component pack button"
    assert panel.import_button.accessibleDescription() == "Opens a file dialog to import a pinned component archive."
    assert panel.cancel_button.accessibleName() == "Cancel component operation button"
    assert panel.cancel_button.accessibleDescription() == "Cancels the currently running component background operation."
