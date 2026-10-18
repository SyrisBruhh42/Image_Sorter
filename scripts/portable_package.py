"""Native portable package layout and byte inventory; no application imports."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

MANIFEST = 'portable-package.json'
PROFILE_NAMES = {'portable.flag', 'settings.json', 'config', 'data', 'cache', 'logs', 'state', MANIFEST, 'PORTABLE-README.txt'}


def native_target():
    target = 'linux' if sys.platform.startswith('linux') else sys.platform
    if target not in {'linux', 'win32', 'darwin'}:
        raise ValueError(f'Unsupported native package platform: {sys.platform}')
    machine = platform.machine().lower()
    architecture = {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)
    if not re.fullmatch(r'[a-z0-9_-]+', architecture):
        raise ValueError('Unable to identify native build architecture')
    return target, architecture


def runtime_layout(dist: Path, target: str):
    if target == 'darwin':
        return dist / 'ImageSorter.app', Path('Contents/MacOS/ImageSorter')
    if target == 'win32' or target.startswith('linux'):
        return dist / 'ImageSorter', Path('ImageSorter.exe' if target == 'win32' else 'ImageSorter')
    raise ValueError(f'Unsupported package platform: {target}')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def inventory(root: Path, *, windows=False):
    """Record only regular files and internal relative links; never follow dirs."""
    root = root.resolve(strict=True)
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(path)
                if windows or Path(link).is_absolute() or not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError(f'Unsupported or escaping package link: {relative}')
                result[relative] = {'link': link}
            elif stat.S_ISREG(info.st_mode):
                result[relative] = {'sha256': digest(path), 'bytes': info.st_size}
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError(f'Non-regular package member: {relative}')
    return result


def create_portable_package(runtime: Path, package: Path, target: str, architecture: str, identity: dict):
    """Copy a freshly built runtime, preserving macOS links and existing profiles."""
    runtime = runtime.resolve(strict=True)
    if target not in {'linux', 'win32', 'darwin'}:
        raise ValueError('Unsupported portable target')
    _, executable = runtime_layout(runtime.parent, target)
    if not (runtime / executable).is_file():
        raise ValueError('Native frozen executable is missing')
    if any((runtime / name).exists() or (runtime / name).is_symlink() for name in PROFILE_NAMES):
        raise ValueError('Runtime contains portable profile state; rebuild before packaging')
    if target == 'darwin':
        info = plistlib.loads((runtime / 'Contents/Info.plist').read_bytes())
        if info.get('CFBundleExecutable') != 'ImageSorter' or info.get('CFBundlePackageType') != 'APPL':
            raise ValueError('macOS app bundle metadata does not match its executable')
    before = inventory(runtime, windows=target == 'win32')
    archive = package.with_name(package.name + ('.zip' if target == 'win32' else '.tar.gz'))
    checksum = archive.with_name(archive.name + '.sha256')
    if any(path.exists() or path.is_symlink() for path in (package, archive, checksum)):
        raise ValueError('Portable output already exists; retain it and choose a fresh build directory')
    package.mkdir(parents=True)
    if target == 'darwin':
        copied = package / 'ImageSorter.app'
        shutil.copytree(runtime, copied, symlinks=True)
        executable = Path('ImageSorter.app') / executable
    else:
        copied = package
        shutil.copytree(runtime, copied, symlinks=True, dirs_exist_ok=True)
    if inventory(copied, windows=target == 'win32') != before or inventory(runtime, windows=target == 'win32') != before:
        raise ValueError('Frozen runtime changed during portable copy; partial output preserved')
    (package / 'portable.flag').write_bytes(b'')
    launch = 'ImageSorter.app' if target == 'darwin' else executable.as_posix()
    (package / 'PORTABLE-README.txt').write_text(
        f'Image Sorter portable local qualification build\n\nOpen {launch} from this extracted folder. '
        'Keep portable.flag beside it; settings and caches stay in this folder. '
        'The folder must be writable. No installation is required.\n\n'
        'Windows and macOS currently allow image review and settings only. '
        'Sorting, metadata writes and Undo remain unavailable pending native durability work.\n\n'
        'This local artifact is not a qualified release. Native desktop, removable-media, '
        'signing/notarization and dependency-license distribution gates remain separate.\n', encoding='utf-8')
    record = {'schema_version': 1, 'target_platform': target, 'architecture': architecture,
              'executable': executable.as_posix(), 'runtime_root': 'ImageSorter.app' if target == 'darwin' else '.',
              'source_identity': identity, 'files': inventory(package, windows=target == 'win32')}
    (package / MANIFEST).write_text(json.dumps(record, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    if target == 'win32':
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(package.rglob('*')):
                output.write(path, (Path(package.name) / path.relative_to(package)).as_posix())
    else:
        with tarfile.open(archive, 'x:gz', dereference=False) as output:
            output.add(package, arcname=package.name)
    checksum.write_text(f'{digest(archive)}  {archive.name}\n', encoding='utf-8')
    return [package, archive, checksum]
