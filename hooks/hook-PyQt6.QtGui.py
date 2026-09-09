"""Keep upstream Qt dependency discovery except the unadvertised PDF decoder.

Filtering before binary dependency analysis prevents collection of QtPdf. SVG,
JPEG, PNG, WebP, GIF and TIFF plugins retain upstream discovery behavior.
"""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, destination) for source, destination in binaries
            if Path(source).name not in {"libqpdf.so", "qpdf.dll", "libqpdf.dylib"}]
