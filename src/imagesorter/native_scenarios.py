"""Enumerated native diagnostic actions on newly generated private fixtures only.

These raw observations support acceptance. They NEVER self-certify the complete
KDE matrix, real Dolphin integration, power-loss recovery, or physical displays.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

SCENARIOS = {"core", "navigation", "hotkeys", "file-set", "recovery", "settings", "settings-corrupt", "settings-relaunch",
             "frames", "optional-ai-cpu", "optional-ai-gpu"}
MARKER = "Image Sorter disposable native diagnostic profile v1\n"


class Unavailable(RuntimeError):
    """An explicitly unobserved capability, not a passing test."""


def _hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _reference(path):
    return {"path": str(Path(path).resolve()), "sha256": _hash(path)}


def _json(path, value):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def reader_observation(directory, name, fixture, reply, *, context=None):
    """Preserve the exact returned metadata and a byte-bound fixture envelope."""
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name):
        raise ValueError("Unsafe reader observation name")
    if not isinstance(reply, dict) or not reply:
        raise ValueError("A complete reader reply is required")
    fixture_record = _reference(fixture)
    if reply.get("input_sha256") is not None and reply["input_sha256"] != fixture_record["sha256"]:
        raise ValueError("Reader observation fixture does not match its input digest")
    output = Path(directory) / "reader-replies"
    output.mkdir(mode=0o700, exist_ok=True)
    reply_path, envelope_path = output / f"{name}-reply.json", output / f"{name}-observation.json"
    with reply_path.open("x", encoding="utf-8") as handle:
        json.dump(reply, handle, indent=2, allow_nan=False)
        handle.write("\n")
    envelope = {"schema_version": 1, "kind": "reader-result-observation",
                "fixture": fixture_record, "reply": _reference(reply_path)}
    if context is not None:
        envelope["context"] = context
    with envelope_path.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return _reference(envelope_path)


def prepare(name):
    if name not in SCENARIOS:
        raise ValueError(f"Unknown native diagnostic scenario: {name}")
    value = os.environ.get("IMAGESORTER_PROFILE_ROOT")
    if not value:
        raise ValueError("Native diagnostics require --profile-root pointing to a disposable private profile")
    root = Path(value).resolve(strict=True)
    from .paths import get_data_dir, get_settings_path
    marker = root / ".native-diagnostic-profile"
    if marker.is_symlink():
        raise ValueError("Diagnostic profile marker must not be a link")
    if marker.exists():
        if marker.read_text() != MARKER:
            raise ValueError("Unrecognized diagnostic profile marker")
    else:
        if Path(get_settings_path()).exists() or (get_data_dir() / "operation_journal.db").exists():
            raise ValueError("Refusing native mutation diagnostics in a pre-existing unmarked settings/journal profile")
        marker.write_text(MARKER)
    root.chmod(0o700)
    directory = root / "native-diagnostics"
    if directory.is_symlink():
        raise ValueError("Diagnostic directory must not be a link")
    directory.mkdir(mode=0o700, exist_ok=True)
    run_id = uuid.uuid4().hex
    directory = directory / run_id
    directory.mkdir(mode=0o700)
    fixtures = directory / "fixtures"
    fixtures.mkdir()
    for child in ("source", "copy", "move", "trash", "screenshots"):
        (directory / child).mkdir()
    from PIL import Image
    images = []
    for index, color in enumerate(("red", "green", "blue")):
        path = directory / "source" / f"image-{index}.jpg"
        Image.new("RGB", (160, 100), color).save(path, quality=95)
        Path(str(path) + ".txt").write_text(f"Human note {index}\n")
        images.append(str(path))
    frames = [Image.new("RGBA", (120, 80), color) for color in ("red", "green", "blue")]
    frame_files = []
    for extension, format_name in (("gif", "GIF"), ("apng", "PNG"), ("webp", "WEBP"), ("tiff", "TIFF")):
        path = fixtures / f"frames.{extension}"
        frames[0].save(path, format=format_name, save_all=True, append_images=frames[1:],
                       duration=[80, 120, 160], loop=1)
        frame_files.append(str(path))
    settings = Path(get_settings_path())
    before = settings.read_bytes() if settings.exists() else None
    descriptor = {"schema_version": 1, "run_id": run_id, "scenario": name, "profile_root": str(root),
                  "directory": str(directory), "images": images, "frame_files": frame_files,
                  "settings_before_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
                  "corrupt_before_launch": bool(before and before.startswith(b"native-diagnostic-invalid-json:"))}
    _json(directory / "input.json", descriptor)
    if name == "recovery":
        if (get_data_dir() / "operation_journal.db").exists():
            raise ValueError("The crash/recovery diagnostic requires a fresh private journal profile")
        import subprocess

        from .worker_protocol import worker_command, worker_environment
        with (directory / "fixture-crash.log").open("wb") as log:
            process = subprocess.Popen(worker_command("native-fixture-job") + [str(directory / "input.json")],
                                       env=worker_environment(), stdout=log, stderr=log, start_new_session=True)
            try:
                code = process.wait(timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Diagnostic mutation helper {process.pid} is still running; preserve this profile. It was not killed.") from exc
        if code != 86:
            raise RuntimeError(f"Diagnostic fixture did not reach its recorded crash boundary (exit {code})")
        descriptor["crash_fixture"] = _reference(directory / "fixture-crash.json")
        _json(directory / "input.json", descriptor)
    return descriptor


def main(args=None):
    """Fixed crash-fixture role, restricted to a freshly marked diagnostic profile."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("descriptor")
    options = parser.parse_args(args)
    root_value = os.environ.get("IMAGESORTER_PROFILE_ROOT")
    if not root_value:
        raise ValueError("Crash fixtures require an explicit disposable profile")
    root = Path(root_value).resolve(strict=True)
    descriptor_path = Path(options.descriptor)
    if descriptor_path.is_symlink():
        raise ValueError("Diagnostic descriptor must not be a link")
    descriptor_path = descriptor_path.resolve(strict=True)
    descriptor = json.loads(descriptor_path.read_text())
    directory = root / "native-diagnostics" / descriptor["run_id"]
    if (descriptor["scenario"] != "recovery" or (root / ".native-diagnostic-profile").read_text() != MARKER or
            directory.resolve() != descriptor_path.parent or descriptor_path.name != "input.json" or
            not directory.resolve().is_relative_to(root / "native-diagnostics")):
        raise ValueError("Crash fixture escaped its generated private diagnostic run")
    from . import file_safety
    from .operation_engine import OperationEngine
    from .operation_journal import OperationJournal
    from .paths import get_data_dir
    from .profile_lock import ProfileLock
    journal_path = get_data_dir() / "operation_journal.db"
    with ProfileLock(str(journal_path) + ".lock"):
        if journal_path.exists():
            raise ValueError("Crash fixture refuses an existing journal")
        journal = OperationJournal(str(journal_path))
        source = directory / "source" / "image-0.jpg"
        operation_id = "diagnostic-crash-" + descriptor["run_id"]
        _json(directory / "fixture-crash.json", {"schema_version": 1, "operation_id": operation_id,
              "source": _reference(source), "sidecar": _reference(Path(str(source) + ".txt")),
              "boundary": "after first publish_no_replace syscall", "deliberate_exit_code": 86})
        original = file_safety.publish_no_replace
        def crash(stage, destination, expected):
            original(stage, destination, expected)
            os._exit(86)
        file_safety.publish_no_replace = crash
        try:
            OperationEngine(journal).execute({"operation_id": operation_id, "action": "move", "source_path": str(source),
                                             "destination_path": str(directory / "move"), "task_options": {}})
        finally:
            journal.close()
    return 1


def install(viewer, descriptor):
    from PyQt6.QtCore import QObject, Qt, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    class ScenarioRunner(QObject):
        def __init__(self):
            super().__init__(viewer)
            self.viewer = viewer
            self.directory = Path(descriptor["directory"])
            self.receipts = []
            self.cases = []
            self.current = None
            self.iterator = None
            self.waiting = None
            self.case_index = 0
            self._finished = False
            viewer._scenario_exit_code = 2  # Premature GUI exit is never a passing diagnostic.
            self.scenario = descriptor["scenario"]
            methods = {"navigation": self.navigation, "hotkeys": self.hotkeys, "file-set": self.file_set, "recovery": self.recovery,
                       "settings": self.settings, "settings-corrupt": self.settings_corrupt,
                       "settings-relaunch": self.settings_relaunch, "frames": self.frames,
                       "optional-ai-cpu": self.optional_ai, "optional-ai-gpu": self.optional_ai}
            self.selected = [(name, methods[name]) for name in ("navigation", "file-set", "settings")] if self.scenario == "core" else [(self.scenario, methods[self.scenario])]
            viewer.worker.signals.operation_result.connect(self.receipts.append)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(50)

        def check(self, name, condition, detail=None):
            self.current["checks"][name] = bool(condition)
            if detail is not None:
                self.current["details"][name] = detail
            if not condition:
                raise AssertionError(name)

        def wait(self, predicate, description, timeout=15):
            return predicate, time.monotonic() + timeout, description

        def displayed(self, path):
            return (0 <= viewer.current_index < len(viewer.images) and
                    viewer.images[viewer.current_index] == str(path) and viewer.viewer.isVisible() and
                    not viewer.viewer.original_pixmap.isNull() and viewer._frame_path == str(path) and
                    viewer._get_pixmap_from_cache(str(path)) is not None)

        def key(self, key, modifier=Qt.KeyboardModifier.NoModifier):
            viewer.activateWindow()
            viewer.setFocus()
            QTest.keyClick(viewer, key, modifier)

        def screenshot(self, name, widget=None):
            widget = widget or viewer
            path = self.directory / "screenshots" / f"{self.current['id']}-{name}.png"
            pixmap = widget.grab()
            if pixmap.isNull() or not pixmap.save(str(path)):
                raise AssertionError("Native widget screenshot failed")
            self.current["screenshots"].append(_reference(path))

        def finish_case(self, status, error=None):
            self.current.update(status=status, error=error, finished_monotonic=time.monotonic())
            receipt_path = self.directory / f"{self.current['id']}-operations.json"
            _json(receipt_path, self.receipts[self.current.pop("receipt_start"):])
            self.current["operation_receipts"] = _reference(receipt_path)
            self.current["fixtures_after"] = self.inventory()
            self.cases.append(self.current)
            self.current = self.iterator = self.waiting = None
            self.case_index += 1

        def inventory(self):
            return [_reference(path) for folder in ("source", "copy", "move", "trash")
                    for path in sorted((self.directory / folder).iterdir()) if path.is_file() and not path.is_symlink()]

        def tick(self):
            if viewer._closing:
                self.timer.stop()
                return
            try:
                if self.iterator is None:
                    if self.case_index == len(self.selected):
                        self.finish_run()
                        return
                    name, function = self.selected[self.case_index]
                    self.current = {"id": name, "started_monotonic": time.monotonic(), "checks": {}, "details": {},
                                    "screenshots": [], "receipt_start": len(self.receipts), "fixtures_before": self.inventory()}
                    self.iterator = iter(function())
                if self.waiting:
                    predicate, deadline, description = self.waiting
                    if not predicate():
                        if time.monotonic() >= deadline:
                            raise TimeoutError(description)
                        return
                    self.waiting = None
                self.waiting = next(self.iterator)
            except StopIteration:
                self.finish_case("passed")
            except Unavailable as exc:
                self.finish_case("not_run", str(exc))
            except Exception as exc:
                self.finish_case("failed", f"{type(exc).__name__}: {exc}")

        def abort(self):
            if self._finished:
                return
            if self.current is not None:
                self.finish_case("not_run", "Window closed before this diagnostic scenario finished")
            else:
                self.cases.append({"id": self.scenario, "status": "not_run", "error": "Window closed before diagnostic completion"})
            self.finish_run(close=False)

        def finish_run(self, *, close=True):
            self._finished = True
            self.timer.stop()
            from .paths import get_resource_dir
            try:
                build = json.loads((get_resource_dir() / "build_identity.json").read_text())
            except (OSError, ValueError):
                build = None
            result = {**descriptor, "kind": "automated-native-observations", "complete": False,
                      "native": QApplication.platformName() == "xcb", "display_backend": QApplication.platformName(),
                      "build_identity": build, "cases": self.cases,
                      "operator_review_required": ["Dolphin/desktop identity and ordered launch routes", "physical scale and monitor transitions",
                                                   "assistive technology and visual readability", "real mount disconnect and VM power-loss recovery"],
                      "screens": [{"name": screen.name(), "device_pixel_ratio": screen.devicePixelRatio(),
                                   "logical_dpi": screen.logicalDotsPerInch(), "geometry": list(screen.geometry().getRect())}
                                  for screen in QApplication.screens()]}
            _json(self.directory / "observations.json", result)
            viewer._scenario_exit_code = 1 if any(case["status"] == "failed" for case in self.cases) else 2 if any(case["status"] == "not_run" for case in self.cases) else 0
            from .diagnostics import record
            record("native_scenarios_finished", report=_reference(self.directory / "observations.json"), exit_code=viewer._scenario_exit_code)
            if close:
                viewer.close()

        def navigation(self):
            images = descriptor["images"]
            viewer.open_paths(images)
            yield self.wait(lambda: self.displayed(images[0]), "Initial native image never painted")
            self.key(Qt.Key.Key_Right)
            yield self.wait(lambda: self.displayed(images[1]), "Keyboard navigation did not display the next image")
            self.check("keyboard_next", viewer.current_index == 1)
            self.key(Qt.Key.Key_Left)
            self.key(Qt.Key.Key_Right)
            self.key(Qt.Key.Key_Right)
            yield self.wait(lambda: self.displayed(images[2]), "Rapid navigation did not converge on the requested image")
            self.check("rapid_navigation_final_identity", viewer.current_index == 2)
            viewer.viewer.scale(1.3, 1.3)
            viewport = viewer.viewer.transform()
            viewer.toggle_locked_zoom(True)
            self.key(Qt.Key.Key_Left)
            yield self.wait(lambda: self.displayed(images[1]), "Locked-viewport navigation failed")
            self.check("locked_viewport", viewer.viewer.transform() == viewport)
            viewer.viewer.toggle_clipping_warnings()
            self.check("clipping_overlay", viewer.viewer.show_clipping and not viewer.viewer.clipping_pixmap.isNull())
            viewer.toggle_zen_mode()
            self.check("zen_hides_hud", not viewer.hud_widget.isVisible())
            viewer.toggle_zen_mode()
            viewer.viewer.toggle_clipping_warnings()
            viewer.toggle_locked_zoom(False)
            self.screenshot("navigation")

        def terminal(self, operation_id):
            return next((row for row in self.receipts if row["operation_id"] == operation_id), None)

        def hotkeys(self):
            images = descriptor["images"]
            viewer.open_paths(images)
            yield self.wait(lambda: self.displayed(images[0]), "Hotkey fixture did not display")
            for key, action, advance in (("D", "copy", False), ("C", "copy", True), ("M", "move", True)):
                viewer.settings.set("hotkeys", key, {"action": action, "folder": str(self.directory / action), "auto_advance": advance})
            before = len(self.receipts)
            self.key(Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
            delay_until = time.monotonic() + 0.2
            yield self.wait(lambda: time.monotonic() >= delay_until, "Modifier observation window")
            self.check("modifier_does_not_dispatch_custom_key", len(self.receipts) == before and self.displayed(images[0]))
            self.key(Qt.Key.Key_D)
            yield self.wait(lambda: len(self.receipts) > before, "Custom copy key did not return a receipt")
            self.check("custom_overrides_fallback_navigation", len(self.receipts) == before + 1 and self.displayed(images[0]) and self.receipts[-1]["state"] == "completed")
            before = len(self.receipts)
            self.key(Qt.Key.Key_C)
            yield self.wait(lambda: len(self.receipts) > before and self.displayed(images[1]), "Copy auto-advance did not settle")
            self.check("copy_exactly_once_auto_advance", len(self.receipts) == before + 1 and not viewer.viewer.show_clipping and self.receipts[-1]["state"] == "completed")
            before = len(self.receipts)
            self.key(Qt.Key.Key_M)
            yield self.wait(lambda: len(self.receipts) > before and self.displayed(images[2]), "Move key and next-image presentation did not settle")
            self.check("move_exactly_once_pair", len(self.receipts) == before + 1 and self.receipts[-1]["state"] == "completed" and not Path(images[1]).exists() and not Path(images[1] + ".txt").exists())
            self.key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            yield self.wait(lambda: Path(images[1]).exists() and len(self.receipts) > before + 1, "Hotkey move Undo did not settle")
            self.check("modifier_undo_precedence", self.receipts[-1]["action"].startswith("undo") and Path(images[1] + ".txt").exists())
            copy_token = viewer.history[-1]
            self.check("copy_hotkey_retains_undo", copy_token["action"] == "copy")
            copied_path = Path(copy_token["current"])
            before = len(self.receipts)
            self.key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            yield self.wait(lambda: len(self.receipts) > before, "Custom-copy Undo did not settle")
            self.check("copy_hotkey_undo_pair", not copied_path.exists() and not Path(str(copied_path) + ".txt").exists())
            self.screenshot("custom-hotkeys")

        def submit(self, action, source, destination=None):
            source = Path(source).resolve(strict=True)
            if not source.is_relative_to(self.directory):
                raise ValueError("Diagnostic operation source escapes generated fixture directory")
            if destination is not None and not Path(destination).resolve().is_relative_to(self.directory):
                raise ValueError("Diagnostic destination escapes generated fixture directory")
            operation_id = viewer.trigger_file_action(action, str(source), str(destination) if destination is not None else None)
            if not operation_id:
                raise AssertionError("Diagnostic file action was not admitted")
            return operation_id

        def file_set(self):
            image = Path(descriptor["images"][0])
            sidecar = Path(str(image) + ".txt")
            original = (_hash(image), _hash(sidecar))
            destination = self.directory / "copy"
            collision = destination / image.name
            collision.write_bytes(b"Unrelated destination; never overwrite")
            collision_sidecar = Path(str(collision) + ".txt")
            collision_sidecar.write_bytes(b"Unrelated companion")
            collision_hashes = (_hash(collision), _hash(collision_sidecar))
            viewer.open_paths([str(image)])
            yield self.wait(lambda: self.displayed(image), "File-set fixture did not display")
            operation_id = self.submit("copy", image, destination)
            yield self.wait(lambda: self.terminal(operation_id), "Copy did not return its durable terminal receipt")
            copied = self.terminal(operation_id)
            self.check("copy_committed", copied["state"] == "completed", copied)
            copied_path = Path(copied["destination_path"])
            self.check("copy_pair", (_hash(copied_path), _hash(Path(str(copied_path) + ".txt"))) == original)
            self.check("collision_no_overwrite", copied_path != collision and collision_hashes == (_hash(collision), _hash(collision_sidecar)))
            self.check("copy_source_unchanged", original == (_hash(image), _hash(sidecar)))
            count = len(self.receipts)
            self.key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            yield self.wait(lambda: len(self.receipts) > count, "Copy Undo did not return a receipt")
            self.check("undo_copy", not copied_path.exists() and not Path(str(copied_path) + ".txt").exists())
            for action, folder in (("move", "move"), ("trash", "trash")):
                viewer.open_paths([str(image)])
                yield self.wait(lambda: self.displayed(image), "Transfer fixture did not display")
                if action == "trash":
                    viewer.settings.set("directories", "trash", str(self.directory / folder))
                operation_id = self.submit(action, image, self.directory / folder if action == "move" else None)
                yield self.wait(lambda oid=operation_id: self.terminal(oid), "Transfer did not return a receipt")
                result = self.terminal(operation_id)
                self.check(action + "_committed", result["state"] == "completed", result)
                current = Path(result["destination_path"])
                self.check(action + "_pair", not image.exists() and not sidecar.exists() and
                           (_hash(current), _hash(Path(str(current) + ".txt"))) == original)
                count = len(self.receipts)
                self.key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
                yield self.wait(lambda before=count: len(self.receipts) > before, "Transfer Undo did not return a receipt")
                self.check("undo_" + action, image.exists() and sidecar.exists() and
                           (_hash(image), _hash(sidecar)) == original and not current.exists())
            bad_destination = self.directory / "unwritable"
            bad_destination.mkdir()
            bad_destination.chmod(0o500)
            try:
                operation_id = self.submit("move", image, bad_destination)
                yield self.wait(lambda: self.terminal(operation_id), "Unwritable-destination attempt did not finish")
                self.check("unwritable_destination_refused", self.terminal(operation_id)["state"] == "failed")
                self.check("failure_source_pair_intact", (_hash(image), _hash(sidecar)) == original)
            finally:
                bad_destination.chmod(0o700)
            operation_id = self.submit("copy", image, destination)
            yield self.wait(lambda: self.terminal(operation_id), "Changed-target fixture copy did not finish")
            copied = self.terminal(operation_id)
            current = Path(copied["destination_path"])
            with current.open("ab") as handle:
                handle.write(b"External edit preserved by refused Undo")
            changed = _hash(current)
            count = len(self.receipts)
            self.key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            yield self.wait(lambda: len(self.receipts) > count, "Changed-target Undo did not finish")
            self.check("changed_target_refused", self.receipts[-1]["state"] == "failed")
            self.check("unrelated_bytes_preserved", _hash(current) == changed and (_hash(image), _hash(sidecar)) == original)
            self.screenshot("file-set-and-refused-undo")

        def settings(self):
            from .ui_settings import SettingsWindow
            window = SettingsWindow(viewer.settings, parent=viewer)
            window.show()
            yield self.wait(lambda: window.check_worker is None, "Settings model validation did not settle")
            window.src_edit.setFocus()
            index = viewer.current_index
            QTest.keyClick(window.src_edit, Qt.Key.Key_D)
            self.check("edit_focus_does_not_navigate", viewer.current_index == index)
            self.check("focused_editor", QApplication.focusWidget() is window.src_edit)
            self.check("components_visible", window.components_panel.table.columnCount() == 5)
            self.screenshot("settings", window)
            window.tabs.setCurrentWidget(window.components_panel)
            yield self.wait(window.components_panel.isVisible, "Optional Components tab did not become visible")
            self.screenshot("optional-components", window)
            window.reject()
            yield self.wait(lambda: not window.isVisible(), "Settings dialog did not close")
            from .settings_manager import SettingsManager
            viewer.settings.set("ui", "font_size", 26)
            self.check("settings_roundtrip", SettingsManager(filepath=viewer.settings.filepath).get("ui", "font_size") == 26)

        def recovery(self):
            from PyQt6.QtWidgets import QMessageBox

            from .ui_recovery import RecoveryDialog
            expected = json.loads(Path(descriptor["crash_fixture"]["path"]).read_text())
            yield self.wait(lambda: any(row["operation_id"] == expected["operation_id"] for row in viewer._recovery_records), "Interrupted fixture was not reported by the journal owner")
            dialog = RecoveryDialog(viewer)
            dialog.show()
            yield self.wait(lambda: dialog.rollback.isEnabled(), "Verified rollback selection did not become available")
            self.screenshot("preserved-before-rollback", dialog)
            # Click the actual dialog button and affirmative confirmation. This
            # is limited to the fixed generated crash fixture, never user files.
            def confirm_fixture():
                active = QApplication.activeModalWidget()
                if isinstance(active, QMessageBox):
                    active.button(QMessageBox.StandardButton.Yes).click()
            QTimer.singleShot(100, confirm_fixture)
            QTest.mouseClick(dialog.rollback, Qt.MouseButton.LeftButton)
            yield self.wait(lambda: any(row.get("resolved_operation_id") == expected["operation_id"] for row in self.receipts), "Confirmed rollback did not produce a resolving receipt")
            source, sidecar = Path(expected["source"]["path"]), Path(expected["sidecar"]["path"])
            self.check("confirmed_recovery_exact_original_pair", _hash(source) == expected["source"]["sha256"] and _hash(sidecar) == expected["sidecar"]["sha256"])
            yield self.wait(lambda: not viewer._recovery_records, "Resolved record did not refresh from the authoritative service")
            self.check("no_public_partial_destination", not list((self.directory / "move").glob("*.jpg*")))
            self.screenshot("resolved-after-rollback", dialog)
            dialog.reject()
            self.current["details"]["crash_fixture"] = descriptor["crash_fixture"]

        def settings_corrupt(self):
            yield from self.settings()
            path = Path(viewer.settings.filepath).resolve()
            self.check("settings_profile_scoped", path.is_relative_to(Path(descriptor["profile_root"])))
            payload = b"native-diagnostic-invalid-json:" + descriptor["run_id"].encode()
            path.write_bytes(payload)
            _json(Path(descriptor["profile_root"]) / "expected-corrupt-settings.json", {"sha256": hashlib.sha256(payload).hexdigest()})
            self.current["details"]["relaunch_required"] = "Launch the same disposable profile with settings-relaunch; this phase is not recovery evidence by itself."

        def settings_relaunch(self):
            if not descriptor["corrupt_before_launch"]:
                raise Unavailable("No known diagnostic corruption was present before this launch")
            expected = json.loads((Path(descriptor["profile_root"]) / "expected-corrupt-settings.json").read_text())
            path = Path(viewer.settings.filepath)
            backups = [item for item in path.parent.glob(path.name + ".corrupt.*.bak") if _hash(item) == expected["sha256"]]
            self.check("actual_relaunch_corrupt_backup", len(backups) == 1, [_reference(item) for item in backups])
            self.check("validated_default_font", viewer.settings.get("ui", "font_size") == 24)
            self.screenshot("recovered-settings")
            yield self.wait(lambda: True, "Completed")

        def frames(self):
            from .component_manager import ComponentManager
            rows = ComponentManager().status()
            if not any(row["id"] == "viewer.animation-multipage" and row["active"] and row["enabled"] for row in rows):
                raise Unavailable("Animation/multipage component has not been explicitly installed/enabled")
            for path in descriptor["frame_files"]:
                extension = Path(path).suffix.lstrip(".")
                viewer.open_paths([path])
                yield self.wait(lambda filename=path: self.displayed(filename), "Animated/multipage first frame did not display")
                self.check(extension + "_paused_first_frame", not viewer._frame_playing and viewer._frame_index == 0 and viewer._frame_count == 3)
                reply = viewer._frame_metadata[viewer._cache_key(path)]
                self.current.setdefault("reader_results", []).append(
                    reader_observation(self.directory, extension + "-first-frame", path, reply))
                viewer.viewer.scale(1.2, 1.2)
                transform = viewer.viewer.transform()
                viewer.frame_seek.setValue(3)
                yield self.wait(lambda: viewer._frame_index == 2 and not viewer._frame_request, "Frame seek did not complete")
                self.check(extension + "_seek_and_viewport", viewer.viewer.transform() == transform and viewer.frame_seek.value() == 3)
                self.screenshot(extension + "-last-frame")
                viewer._seek_frame(0)
                yield self.wait(lambda: viewer._frame_index == 0 and not viewer._frame_request, "First-frame seek did not complete")
                started = time.monotonic()
                viewer._set_frame_playing(True)
                yield self.wait(lambda: not viewer._frame_buffering, "Playback buffer did not become ready")
                self.check(extension + "_playback_started", viewer._frame_playing)
                self.current["details"][extension + "_cold_buffer_ms"] = round((time.monotonic() - started) * 1000)
                scheduled = viewer._frame_tick_started
                expected_duration = max(1, viewer._frame_metadata[viewer._frame_base_key].get("duration_ms", 100) or 100)
                yield self.wait(lambda: viewer._frame_index == 1, "Playback did not advance")
                ready_elapsed = round((time.monotonic() - scheduled) * 1000)
                self.current["details"][extension + "_first_ready_advance_ms"] = ready_elapsed
                self.check(extension + "_ready_frame_timing", expected_duration - 15 <= ready_elapsed <= expected_duration + 100,
                           {"declared_duration_ms": expected_duration, "observed_ms": ready_elapsed, "observer_poll_interval_ms": 50})
                viewer._set_frame_playing(False)
                self.check(extension + "_pause", not viewer._frame_timer.isActive())
                QTest.mouseClick(viewer.frame_previous, Qt.MouseButton.LeftButton)
                yield self.wait(lambda: viewer._frame_index == 0 and not viewer._frame_request, "Previous-frame button did not step")
                QTest.mouseClick(viewer.frame_next, Qt.MouseButton.LeftButton)
                yield self.wait(lambda: viewer._frame_index == 1 and not viewer._frame_request, "Next-frame button did not step")
                self.check(extension + "_step_controls", not viewer._frame_playing and viewer.viewer.transform() == transform)
                viewer.frame_loop.setFocus()
                QTest.keyClick(viewer.frame_loop, Qt.Key.Key_Space)
                self.check(extension + "_continuous_loop_control", viewer.frame_loop.isChecked())
                viewer._seek_frame(2)
                yield self.wait(lambda: viewer._frame_index == 2 and not viewer._frame_request, "Last frame did not become ready for loop check")
                viewer._set_frame_playing(True)
                yield self.wait(lambda: viewer._frame_index == 0 and viewer._frame_repeats >= 1, "Continuous loop did not wrap")
                self.check(extension + "_continuous_loop", viewer._frame_playing and viewer.viewer.transform() == transform)
                viewer._set_frame_playing(False)
                viewer.frame_loop.setFocus()
                QTest.keyClick(viewer.frame_loop, Qt.Key.Key_Space)
                self.check(extension + "_finite_loop_control", not viewer.frame_loop.isChecked())
                viewer._seek_frame(0)
                yield self.wait(lambda: viewer._frame_index == 0 and not viewer._frame_request, "First frame did not become ready for finite loop check")
                # Exercise every declared repeat with real timer callbacks.
                viewer._set_frame_playing(True)
                yield self.wait(lambda: not viewer._frame_playing, "Finite playback did not stop at its declared repeat limit")
                self.check(extension + "_finite_loop_stops", viewer._frame_index == 2 and not viewer._frame_timer.isActive())
                # The fixture generator declares raw loop=1. GIF stores repeats
                # after the first play; APNG/WebP store total plays. TIFF has no
                # animation-loop field. This oracle is independent of the reply.
                expected_wraps = 1 if extension == "gif" else 0
                self.check(extension + "_exact_repeat_count", viewer._frame_repeats == expected_wraps,
                           {"expected_wraps": expected_wraps, "observed_wraps": viewer._frame_repeats})

        def optional_ai(self):
            from .component_manager import ComponentManager
            rows = ComponentManager().status()
            if not any(row["id"] == "ai.mobilenet-v2" and row["active"] and row["enabled"] for row in rows):
                raise Unavailable("Pinned model has not been explicitly installed/enabled")
            gpu = self.scenario.endswith("gpu")
            viewer.settings.set("ai_tagger", "enabled", True)
            viewer.settings.set("ai_tagger", "threshold", 0.0)
            viewer.settings.set("advanced", "hardware_acceleration", gpu)
            viewer.settings.set("metadata", "write_sidecar", True)
            source = Path(descriptor["images"][0])
            original = (_hash(source), _hash(Path(str(source) + ".txt")))
            viewer.open_paths([str(source)])
            yield self.wait(lambda: self.displayed(source), "Inference fixture did not display")
            operation_id = self.submit("copy", source, self.directory / "copy")
            yield self.wait(lambda: self.terminal(operation_id), "Primary image was not committed before inference")
            self.check("primary_committed", self.terminal(operation_id)["state"] == "completed")
            yield self.wait(lambda: any(row.get("action") == "metadata" for row in self.receipts) or
                            (getattr(viewer.worker, "last_inference_failure", None) or {}).get("parent_operation_id") == operation_id,
                            "Optional metadata child did not return a receipt", timeout=220)
            self.check("inference_succeeded", not getattr(viewer.worker, "last_inference_failure", None),
                       getattr(viewer.worker, "last_inference_failure", None))
            metadata = next(row for row in self.receipts if row.get("action") == "metadata")
            self.check("metadata_child_committed", metadata["state"] == "completed", metadata)
            receipt = getattr(viewer.worker, "last_inference_receipt", {})
            self.current["details"]["inference_receipt"] = receipt
            self.current.setdefault("reader_results", []).append(reader_observation(
                self.directory, "cuda-inference" if gpu else "cpu-inference", source,
                getattr(viewer.worker, "last_inference_reply", {}),
                context={"parent_operation_id": operation_id, "inference_copy_path": self.terminal(operation_id)["destination_path"],
                         "fixture_relation": "The preserved source has the same bytes as the committed copy supplied to inference before metadata enrichment."}))
            if gpu:
                self.current["gpu_result"] = _reference(self.directory / "reader-replies" / "cuda-inference-reply.json")
            self.check("actual_provider", receipt.get("provider") == ("CUDAExecutionProvider" if gpu else "CPUExecutionProvider"))
            if gpu:
                self.check("actual_cuda_compute", receipt.get("cuda_compute_events", 0) > 0)
            self.check("original_pair_untouched", original == (_hash(source), _hash(Path(str(source) + ".txt"))))
            self.screenshot("provider-and-tagging")
            primary = self.terminal(operation_id)
            token = viewer.history[-1]
            self.check("metadata_revises_parent_undo", token["token_id"] == primary["undo_token"]["token_id"] and
                       token.get("revision", 1) > primary["undo_token"].get("revision", 1))
            copied = Path(primary["destination_path"])
            count = len(self.receipts)
            self.key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            yield self.wait(lambda: len(self.receipts) > count and self.receipts[-1].get("action", "").startswith("undo"), "Enriched parent Undo did not return its receipt")
            self.check("enriched_parent_undo_pair", self.receipts[-1]["state"] == "completed" and
                       not copied.exists() and not Path(str(copied) + ".txt").exists() and
                       original == (_hash(source), _hash(Path(str(source) + ".txt"))))

    runner = ScenarioRunner()
    viewer._native_scenario_runner = runner
    return runner
