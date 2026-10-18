"""Storage-boundary regression tests; no GUI, real photos or native profile use."""
from __future__ import annotations

import os
from contextlib import ExitStack
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from imagesorter import paths


class PortableIsolationTests(unittest.TestCase):
    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.usb = self.root / "usb"
        self.usb.mkdir()
        (self.usb / "portable.flag").touch()
        environment = dict(os.environ)
        environment.pop("IMAGESORTER_PROFILE_ROOT", None)
        for category in ("CONFIG", "DATA", "CACHE", "STATE"):
            environment[f"XDG_{category}_HOME"] = str(self.root / category.lower())
        environment["APPDATA"] = str(self.root / "roaming")
        environment["LOCALAPPDATA"] = str(self.root / "local")
        self.contexts.enter_context(patch.dict(os.environ, environment, clear=True))
        self.contexts.enter_context(patch.object(paths, "get_app_dir", return_value=self.usb))
        self.contexts.enter_context(patch.object(Path, "home", return_value=self.root / "home"))
        self.contexts.enter_context(patch.object(paths.tempfile, "gettempdir",
                                      side_effect=AssertionError("Unexpected host temporary fallback")))

    def test_existing_portable_layout_and_settings_survive_all_platform_selectors(self):
        settings = self.usb / "settings.json"
        settings.write_text('{"retained":true}', encoding="utf-8")
        getters = {"config": paths.get_config_dir, "data": paths.get_data_dir,
                   "cache": paths.get_cache_dir, "logs": paths.get_logs_dir,
                   "data/components": paths.get_components_dir,
                   "cache/component-downloads": paths.get_component_cache_dir}
        for platform in ("linux", "win32", "darwin"):
            with self.subTest(platform=platform), patch.object(paths, "sys", SimpleNamespace(platform=platform)):
                for relative, getter in getters.items():
                    self.assertEqual(getter(), self.usb / relative)
                self.assertEqual(paths.get_settings_path(), settings)
                self.assertEqual(settings.read_text(encoding="utf-8"), '{"retained":true}')
        self.assertEqual(set(self.root.iterdir()), {self.usb})

    def test_unavailable_portable_directories_never_select_host_fallback(self):
        for name, getter in (("config", paths.get_config_dir), ("data", paths.get_data_dir),
                             ("cache", paths.get_cache_dir), ("logs", paths.get_logs_dir)):
            with self.subTest(category=name):
                obstruction = self.usb / name
                obstruction.write_bytes(b"retain existing file")
                with self.assertRaisesRegex(OSError, "Portable profile is unavailable"):
                    getter()
                self.assertEqual(obstruction.read_bytes(), b"retain existing file")
        self.assertEqual(set(self.root.iterdir()), {self.usb})

    def test_unwritable_portable_root_refuses_settings_and_directory_paths(self):
        with patch.object(paths, "_test_directory_writable", return_value=False):
            for getter in (paths.get_config_dir, paths.get_data_dir, paths.get_cache_dir,
                           paths.get_logs_dir, paths.get_settings_path):
                with self.subTest(getter=getter.__name__):
                    with self.assertRaisesRegex(OSError, "Portable profile is unavailable"):
                        getter()
        self.assertEqual(set(self.root.iterdir()), {self.usb})

    def test_symlinked_portable_categories_and_nested_components_cannot_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        for relative, getter in (("config", paths.get_config_dir), ("data", paths.get_data_dir),
                                 ("cache", paths.get_cache_dir), ("logs", paths.get_logs_dir),
                                 ("data/components", paths.get_components_dir),
                                 ("cache/component-downloads", paths.get_component_cache_dir)):
            with self.subTest(category=relative):
                link = self.usb / relative
                link.parent.mkdir(exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                try:
                    with self.assertRaisesRegex(OSError, "Portable profile path escapes root"):
                        getter()
                    self.assertEqual(list(outside.iterdir()), [])
                finally:
                    link.unlink()

    def test_external_settings_symlink_is_not_read_or_replaced(self):
        outside = self.root / "outside-settings.json"
        outside.write_bytes(b"original settings")
        link = self.usb / "settings.json"
        link.symlink_to(outside)
        with self.assertRaisesRegex(OSError, "Portable profile path escapes root"):
            paths.get_settings_path()
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(), b"original settings")

    def test_explicit_profile_keeps_precedence_and_existing_layout(self):
        explicit = self.root / "explicit"
        with patch.dict(os.environ, {"IMAGESORTER_PROFILE_ROOT": str(explicit)}):
            self.assertFalse(paths.is_portable_mode())
            self.assertEqual(paths.get_settings_path(), explicit / "config/ImageSorter/settings.json")
            self.assertEqual(paths.get_data_dir(), explicit / "data/ImageSorter")
        self.assertEqual(list(self.usb.iterdir()), [self.usb / "portable.flag"])


if __name__ == "__main__":
    unittest.main()
