"""Real archive/layout checks; simulated layouts are not native OS proof."""
from __future__ import annotations

import json
import plistlib
import tarfile
import zipfile
from types import SimpleNamespace

import pytest

from scripts import portable_package as portable
from scripts import qualify_portable_package as qualification

IDENTITY = {'schema_version': 1, 'source_sha': 'a' * 40, 'source_tree': 'b' * 40,
            'dirty': False, 'source_files': {'fixture.py': 'c' * 64}}


def fixture_runtime(tmp_path, target):
    runtime, launcher = portable.runtime_layout(tmp_path / 'dist', target)
    (runtime / launcher).parent.mkdir(parents=True)
    (runtime / launcher).write_bytes(b'disposable native layout fixture, not executable')
    (runtime / launcher).chmod(0o755)
    internal = runtime / ('Contents/Frameworks' if target == 'darwin' else '_internal')
    internal.mkdir()
    (internal / 'library').write_bytes(b'runtime library')
    if target == 'darwin':
        (runtime / 'Contents/Info.plist').write_bytes(plistlib.dumps(
            {'CFBundleExecutable': 'ImageSorter', 'CFBundlePackageType': 'APPL'}))
    return runtime


@pytest.mark.parametrize('target', ['linux', 'win32', 'darwin'])
def test_native_archive_roundtrip_contains_marker_and_exact_runtime(tmp_path, target):
    runtime = fixture_runtime(tmp_path, target)
    before = portable.inventory(runtime)
    package, archive, checksum = portable.create_portable_package(runtime, tmp_path / 'portable', target, 'x86_64', IDENTITY)
    assert portable.inventory(runtime) == before
    assert checksum.read_text() == f'{portable.digest(archive)}  {archive.name}\n'
    unpacked = tmp_path / 'unpacked'
    if target == 'win32':
        with zipfile.ZipFile(archive) as source:
            source.extractall(unpacked)
    else:
        with tarfile.open(archive) as source:
            # Trusted archive produced in this test; no external extraction.
            source.extractall(unpacked, filter='data')
    assert portable.inventory(unpacked / package.name) == portable.inventory(package)
    manifest = json.loads((package / portable.MANIFEST).read_text())
    assert manifest['source_identity'] == IDENTITY
    assert manifest['target_platform'] == target
    assert (package / manifest['executable']).is_file()
    assert manifest['files']['portable.flag']['bytes'] == 0
    if target == 'darwin':
        assert not (package / 'ImageSorter.app/portable.flag').exists()


def test_app_bundle_preserves_internal_framework_symlinks_in_archive(tmp_path):
    runtime = fixture_runtime(tmp_path, 'darwin')
    link = runtime / 'Contents/Frameworks/current'
    try:
        link.symlink_to('library')
    except OSError as error:
        pytest.skip(f'Host cannot create a disposable symlink: {error}')
    package, archive, _ = portable.create_portable_package(runtime, tmp_path / 'portable', 'darwin', 'arm64', IDENTITY)
    with tarfile.open(archive) as source:
        entry = source.getmember('portable/ImageSorter.app/Contents/Frameworks/current')
        assert entry.issym() and entry.linkname == 'library'
    assert (package / 'ImageSorter.app/Contents/Frameworks/current').is_symlink()


@pytest.mark.parametrize('target', ['win32', 'darwin', 'linux'])
def test_profile_state_and_existing_output_are_never_repackaged_or_replaced(tmp_path, target):
    runtime = fixture_runtime(tmp_path, target)
    (runtime / 'settings.json').write_text('private existing settings')
    with pytest.raises(ValueError, match='profile state'):
        portable.create_portable_package(runtime, tmp_path / 'portable', target, 'x86_64', IDENTITY)
    assert not (tmp_path / 'portable').exists()
    (runtime / 'settings.json').unlink()
    destination = tmp_path / 'portable'
    destination.mkdir()
    sentinel = destination / 'settings.json'
    sentinel.write_text('retained profile')
    with pytest.raises(ValueError, match='already exists'):
        portable.create_portable_package(runtime, destination, target, 'x86_64', IDENTITY)
    assert sentinel.read_text() == 'retained profile'


def test_escaping_or_windows_symlink_is_rejected_before_copy(tmp_path):
    runtime = fixture_runtime(tmp_path, 'win32')
    external = tmp_path / 'private.txt'
    external.write_text('never package')
    try:
        (runtime / '_internal/link').symlink_to(external)
    except OSError as error:
        pytest.skip(f'Host cannot create a disposable symlink: {error}')
    with pytest.raises(ValueError, match='package link'):
        portable.create_portable_package(runtime, tmp_path / 'portable', 'win32', 'x86_64', IDENTITY)
    with pytest.raises(ValueError, match='package link'):
        portable.inventory(runtime)
    assert not (tmp_path / 'portable').exists()


def test_qualification_requires_exact_identity_bytes_platform_and_launcher(tmp_path, monkeypatch):
    runtime = fixture_runtime(tmp_path, 'linux')
    package, _, _ = portable.create_portable_package(runtime, tmp_path / 'portable', 'linux', 'x86_64', IDENTITY)
    monkeypatch.setattr(qualification, 'native_target', lambda: ('linux', 'x86_64'))
    assert qualification.verify_package(package, IDENTITY)['executable'] == 'ImageSorter'
    with pytest.raises(ValueError, match='identity'):
        qualification.verify_package(package, dict(IDENTITY, source_tree='d' * 40))
    monkeypatch.setattr(qualification, 'native_target', lambda: ('darwin', 'x86_64'))
    with pytest.raises(ValueError, match='native OS'):
        qualification.verify_package(package, IDENTITY)
    monkeypatch.setattr(qualification, 'native_target', lambda: ('linux', 'x86_64'))
    (package / '_internal/library').write_bytes(b'changed')
    with pytest.raises(ValueError, match='bytes'):
        qualification.verify_package(package, IDENTITY)


@pytest.mark.parametrize('produced_target', ['darwin', 'linux'])
def test_mac_builder_requires_app_output_not_plain_onedir(tmp_path, monkeypatch, produced_target):
    import build_desktop as build
    source = tmp_path / 'src/imagesorter/main.py'
    source.parent.mkdir(parents=True)
    source.write_text('# synthetic build input\n')
    monkeypatch.setattr(build, '__file__', str(tmp_path / 'build_desktop.py'))
    monkeypatch.setattr(build, 'sys', SimpleNamespace(platform='darwin', executable='fixture-python'))
    monkeypatch.setattr(build, 'native_target', lambda: ('darwin', 'arm64'))
    monkeypatch.setattr(build, 'check_pyinstaller_installed', lambda: True)
    monkeypatch.setattr(build, 'ensure_icon_assets', lambda root: root)
    def builder(command, **kwargs):
        assert command[:3] == ['fixture-python', '-m', 'PyInstaller']
        assert kwargs['env']['PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR'] == '1'
        fixture_runtime(tmp_path, produced_target)
    monkeypatch.setattr(build.subprocess, 'run', builder)
    if produced_target == 'linux':
        with pytest.raises(build.BuildError, match='did not produce'):
            build.build_executable()
    else:
        assert build.build_executable() == [tmp_path / 'dist/ImageSorter.app']
    assert not (tmp_path / 'dist/imagesorter.desktop').exists()
