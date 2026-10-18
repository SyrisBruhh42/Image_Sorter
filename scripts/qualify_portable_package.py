#!/usr/bin/env python3
"""Disposable native frozen review smoke, separate from mutation/release proof.

Copies an exact package into a fresh output directory and launches it twice,
including after moving its containing folder. Never uses --profile-root.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__:
    from .portable_package import MANIFEST, digest, inventory, native_target
else:
    from portable_package import MANIFEST, digest, inventory, native_target


def _unredirected_location(value):
    """Check named entries before resolving; this is not a concurrent-write sandbox."""
    path = Path(value).absolute()
    for entry in (path, *path.parents):
        if entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
            raise ValueError(f'Package and output locations must not use symlinks or junctions: {entry}')
    return path.resolve()


def verify_package(package, expected_identity):
    package = _unredirected_location(package)
    if not package.is_dir():
        raise ValueError('Package must be a regular directory')
    record = json.loads((package / MANIFEST).read_text(encoding='utf-8'))
    if record.get('schema_version') != 1 or record.get('source_identity') != expected_identity:
        raise ValueError('Package source identity does not match the independently supplied build identity')
    if (record.get('target_platform'), record.get('architecture')) != native_target():
        raise ValueError('Package must be qualified on its native OS and architecture')
    actual = inventory(package, windows=record['target_platform'] == 'win32')
    actual.pop(MANIFEST, None)
    if actual != record.get('files') or (package / 'portable.flag').read_bytes() != b'':
        raise ValueError('Package bytes or portable marker differ from their build manifest')
    expected_executable = {'linux': 'ImageSorter', 'win32': 'ImageSorter.exe',
                           'darwin': 'ImageSorter.app/Contents/MacOS/ImageSorter'}[record['target_platform']]
    expected_runtime = 'ImageSorter.app' if record['target_platform'] == 'darwin' else '.'
    if record.get('executable') != expected_executable or record.get('runtime_root') != expected_runtime:
        raise ValueError('Package launcher does not match its native layout')
    executable = package / expected_executable
    if executable.is_symlink() or not executable.is_file():
        raise ValueError('Package launcher must be a regular frozen executable')
    return record


def qualify(package, output, expected_identity, *, backend='offscreen', timeout=40):
    package, output = _unredirected_location(package), _unredirected_location(output)
    if output.exists() or output.is_relative_to(package):
        raise ValueError('Qualification requires a fresh output directory outside the package')
    record = verify_package(package, expected_identity)
    original = inventory(package, windows=record['target_platform'] == 'win32')
    output.mkdir(parents=True)
    result = {'schema_version': 1, 'status': 'RUNNING', 'source_identity': expected_identity,
              'platform': record['target_platform'], 'architecture': record['architecture'],
              'backend': backend, 'launches': [], 'scope': 'native frozen review startup and disposable profile files',
              'not_qualified': ['native picker shell integration', 'registry and OS-managed state',
                                'physical USB/unplug/power-loss', 'mutations and Undo',
                                'signing/notarization', 'network observation', 'release distribution']}
    try:
        from PIL import Image
        image = output / 'fixture image.png'
        Image.new('RGB', (160, 100), (33, 88, 144)).save(image)
        image_before = digest(image)
        host = output / 'disposable host'
        environment = os.environ.copy()
        for key in ('IMAGESORTER_PROFILE_ROOT', 'IMAGESORTER_PORTABLE_ROOT', 'APPDIR', 'APPIMAGE',
                    'PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
            environment.pop(key, None)
        for key in ('HOME', 'USERPROFILE', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
                    'XDG_STATE_HOME', 'APPDATA', 'LOCALAPPDATA', 'TMPDIR', 'TMP', 'TEMP'):
            directory = host / key
            directory.mkdir(parents=True)
            environment[key] = str(directory)
        environment.update(QT_QPA_PLATFORM=backend, ONNXRUNTIME_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1',
                           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
        sentinel = host / 'XDG_CONFIG_HOME/QtProject.conf'
        sentinel.write_text('[sentinel]\nvalue=unchanged\n', encoding='utf-8')
        host_before = inventory(host)
        working = output / 'USB folder with spaces'
        shutil.copytree(package, working, symlinks=True)
        verify_package(working, expected_identity)
        for index in range(2):
            if index:
                relocated = output / 'Relocated USB folder with spaces'
                working.rename(relocated)
                working = relocated
            receipt = working / 'state' / f'readiness-{index}.json'
            command = [str(working / record['executable']), '--diagnostic-receipt', str(receipt),
                       '--acceptance-exit-after-ready', str(image)]
            # Only a disposable review-only GUI is launched. A timeout is a
            # failed check; terminating it cannot qualify graceful shutdown.
            process = subprocess.Popen(command, cwd=output, env=environment, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=6)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=3)
                (output / f'launch-{index}.stderr.log').write_text(stderr, encoding='utf-8')
                raise ValueError('Frozen launch timed out; graceful shutdown not qualified') from None
            (output / f'launch-{index}.stdout.log').write_text(stdout, encoding='utf-8')
            (output / f'launch-{index}.stderr.log').write_text(stderr, encoding='utf-8')
            if process.returncode != 0:
                raise ValueError(f'Frozen launch failed with exit {process.returncode}; see retained logs')
            observed = json.loads(receipt.read_text(encoding='utf-8'))
            ready = next((e for e in observed.get('events', []) if e.get('event') == 'image_presented'), {})
            closed = next((e for e in observed.get('events', []) if e.get('event') == 'gui_shutdown'), {})
            if (observed.get('profile_root') != str(working) or ready.get('build_identity') != expected_identity
                    or ready.get('backend') != backend or ready.get('filepath') != str(image)
                    or (ready.get('width'), ready.get('height')) != (160, 100)):
                raise ValueError('Frozen readiness identity, selected profile or decoded image differs')
            origin = Path(ready.get('module_origin', '')).resolve()
            if not origin.is_relative_to(working / record['runtime_root']):
                raise ValueError('Frozen module origin escaped the copied package')
            if not closed or closed['monotonic'] < ready['monotonic'] or closed['elapsed_ms'] > 6000:
                raise ValueError('Frozen GUI did not report graceful shutdown')
            if inventory(host) != host_before or digest(image) != image_before:
                raise ValueError('Frozen review changed disposable host files or original image')
            for category in ('config', 'data', 'cache', 'logs', 'state'):
                path = working / category
                if path.exists() and (path.is_symlink() or not path.resolve().is_relative_to(working)):
                    raise ValueError('Portable profile directory escaped copied package')
            if not (working / 'cache/temporary').is_dir():
                raise ValueError('Isolated library temporary directory was not created')
            # Runtime files must remain identical; only the selected profile
            # may acquire settings/cache/readiness files.
            runtime_files = inventory(working, windows=record['target_platform'] == 'win32')
            if any(runtime_files.get(name) != value for name, value in original.items()):
                raise ValueError('Frozen launch changed packaged runtime bytes')
            for name in runtime_files.keys() - original.keys():
                if name.split('/')[0] not in {'config', 'data', 'cache', 'logs', 'state'} and name != 'settings.json':
                    raise ValueError(f'Frozen launch wrote outside its profile categories: {name}')
            retained_receipt = output / f'launch-{index}.readiness.json'
            shutil.copy2(receipt, retained_receipt)
            result['launches'].append({'returncode': process.returncode, 'profile_root': str(working),
                                       'receipt': str(retained_receipt), 'receipt_sha256': digest(retained_receipt),
                                       'selected_without_profile_argument': True, 'graceful_shutdown': True,
                                       'disposable_host_files_unchanged': True, 'original_image_unchanged': True})
        if inventory(package, windows=record['target_platform'] == 'win32') != original:
            raise ValueError('Original package changed during qualification')
        result['status'] = 'PASS_PORTABLE_FROZEN_SMOKE'
    except Exception as error:
        result.update(status='FAIL', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        (output / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--build-identity', required=True, type=Path)
    parser.add_argument('--backend', default='offscreen', choices=('offscreen', 'xcb', 'windows', 'cocoa'))
    options = parser.parse_args(argv)
    try:
        qualify(options.package, options.output,
                json.loads(options.build_identity.read_text(encoding='utf-8')), backend=options.backend)
    except (OSError, ValueError, KeyError) as error:
        print(f'Portable qualification failed: {error}', file=sys.stderr)
        return 1
    print(options.output / 'qualification.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
