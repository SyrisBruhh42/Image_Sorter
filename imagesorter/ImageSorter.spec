# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

# Get the directory where the spec file is located
spec_dir = os.path.dirname(os.path.abspath(SPEC))

# Determine if we are building standalone (onefile) or onedir
# We will use an environment variable 'BUILD_TYPE' which should be set to 'onefile' or 'onedir'
build_type = os.environ.get('BUILD_TYPE', 'onedir')
is_onefile = build_type == 'onefile'

# Paths
src_main = os.path.join(spec_dir, 'src', 'main.py')
models_dir = os.path.join(spec_dir, 'models')

# Ensure models directory exists
os.makedirs(models_dir, exist_ok=True)
Path(os.path.join(models_dir, '.gitkeep')).touch(exist_ok=True)

# Data files to bundle
datas = [
    (models_dir, 'models'),
]

# Optional files if they exist in the root of the project
for optional_file in ['settings.json', 'README.md', 'LICENSE']:
    file_path = os.path.join(spec_dir, optional_file)
    if os.path.exists(file_path):
        datas.append((file_path, '.'))

# Hidden imports required by the application
hiddenimports = [
    'onnxruntime',
    'piexif',
    'send2trash',
    'psutil',
    'PIL',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'numpy'
]

a = Analysis(
    [src_main],
    pathex=[spec_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# Metadata file for Windows version info
version_file = os.path.join(spec_dir, 'version_info.txt')
if not os.path.exists(version_file):
    # This shouldn't happen as we'll make sure it's created, but good to have fallback
    version_file = None

if is_onefile:
    # Standalone (onefile) build
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='ImageSorter',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        version=version_file,
    )
else:
    # One directory build (onedir)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='ImageSorter',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        version=version_file,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='ImageSorter',
    )