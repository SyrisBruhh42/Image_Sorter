# -*- mode: python ; coding: utf-8 -*-
import sys
import os
import json
from pathlib import Path

_root_dir = Path(os.path.abspath(SPECPATH)) if 'SPECPATH' in globals() else Path.cwd()
SRC_PATH = str(_root_dir / 'src')
sys.path.insert(0, str(_root_dir))
from build_support import source_identity, verify_frozen_modules
_build_identity = source_identity(_root_dir)
_identity_path = _root_dir / 'build' / 'attestation' / 'build_identity.json'
_identity_path.parent.mkdir(parents=True, exist_ok=True)
_identity_path.write_text(json.dumps(_build_identity, sort_keys=True, indent=2) + '\n')

_version_namespace = {}
exec((_root_dir / 'src' / 'imagesorter' / '__init__.py').read_text(encoding='utf-8'), _version_namespace)
APP_VERSION = _version_namespace['__version__']
_numeric_version = APP_VERSION.split('+', 1)[0].split('dev', 1)[0].rstrip('.')
_version_parts = [int(part) for part in _numeric_version.split('.')]
VERSION_TUPLE = tuple((_version_parts + [0, 0, 0, 0])[:4])

block_cipher = None

version_info = None
if sys.platform == "win32":
    try:
        from PyInstaller.utils.win32.versioninfo import (
            VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
        )
        version_info = VSVersionInfo(
            ffi=FixedFileInfo(
                filevers=VERSION_TUPLE,
                prodvers=VERSION_TUPLE,
                mask=0x3f,
                flags=0x0,
                OS=0x40004,
                fileType=0x1,
                subtype=0x0,
                date=(0, 0)
            ),
            kids=[
                StringFileInfo(
                    [
                        StringTable(
                            '040904B0',
                            [
                                StringStruct('CompanyName', 'SyrisBruhh42'),
                                StringStruct('FileDescription', 'Image Sorter'),
                                StringStruct('FileVersion', APP_VERSION),
                                StringStruct('InternalName', 'ImageSorter'),
                                StringStruct('LegalCopyright', 'Copyright (c) 2026 SyrisBruhh42'),
                                StringStruct('OriginalFilename', 'ImageSorter.exe'),
                                StringStruct('ProductName', 'Image Sorter'),
                                StringStruct('ProductVersion', APP_VERSION),
                            ]
                        )
                    ]
                ),
                VarFileInfo([VarStruct('Translation', [1033, 1200])])
            ]
        )
    except Exception:
        version_info = None

icon_path = os.path.join(SRC_PATH, 'imagesorter', 'resources', 'imagesorter.ico')
if not os.path.exists(icon_path):
    icon_path = None

a = Analysis(
    [os.path.join(str(_root_dir), 'run_app.py')],
    pathex=[SRC_PATH],
    binaries=[],
    datas=[
        (os.path.join(SRC_PATH, 'imagesorter', 'resources'), 'imagesorter/resources'),
        (str(_identity_path), 'imagesorter/resources'),
    ],
    hiddenimports=[
        'onnxruntime',
        'piexif',
        'send2trash',
        'psutil',
        'PIL',
        'PIL.Image',
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtSvg',
        'PyQt6.QtTest',
        'imagesorter',
        'imagesorter.main',
        'imagesorter.launch_requests',
        'imagesorter.components',
        'imagesorter.metadata_io',
        'imagesorter.bootstrap',
        'imagesorter.reader_job',
        'imagesorter.mutation_service',
        'imagesorter.operation_engine',
        'imagesorter.component_jobs',
        'imagesorter.component_worker',
        'imagesorter.ai_preprocessing',
        'imagesorter.apng_frames',
        'imagesorter.native_scenarios',
    ],
    hookspath=[str(_root_dir / 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# An editable development install can precede pathex in PyInstaller's graph.
# Bind every application module to this exact captured source tree, not ambient
# imports that happen to expose an identically named package.
verify_frozen_modules(_root_dir, _build_identity, a.pure)
if any(Path(_destination).name in {'libqpdf.so', 'libQt6Pdf.so.6'} for _destination, *_rest in a.binaries):
    raise RuntimeError('Unadvertised PDF decoding re-entered the application runtime')

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ImageSorter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info if sys.platform == "win32" else None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ImageSorter',
)
if source_identity(_root_dir) != _build_identity:
    raise RuntimeError('Source changed during frozen application build')
