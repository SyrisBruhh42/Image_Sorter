"""Real subprocess coverage for the portable profile's launch identity.

The frozen fixture runs a real execve through the production runtime copier;
it simulates an onedir bundle and is not a packaged-binary qualification.
Run the stdlib cases with: python -m unittest discover -s tests -p test_portable_process_identity.py
"""
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
PATH_PROBE = """
import json, os, sys
from imagesorter import paths
print(json.dumps({
    "settings": str(paths.get_settings_path()),
    "config": str(paths.get_config_dir()), "data": str(paths.get_data_dir()),
    "cache": str(paths.get_cache_dir()), "logs": str(paths.get_logs_dir()),
    "components": str(paths.get_components_dir()),
    "portable": paths.is_portable_mode(),
    "inherited": os.environ.get("IMAGESORTER_PORTABLE_ROOT"),
    "executable": sys.executable,
}))
"""


class PortableProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="portable-process-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.usb = self.root / "USB with spaces"
        self.usb.mkdir()
        (self.usb / "portable.flag").touch()
        (self.usb / "settings.json").write_text('{"retained": true}')
        self.host = self.root / "host"
        self.host.mkdir()
        self.cwd = self.root / "unrelated-cwd"
        self.cwd.mkdir()
        self.environment = os.environ.copy()
        for key in ("IMAGESORTER_PROFILE_ROOT", "IMAGESORTER_PORTABLE_ROOT", "PYTHONHOME"):
            self.environment.pop(key, None)
        self.environment.update(PYTHONPATH=str(SOURCE), PYTHONDONTWRITEBYTECODE="1",
                                QT_QPA_PLATFORM="offscreen", HOME=str(self.host),
                                USERPROFILE=str(self.host), APPDATA=str(self.host / "roaming"),
                                LOCALAPPDATA=str(self.host / "local"))
        for category in ("CONFIG", "DATA", "CACHE", "STATE"):
            self.environment[f"XDG_{category}_HOME"] = str(self.host / category.lower())
        temp = self.root / "tmp"
        temp.mkdir()
        self.environment.update(TMPDIR=str(temp), TMP=str(temp), TEMP=str(temp))

    def run_script(self, body, *, environment=None, arguments=(), executable=False):
        script = self.usb / ("ImageSorter" if executable else "launcher.py")
        script.write_text((f"#!{sys.executable}\n" if executable else "") + textwrap.dedent(body))
        if executable:
            script.chmod(0o700)
        command = [str(script)] if executable else [sys.executable, str(script)]
        return subprocess.run([*command, *arguments], cwd=self.cwd,
                              env=self.environment if environment is None else environment,
                              text=True, capture_output=True, timeout=30)

    def successful_json(self, result):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def assert_portable_paths(self, values):
        self.assertTrue(values["portable"])
        self.assertEqual(values["settings"], str(self.usb / "settings.json"))
        for category in ("config", "data", "cache", "logs"):
            self.assertEqual(values[category], str(self.usb / category))
        self.assertEqual(values["components"], str(self.usb / "data" / "components"))
        self.assertEqual((self.usb / "settings.json").read_text(), '{"retained": true}')
        self.assertFalse(list(self.host.rglob("ImageSorter")), "Worker wrote to a native host profile")

    def test_source_worker_keeps_launch_root_after_argv_and_cwd_change(self):
        body = f"""
            import subprocess, sys
            from imagesorter.worker_protocol import worker_command, worker_environment
            environment = worker_environment()
            subprocess.run(worker_command("component-job") + ["status"],
                           env=environment, check=True, capture_output=True)
            subprocess.run([sys.executable, "-c", {PATH_PROBE!r}], env=environment, check=True)
        """
        values = self.successful_json(self.run_script(body))
        self.assert_portable_paths(values)
        self.assertEqual(values["inherited"], str(self.usb))

    def test_component_command_bootstraps_explicit_cli_precedence(self):
        explicit = self.root / "explicit"
        environment = dict(self.environment, IMAGESORTER_PORTABLE_ROOT="invalid-inherited-root")
        body = f"""
            import subprocess
            from imagesorter.component_runtime import component_job_command
            subprocess.run(component_job_command(["--profile-root", {str(explicit)!r}, "status"]), check=True)
        """
        values = self.successful_json(self.run_script(body, environment=environment))
        self.assertEqual(values["state"], "completed")
        self.assertTrue((explicit / "data" / "ImageSorter" / "components").is_dir())
        self.assertFalse((self.usb / "data").exists())
        self.assertFalse(list(self.host.rglob("ImageSorter")))

    def test_explicit_environment_overrides_inherited_portable_identity(self):
        explicit = self.root / "explicit"
        environment = dict(self.environment, IMAGESORTER_PROFILE_ROOT=str(explicit),
                           IMAGESORTER_PORTABLE_ROOT="invalid-inherited-root")
        body = f"""
            import subprocess, sys
            from imagesorter.worker_protocol import worker_environment
            subprocess.run([sys.executable, "-c", {PATH_PROBE!r}], env=worker_environment(), check=True)
        """
        values = self.successful_json(self.run_script(body, environment=environment))
        self.assertFalse(values["portable"])
        self.assertIsNone(values["inherited"])
        self.assertEqual(values["settings"], str(explicit / "config" / "ImageSorter" / "settings.json"))
        self.assertEqual(values["data"], str(explicit / "data" / "ImageSorter"))
        self.assertEqual(values["logs"], str(explicit / "state" / "ImageSorter" / "logs"))
        self.assertFalse((self.usb / "data").exists())

    def test_inherited_root_works_without_flag(self):
        (self.usb / "portable.flag").unlink()
        environment = dict(self.environment, IMAGESORTER_PORTABLE_ROOT=str(self.usb))
        self.assert_portable_paths(self.successful_json(self.run_script(PATH_PROBE, environment=environment)))

    def test_invalid_inherited_roots_fail_closed_before_writes(self):
        for root in ("", "relative", str(self.root / "missing"), str(self.usb / "settings.json")):
            with self.subTest(root=root):
                environment = dict(self.environment, IMAGESORTER_PORTABLE_ROOT=root)
                result = self.run_script(PATH_PROBE, environment=environment)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("Portable", result.stderr)
                self.assertFalse((self.usb / "data").exists())
                self.assertFalse(list(self.host.rglob("ImageSorter")))

    def make_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Directory symlinks unavailable: {error}")

    def test_replaced_inherited_root_cannot_redirect_profile(self):
        moved = self.root / "original-usb"
        self.usb.rename(moved)
        self.make_link(self.usb, self.host)
        environment = dict(self.environment, IMAGESORTER_PORTABLE_ROOT=str(self.usb))
        # The launcher is deliberately separate from the now-replaced profile.
        result = subprocess.run([sys.executable, "-c", PATH_PROBE], cwd=self.cwd,
                                env=environment, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Portable", result.stderr)
        self.assertEqual(list(self.host.iterdir()), [])

    def test_inherited_profile_rejects_category_symlink_escape(self):
        self.make_link(self.usb / "data", self.host)
        environment = dict(self.environment, IMAGESORTER_PORTABLE_ROOT=str(self.usb))
        result = self.run_script(PATH_PROBE, environment=environment)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("escapes root", result.stderr)
        self.assertEqual(list(self.host.iterdir()), [])

    def test_helper_environment_keeps_identity_without_restoring_external_python_path(self):
        # Inspect only named, non-secret identity fields through the real pipe.
        probe = """
import json, os, sys
request = json.loads(sys.stdin.readline())
result = dict(request, ok=True, payload_length=0,
              portable_root=os.environ.get("IMAGESORTER_PORTABLE_ROOT"),
              python_path=os.environ.get("PYTHONPATH"),
              qt_plugin_path=os.environ.get("QT_PLUGIN_PATH"))
if request["action"] == "internal-probe":
    import imagesorter
    result["source"] = imagesorter.__file__
print(json.dumps(result))
"""
        body = f"""
            import json, os, sys
            from pathlib import Path
            from imagesorter.component_runtime import _run
            os.environ["PYTHONPATH"] = "untrusted-ambient-python-path"
            os.environ["QT_PLUGIN_PATH"] = "untrusted-ambient-qt-path"
            results = []
            for internal in (False, True):
                request = {{"action": "internal-probe" if internal else "external-probe",
                            "component_id": "core.cpu", "component_version": "base"}}
                result, payload = _run([sys.executable, "-c", {probe!r}], request,
                                       cwd=Path({str(self.cwd)!r}), timeout=10, internal_worker=internal)
                assert payload == b""
                results.append(result)
            print(json.dumps(results))
        """
        external, internal = self.successful_json(self.run_script(body))
        for result in (external, internal):
            self.assertEqual(result["portable_root"], str(self.usb))
            self.assertIsNone(result["qt_plugin_path"])
        self.assertIsNone(external["python_path"])
        self.assertEqual(internal["python_path"], str(SOURCE))
        self.assertEqual(Path(internal["source"]).resolve(), SOURCE / "imagesorter" / "__init__.py")

    def test_bootstrap_rejects_lost_identity_before_component_import(self):
        environment = dict(self.environment, IMAGESORTER_PORTABLE_ROOT=str(self.root / "missing"))
        result = subprocess.run([sys.executable, "-m", "imagesorter.bootstrap", "--component-job", "status"],
                                cwd=self.cwd, env=environment, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Portable profile identity is unavailable", result.stderr)
        self.assertEqual(list(self.host.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "Real shebang execve fixture requires POSIX")
    def test_frozen_runtime_reexec_retains_original_profile(self):
        internal = self.usb / "_internal"
        internal.mkdir()
        (internal / "runtime-fixture").write_text("immutable runtime fixture")
        body = f"""
            import sys
            from pathlib import Path
            sys.frozen = True
            sys.executable = str(Path(__file__).resolve())
            sys._MEIPASS = str(Path(__file__).resolve().parent / "_internal")
            from imagesorter import bootstrap, mutation_service
            def fixture_main(args):
                mutation_service._pin_runtime()
                exec({PATH_PROBE!r})
            mutation_service.main = fixture_main
            raise SystemExit(bootstrap.entry())
        """
        values = self.successful_json(self.run_script(body, executable=True, arguments=["--mutation-service"]))
        self.assert_portable_paths(values)
        self.assertEqual(values["inherited"], str(self.usb))
        pinned = Path(values["executable"]).parent
        self.assertEqual(pinned.parent, self.usb / "data" / "runtime")
        self.assertTrue((pinned / ".complete").is_file())
        self.assertFalse((pinned / "portable.flag").exists())
        self.assertFalse((pinned / "settings.json").exists())
        self.assertFalse((pinned / "data").exists())

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") and importlib.util.find_spec("PIL"),
                         "Real reader requires optional test runtime dependencies")
    def test_real_reader_uses_portable_component_store_and_preserves_image(self):
        body = f"""
            from pathlib import Path
            from PIL import Image
            from imagesorter.reader_process import run_reader
            source = Path({str(self.root / 'original.bmp')!r})
            Image.new("RGB", (7, 5), "red").save(source)
            original = source.read_bytes()
            result, pixels = run_reader({{"action": "decode", "filepath": str(source)}})
            assert (result["width"], result["height"], len(pixels)) == (7, 5, 140), result
            assert source.read_bytes() == original
            exec({PATH_PROBE!r})
        """
        self.assert_portable_paths(self.successful_json(self.run_script(body)))

    @unittest.skipUnless(importlib.util.find_spec("PyQt6"), "Real QProcess requires Qt")
    def test_component_panel_qprocess_inherits_portable_root(self):
        body = f"""
            from PyQt6.QtWidgets import QApplication
            from imagesorter.ui_components import ComponentsPanel
            app = QApplication([])
            panel = ComponentsPanel()
            panel.start(["status"])
            process = panel.process
            assert process.waitForFinished(10000), process.errorString()
            assert process.exitCode() == 0, panel.status_label.text()
            assert panel.status_label.text() == "Component operation completed and verified."
            exec({PATH_PROBE!r})
            panel.close()
        """
        self.assert_portable_paths(self.successful_json(self.run_script(body)))

    @unittest.skipUnless(sys.platform.startswith("linux") and importlib.util.find_spec("PyQt6"),
                         "Portable mutation test requires Linux abstract IPC and Qt")
    def test_real_portable_mutation_service_copies_and_drains(self):
        body = f"""
            import time
            from pathlib import Path
            from PyQt6.QtWidgets import QApplication
            from imagesorter.mutation_client import MutationClient
            app = QApplication([])
            source = Path({str(self.root / 'original.jpg')!r})
            source.write_bytes(b"temporary original fixture")
            destination = Path({str(self.root / 'copies')!r})
            destination.mkdir()
            client = MutationClient()
            results, statuses = [], []
            client.result.connect(results.append)
            client.status.connect(statuses.append)
            try:
                client.submit({{"operation_id": "portable-copy", "action": "copy",
                               "source_path": str(source), "destination_path": str(destination),
                               "task_options": {{}}}})
                deadline = time.monotonic() + 10
                while not results and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                assert results, statuses
                assert results[0]["state"] == "completed", results
            finally:
                client.disconnect()
                if client.process:
                    client.process.wait(timeout=5)
            assert source.read_bytes() == b"temporary original fixture"
            assert (destination / source.name).read_bytes() == source.read_bytes()
            exec({PATH_PROBE!r})
        """
        self.assert_portable_paths(self.successful_json(self.run_script(body)))
        self.assertTrue((self.usb / "data" / "operation_journal.db").is_file())
        self.assertTrue((self.usb / "logs" / "mutation-service.log").is_file())


if __name__ == "__main__":
    unittest.main()
