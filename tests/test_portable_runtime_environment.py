"""Real subprocess checks for library state selected before application imports."""
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


class PortableRuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="portable-library-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.usb = self.root / "USB with spaces"
        self.usb.mkdir()
        (self.usb / "portable.flag").touch()
        self.host = self.root / "host"
        self.host.mkdir()
        self.environment = os.environ.copy()
        for key in ("IMAGESORTER_PROFILE_ROOT", "IMAGESORTER_PORTABLE_ROOT", "PYTHONHOME", "APPIMAGE", "APPDIR"):
            self.environment.pop(key, None)
        self.environment.update(PYTHONPATH=str(SOURCE), PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
                                HOME=str(self.host), USERPROFILE=str(self.host))
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA",
                    "TMPDIR", "TMP", "TEMP", "CUDA_CACHE_PATH", "__GL_SHADER_DISK_CACHE_PATH", "MESA_SHADER_CACHE_DIR", "QT_SHADER_CACHE_PATH"):
            directory = self.host / key
            directory.mkdir()
            self.environment[key] = str(directory)

    def run_probe(self, body, *, environment=None):
        script = self.usb / "launcher.py"
        script.write_text(textwrap.dedent(body))
        return subprocess.run([sys.executable, str(script)], cwd=self.host,
                              env=environment or self.environment, text=True, capture_output=True, timeout=30)

    def test_temp_and_library_cache_paths_are_inside_selected_profile(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                environment = self.environment.copy()
                selected = self.root / "explicit" if explicit else self.usb
                if explicit:
                    environment["IMAGESORTER_PROFILE_ROOT"] = str(selected)
                result = self.run_probe('''
                    import json, os, tempfile
                    from pathlib import Path
                    previous = tempfile.gettempdir()
                    from imagesorter.bootstrap import configure_profile, configure_runtime_environment
                    if root := os.environ.get("IMAGESORTER_PROFILE_ROOT"):
                        configure_profile(root)
                    assert configure_runtime_environment()
                    with tempfile.NamedTemporaryFile() as output:
                        values = {key: os.environ[key] for key in (
                            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA",
                            "TMPDIR", "TMP", "TEMP", "CUDA_CACHE_PATH", "__GL_SHADER_DISK_CACHE_PATH", "MESA_SHADER_CACHE_DIR", "QT_SHADER_CACHE_PATH")}
                        values["actual_temp_file"] = output.name
                        assert previous != tempfile.gettempdir()
                        print(json.dumps(values))
                ''', environment=environment)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(all(Path(value).is_relative_to(selected) for value in json.loads(result.stdout).values()))
                self.assertFalse(any(path.is_file() for path in self.host.rglob("*")))

    def test_ordinary_launch_preserves_environment_and_temp_selection(self):
        (self.usb / "portable.flag").unlink()
        result = self.run_probe('''
            import os, tempfile
            from imagesorter.bootstrap import configure_runtime_environment
            before = dict(os.environ)
            previous = tempfile.gettempdir()
            assert configure_runtime_environment() is False
            assert before == dict(os.environ)
            assert previous == tempfile.gettempdir()
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_diagnostics_require_selected_profile_and_remain_opt_in(self):
        (self.usb / "portable.flag").unlink()
        result = self.run_probe(f'''
            from imagesorter import diagnostics
            from pathlib import Path
            candidate = Path({str(self.usb / "never-created/receipt.json")!r})
            diagnostics.record("not-configured")
            assert not candidate.parent.exists()
            try:
                diagnostics.configure(candidate)
            except ValueError as error:
                assert "selected explicit or portable profile" in str(error)
            else:
                raise AssertionError("Ordinary profile accepted diagnostic output")
            assert not candidate.parent.exists()
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_diagnostics_write_only_inside_explicit_or_portable_selection(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                environment = self.environment.copy()
                selected = self.root / "explicit" if explicit else self.usb
                if explicit:
                    environment["IMAGESORTER_PROFILE_ROOT"] = str(selected)
                    environment["IMAGESORTER_PORTABLE_ROOT"] = str(self.usb)
                result = self.run_probe(f'''
                    import json
                    from pathlib import Path
                    from imagesorter import diagnostics
                    root = Path({str(selected)!r})
                    receipt = root / "state/receipt.json"
                    diagnostics.configure(receipt)
                    assert not receipt.exists()
                    diagnostics.record("fixture")
                    observed = json.loads(receipt.read_text())
                    assert observed["profile_root"] == str(root)
                    assert observed["events"][0]["event"] == "fixture"
                    outside = Path({str(self.host / "refused/receipt.json")!r})
                    try:
                        diagnostics.configure(outside)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Outside receipt was accepted")
                    assert not outside.parent.exists()
                ''', environment=environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(path.is_file() for path in self.host.rglob("*")))

    def test_diagnostic_receipt_and_parent_symlinks_cannot_escape(self):
        state = self.usb / "state"
        state.mkdir()
        for link, target in ((state / "receipt.json", self.host / "outside.json"), (self.usb / "escape", self.host)):
            try:
                link.symlink_to(target, target_is_directory=target.is_dir())
            except OSError as error:
                self.skipTest(f"Symlink creation unavailable: {error}")
        result = self.run_probe(f'''
            from imagesorter import diagnostics
            from pathlib import Path
            for candidate in ({str(state / "receipt.json")!r}, {str(self.usb / "escape/new/receipt.json")!r}):
                try:
                    diagnostics.configure(candidate)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Escaping symlink was accepted")
            assert not Path({str(self.host / "new")!r}).exists()
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(path.is_file() for path in self.host.rglob("*")))

    def test_diagnostic_staging_does_not_follow_preexisting_tmp_symlink(self):
        state = self.usb / "state"
        state.mkdir()
        outside = self.host / "untouched.txt"
        outside.write_text("original")
        try:
            (state / "receipt.tmp").symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        result = self.run_probe(f'''
            from imagesorter import diagnostics
            from pathlib import Path
            diagnostics.configure({str(state / "receipt.json")!r})
            diagnostics.record("fixture")
            assert Path({str(state / "receipt.json")!r}).is_file()
            assert Path({str(state / "receipt.tmp")!r}).is_symlink()
            assert not list(Path({str(state)!r}).glob(".readiness-*.tmp"))
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), "original")

    def test_diagnostic_parent_replacement_before_record_is_rejected(self):
        state = self.usb / "state"
        state.mkdir()
        # Check symlink support before the child changes the test directory.
        probe = self.usb / "symlink-probe"
        try:
            probe.symlink_to(self.host, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        probe.unlink()
        result = self.run_probe(f'''
            from imagesorter import diagnostics
            from pathlib import Path
            state = Path({str(state)!r})
            diagnostics.configure(state / "receipt.json")
            state.rename(state.with_name("original-state"))
            state.symlink_to({str(self.host)!r}, target_is_directory=True)
            try:
                diagnostics.record("fixture")
            except ValueError as error:
                assert "escapes" in str(error)
            else:
                raise AssertionError("Replaced receipt parent was accepted")
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(path.is_file() for path in self.host.rglob("*")))

    @unittest.skipUnless(importlib.util.find_spec("PyQt6"), "Qt is required")
    def test_gui_startup_disables_shader_disk_cache_inside_isolated_profile(self):
        result = self.run_probe('''
            from PyQt6.QtCore import Qt
            from PyQt6.QtWidgets import QApplication
            from imagesorter import main
            class Viewer:
                def __init__(self, settings, initial_paths=None):
                    assert QApplication.testAttribute(Qt.ApplicationAttribute.AA_DisableShaderDiskCache)
                def show(self):
                    pass
                def close(self):
                    pass
            main.MainViewer = Viewer
            QApplication.exec = lambda self: 0
            assert main.main() == 0
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlinks unavailable")
    def test_escaping_library_cache_refuses_before_redirect_or_write(self):
        (self.usb / "cache").mkdir()
        try:
            (self.usb / "cache" / "temporary").symlink_to(self.host, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        result = self.run_probe('''
            from imagesorter.bootstrap import configure_runtime_environment
            configure_runtime_environment()
        ''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("escapes root", result.stderr)
        self.assertFalse(any(path.is_file() for path in self.host.rglob("*")))


if __name__ == "__main__":
    unittest.main()
