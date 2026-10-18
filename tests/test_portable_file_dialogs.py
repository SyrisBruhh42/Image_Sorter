"""Actual Qt selection/cancel journeys; no substitute for native OS qualification."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "Qt is required")
class PortableFileDialogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="portable-picker-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.usb = self.root / "USB with spaces"
        self.usb.mkdir()
        (self.usb / "portable.flag").touch()
        self.host = self.root / "host"
        self.host.mkdir()
        self.environment = os.environ.copy()
        for key in ("IMAGESORTER_PROFILE_ROOT", "IMAGESORTER_PORTABLE_ROOT", "PYTHONHOME", "APPDIR", "APPIMAGE"):
            self.environment.pop(key, None)
        self.environment.update(PYTHONPATH=str(SOURCE), PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
                                HOME=str(self.host), USERPROFILE=str(self.host))
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA", "TMPDIR", "TMP", "TEMP"):
            directory = self.host / key
            directory.mkdir()
            self.environment[key] = str(directory)
        (self.host / "XDG_CONFIG_HOME" / "QtProject.conf").write_text("[sentinel]\nvalue=unchanged\n")

    def snapshot(self):
        return {str(path.relative_to(self.host)): path.read_bytes() for path in self.host.rglob("*") if path.is_file()}

    def run_journey(self, body, *, explicit=False, configure=True):
        environment = self.environment.copy()
        if explicit:
            environment["IMAGESORTER_PROFILE_ROOT"] = str(self.root / "explicit")
        script = self.usb / "launcher.py"
        prefix = '''
            import json, os
            from pathlib import Path
            from imagesorter.bootstrap import configure_profile, configure_runtime_environment
            if root := os.environ.get("IMAGESORTER_PROFILE_ROOT"):
                configure_profile(root)
        '''
        if configure:
            prefix += "\n            assert configure_runtime_environment()\n"
        prefix += '''
            from PyQt6.QtCore import QTimer
            from PyQt6.QtWidgets import QApplication
            from imagesorter.file_dialogs import FileDialog, IsolatedPathDialog
            app = QApplication([])
        '''
        script.write_text(textwrap.dedent(prefix) + "\n" + textwrap.dedent(body))
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(script)], cwd=self.usb, env=environment,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, self.snapshot(), "File selection wrote outside the isolated profile")
        return result

    def test_open_existing_archive_and_cancel_leave_host_state_unchanged(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                archive = self.usb / "valid pack.tar.gz"
                archive.write_bytes(b"fixture")
                result = self.run_journey(f'''
                    def select():
                        dialog = next(w for w in app.topLevelWidgets() if isinstance(w, IsolatedPathDialog) and w.isVisible())
                        dialog.selection.setText({str(archive)!r})
                        dialog.accept()
                    QTimer.singleShot(0, select)
                    filename, selected_filter = FileDialog.getOpenFileName(None, "Import", "", "Component pack (*.tar.gz)")
                    assert filename == {str(archive)!r}
                    assert selected_filter == "Component pack (*.tar.gz)"
                    def cancel():
                        next(w for w in app.topLevelWidgets() if isinstance(w, IsolatedPathDialog) and w.isVisible()).reject()
                    QTimer.singleShot(0, cancel)
                    assert FileDialog.getOpenFileName(None, "Cancel") == ("", "")
                    print(json.dumps({{"selected": filename, "cancelled": True}}))
                ''', explicit=explicit)
                self.assertTrue(json.loads(result.stdout)["cancelled"])

    def test_directory_navigation_hidden_toggle_and_invalid_selection(self):
        destination = self.usb / "parent" / "selected folder"
        destination.mkdir(parents=True)
        self.run_journey(f'''
            def select():
                dialog = next(w for w in app.topLevelWidgets() if isinstance(w, IsolatedPathDialog) and w.isVisible())
                assert dialog.navigate({str(destination.parent)!r})
                dialog.hidden.setChecked(True)
                dialog.selection.setText("missing directory")
                dialog.accept()
                assert dialog.isVisible() and dialog.error.text()
                assert dialog.navigate("selected folder")
                dialog.accept()
            QTimer.singleShot(0, select)
            assert FileDialog.getExistingDirectory(None, "Directory", {str(self.usb)!r}) == {str(destination)!r}
        ''')

    def test_archive_filter_rejects_unmatched_file_then_accepts_matching_file(self):
        (self.usb / "wrong.txt").write_text("fixture")
        (self.usb / "RIGHT.TAR.GZ").write_bytes(b"fixture")
        self.run_journey('''
            def select():
                dialog = next(w for w in app.topLevelWidgets() if isinstance(w, IsolatedPathDialog) and w.isVisible())
                dialog.selection.setText("wrong.txt")
                dialog.accept()
                assert dialog.isVisible() and dialog.error.text()
                dialog.selection.setText("RIGHT.TAR.GZ")
                dialog.accept()
            QTimer.singleShot(0, select)
            selected, _ = FileDialog.getOpenFileName(None, "Import", "", "Component pack (*.tar.gz)")
            assert Path(selected).name == "RIGHT.TAR.GZ"
        ''')

    def test_repeated_parented_dialogs_release_models_after_close(self):
        self.run_journey('''
            from PyQt6.QtCore import QCoreApplication, QEvent
            from PyQt6.QtWidgets import QWidget
            from PyQt6 import sip
            owner = QWidget()
            closed = []
            def cancel():
                dialog = next(w for w in app.topLevelWidgets() if isinstance(w, IsolatedPathDialog) and w.isVisible())
                closed.append((dialog, dialog.model))
                dialog.reject()
            for _ in range(5):
                QTimer.singleShot(0, cancel)
                assert FileDialog.getExistingDirectory(owner, "Directory") == ""
                QTimer.singleShot(0, cancel)
                assert FileDialog.getOpenFileName(owner, "File") == ("", "")
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                assert not owner.findChildren(IsolatedPathDialog)
                assert all(sip.isdeleted(dialog) and sip.isdeleted(model) for dialog, model in closed)
        ''')

    def test_picker_itself_has_no_native_history_even_before_environment_redirection(self):
        self.run_journey('''
            def cancel():
                next(w for w in app.topLevelWidgets() if isinstance(w, IsolatedPathDialog) and w.isVisible()).reject()
            QTimer.singleShot(0, cancel)
            assert FileDialog.getExistingDirectory(None, "Cancel") == ""
        ''', configure=False)

    def test_ordinary_mode_keeps_native_dialog_behavior(self):
        (self.usb / "portable.flag").unlink()
        self.run_journey('''
            from unittest.mock import patch
            with patch("imagesorter.file_dialogs.QFileDialog.getExistingDirectory", return_value="native directory") as native:
                assert FileDialog.getExistingDirectory(None, "Ordinary", "start") == "native directory"
                native.assert_called_once_with(None, "Ordinary", "start")
        ''', configure=False)


if __name__ == "__main__":
    unittest.main()
