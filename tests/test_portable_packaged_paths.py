"""Stdlib-only package layout tests; native package execution is a separate gate."""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from imagesorter import paths


class PortablePackagedPathTests(unittest.TestCase):
    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        temporary = self.contexts.enter_context(tempfile.TemporaryDirectory(prefix="packaged-paths-"))
        self.root = Path(temporary).resolve()
        self.external = self.root / "USB with spaces"
        self.external.mkdir()
        self.appdir = self.root / "mounted package"
        self.executable = self.appdir / "usr" / "bin" / "ImageSorter"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"fixture executable")
        self.executable.chmod(0o700)
        launcher = self.appdir / "AppRun"
        launcher.write_bytes(b"fixture launcher")
        launcher.chmod(0o700)
        self.artifact = self.external / "Image Sorter.AppImage"
        self.artifact.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02fixture")
        self.host = self.root / "host"
        environment = dict(os.environ)
        for name in ("IMAGESORTER_PROFILE_ROOT", "IMAGESORTER_PORTABLE_ROOT", "APPIMAGE", "APPDIR"):
            environment.pop(name, None)
        for category in ("CONFIG", "DATA", "CACHE", "STATE"):
            environment[f"XDG_{category}_HOME"] = str(self.host / category.lower())
        self.contexts.enter_context(patch.dict(os.environ, environment, clear=True))
        self.contexts.enter_context(patch.object(Path, "home", return_value=self.host))
        self.contexts.enter_context(patch.object(paths, "sys", SimpleNamespace(
            frozen=True, platform="linux", executable=str(self.executable),
            _MEIPASS=str(self.executable.parent / "_internal"))))

    def appimage(self):
        os.environ.update(APPDIR=str(self.appdir), APPIMAGE=str(self.artifact))

    def assert_no_host_state(self):
        self.assertFalse(self.host.exists())

    def symlink(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")

    def test_external_appimage_marker_selects_artifact_directory_not_mounted_code(self):
        self.appimage()
        (self.external / "portable.flag").touch()
        self.assertEqual(paths.get_package_storage_root(), self.external)
        self.assertEqual(paths.get_app_dir(), self.executable.parent)
        self.assertEqual(paths.get_resource_dir(), self.executable.parent / "_internal/imagesorter/resources")
        self.assertEqual(paths.get_settings_path(), self.external / "settings.json")
        for name, getter in (("config", paths.get_config_dir), ("data", paths.get_data_dir),
                             ("cache", paths.get_cache_dir), ("logs", paths.get_logs_dir)):
            self.assertEqual(getter(), self.external / name)
        self.assertFalse((self.appdir / "data").exists())
        self.assert_no_host_state()

    def test_valid_appimage_without_external_marker_stays_native(self):
        self.appimage()
        (self.executable.parent / "portable.flag").touch()
        (self.appdir / "portable.flag").touch()
        self.assertIsNone(paths.get_portable_root())
        self.assertEqual(paths.get_config_dir(), self.host / "config/ImageSorter")

    def test_extracted_appdir_uses_outer_marker(self):
        os.environ["APPDIR"] = str(self.appdir)
        (self.appdir / "portable.flag").touch()
        self.assertEqual(paths.get_portable_root(), self.appdir)
        self.assertEqual(paths.get_settings_path(), self.appdir / "settings.json")
        self.assert_no_host_state()

    def test_invalid_appimage_metadata_refuses_all_storage_getters(self):
        wrong_type = self.external / "directory.AppImage"
        wrong_type.mkdir()
        for invalid in ("", "relative.AppImage", str(self.external / "missing"), str(wrong_type)):
            with self.subTest(value=invalid), patch.dict(os.environ, APPDIR=str(self.appdir), APPIMAGE=invalid):
                for getter in (paths.get_portable_root, paths.get_config_dir, paths.get_settings_path):
                    with self.assertRaisesRegex(OSError, "package identity"):
                        getter()
        self.assert_no_host_state()

    def test_invalid_appdir_metadata_and_missing_pair_refuse(self):
        for invalid in ("", "relative", str(self.root / "missing"), str(self.artifact), str(self.external)):
            with self.subTest(value=invalid), patch.dict(os.environ, APPDIR=invalid, APPIMAGE=str(self.artifact)):
                with self.assertRaisesRegex(OSError, "package identity"):
                    paths.get_config_dir()
        with patch.dict(os.environ, APPIMAGE=str(self.artifact)):
            with self.assertRaisesRegex(OSError, "APPDIR is missing"):
                paths.get_config_dir()
        self.assert_no_host_state()

    def test_wrong_appdir_cannot_redirect_executable(self):
        other = self.root / "other.AppDir"
        other.mkdir()
        launcher = other / "AppRun"
        launcher.write_bytes(b"launcher")
        launcher.chmod(0o700)
        with patch.dict(os.environ, APPDIR=str(other)):
            with self.assertRaisesRegex(OSError, "does not match"):
                paths.get_config_dir()
        self.assert_no_host_state()

    def test_non_appimage_artifact_and_artifact_inside_mount_refuse(self):
        self.appimage()
        self.artifact.write_bytes(b"unrelated ordinary document")
        with self.assertRaisesRegex(OSError, "external type2 AppImage"):
            paths.get_config_dir()
        inside = self.appdir / "ImageSorter.AppImage"
        inside.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02fixture")
        os.environ["APPIMAGE"] = str(inside)
        with self.assertRaisesRegex(OSError, "external type2 AppImage"):
            paths.get_config_dir()
        self.assert_no_host_state()

    def test_symlinked_artifact_mount_or_launcher_cannot_select_storage(self):
        self.appimage()
        for name, target in (("APPIMAGE", self.artifact), ("APPDIR", self.appdir)):
            link = self.root / (name + "-redirect")
            self.symlink(link, target)
            with self.subTest(name=name), patch.dict(os.environ, {name: str(link)}):
                with self.assertRaisesRegex(OSError, "package identity"):
                    paths.get_config_dir()
        launcher = self.appdir / "AppRun"
        launcher.unlink()
        self.symlink(launcher, self.executable)
        with self.assertRaisesRegex(OSError, "package identity"):
            paths.get_config_dir()
        self.assert_no_host_state()

    def test_marker_symlinks_including_dangling_and_directory_refuse(self):
        self.appimage()
        marker = self.external / "portable.flag"
        for target in (self.artifact, self.root / "missing"):
            with self.subTest(target=str(target)):
                self.symlink(marker, target)
                with self.assertRaisesRegex(OSError, "non-symlink"):
                    paths.get_config_dir()
                marker.unlink()
        marker.mkdir()
        with self.assertRaisesRegex(OSError, "non-symlink"):
            paths.get_config_dir()
        self.assert_no_host_state()

    def test_selected_appimage_storage_rejects_category_escape(self):
        self.appimage()
        (self.external / "portable.flag").touch()
        outside = self.root / "outside"
        outside.mkdir()
        self.symlink(self.external / "data", outside)
        with self.assertRaisesRegex(OSError, "escapes root"):
            paths.get_data_dir()
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_no_host_state()

    def test_explicit_and_inherited_selection_precedes_invalid_package_metadata(self):
        os.environ.update(APPDIR="invalid", APPIMAGE="invalid")
        os.environ["IMAGESORTER_PORTABLE_ROOT"] = str(self.external)
        self.assertEqual(paths.get_portable_root(), self.external)
        self.assertEqual(paths.get_config_dir(), self.external / "config")
        explicit = self.root / "explicit"
        os.environ.update(IMAGESORTER_PROFILE_ROOT=str(explicit), IMAGESORTER_PORTABLE_ROOT="invalid")
        self.assertIsNone(paths.get_portable_root())
        self.assertEqual(paths.get_config_dir(), explicit / "config/ImageSorter")
        self.assert_no_host_state()

    def test_invalid_inherited_root_does_not_rediscover_valid_appimage(self):
        self.appimage()
        (self.external / "portable.flag").touch()
        os.environ["IMAGESORTER_PORTABLE_ROOT"] = "invalid"
        with self.assertRaisesRegex(OSError, "Portable profile identity is unavailable"):
            paths.get_config_dir()
        self.assert_no_host_state()

    def test_mac_bundle_marker_is_beside_exact_bundle_shape(self):
        binary = self.external / "Image Sorter.app/Contents/MacOS/ImageSorter"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"bundle fixture")
        paths.sys.platform = "darwin"
        paths.sys.executable = str(binary)
        (self.external / "portable.flag").touch()
        self.assertEqual(paths.get_portable_root(), self.external)
        self.assertEqual(paths.get_app_dir(), binary.parent)
        self.assertEqual(paths.get_settings_path(), self.external / "settings.json")
        self.assert_no_host_state()

    def test_mac_bundle_does_not_use_internal_marker_or_guess_app_ancestors(self):
        binary = self.external / "Image Sorter.app/Contents/MacOS/ImageSorter"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"bundle fixture")
        paths.sys.platform = "darwin"
        paths.sys.executable = str(binary)
        (binary.parent / "portable.flag").touch()
        self.assertIsNone(paths.get_portable_root())
        nested = self.external / "ordinary.app/tools/bin/ImageSorter"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"not a standard bundle")
        paths.sys.executable = str(nested)
        (self.external / "portable.flag").touch()
        self.assertEqual(paths.get_package_storage_root(), nested.parent)
        self.assertIsNone(paths.get_portable_root())

    def test_redirected_mac_bundle_fails_closed(self):
        bundle = self.root / "outside.app"
        binary = bundle / "Contents/MacOS/ImageSorter"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"bundle fixture")
        self.symlink(self.external / "ImageSorter.app", bundle)
        paths.sys.platform = "darwin"
        paths.sys.executable = str(self.external / "ImageSorter.app/Contents/MacOS/ImageSorter")
        with self.assertRaisesRegex(OSError, "package identity"):
            paths.get_config_dir()
        self.assert_no_host_state()

    def test_source_and_windows_onedir_ignore_appimage_environment(self):
        os.environ.update(APPDIR="invalid", APPIMAGE="invalid")
        (self.executable.parent / "portable.flag").touch()
        paths.sys.platform = "win32"
        self.assertEqual(paths.get_portable_root(), self.executable.parent)
        paths.sys.frozen = False
        paths.sys.platform = "linux"
        paths.sys.argv = [str(self.executable)]
        self.assertEqual(paths.get_portable_root(), self.executable.parent)


if __name__ == "__main__":
    unittest.main()
