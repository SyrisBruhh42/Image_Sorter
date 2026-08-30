# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

version_info = None
if sys.platform == "win32":
    try:
        from PyInstaller.utils.win32.versioninfo import (
            VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
        )
        version_info = VSVersionInfo(
            ffi=FixedFileInfo(
                filevers=(1, 0, 0, 0),
                prodvers=(1, 0, 0, 0),
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
                                StringStruct('FileDescription', 'Image Sorter Enterprise'),
                                StringStruct('FileVersion', '1.0.0.0'),
                                StringStruct('InternalName', 'ImageSorter'),
                                StringStruct('LegalCopyright', 'Copyright (c) 2026 SyrisBruhh42'),
                                StringStruct('OriginalFilename', 'ImageSorter.exe'),
                                StringStruct('ProductName', 'Image Sorter Enterprise'),
                                StringStruct('ProductVersion', '1.0.0.0'),
                            ]
                        )
                    ]
                ),
                VarFileInfo([VarStruct('Translation', [1033, 1200])])
            ]
        )
    except Exception:
        version_info = None

icon_path = os.path.join('src', 'imagesorter', 'resources', 'imagesorter.ico')
if not os.path.exists(icon_path):
    icon_path = None

a = Analysis(
    ['src/imagesorter/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('models', 'models'),
        ('src/imagesorter/resources', 'src/imagesorter/resources'),
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='ImageSorter',
)
