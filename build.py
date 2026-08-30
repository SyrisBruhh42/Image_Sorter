import subprocess
import sys
import os
from pathlib import Path


DESKTOP_ENTRY = """[Desktop Entry]
Version=1.0
Type=Application
Name=Image Sorter Enterprise
GenericName=Image Triage & AI Tagging Suite
Comment=High-Throughput Image Triage & AI Tagging Suite
Exec=ImageSorter %F
Icon=imagesorter
Terminal=false
Categories=Graphics;Viewer;Photography;
MimeType=image/jpeg;image/png;image/webp;image/bmp;image/gif;image/tiff;
Keywords=image;photo;sorter;triage;exif;ai;tagger;
"""

MIME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-imagesorter-project">
    <comment>Image Sorter Project File</comment>
    <glob pattern="*.isp"/>
  </mime-type>
</mime-info>
"""


def generate_freedesktop_artifacts(output_dir: Path) -> None:
    """Generates standard Freedesktop .desktop and MIME integration files."""
    desktop_path = output_dir / "imagesorter.desktop"
    mime_path = output_dir / "imagesorter-mime.xml"

    with open(desktop_path, "w", encoding="utf-8") as f:
        f.write(DESKTOP_ENTRY)
    print(f"Generated Freedesktop desktop file: {desktop_path}")

    with open(mime_path, "w", encoding="utf-8") as f:
        f.write(MIME_XML)
    print(f"Generated Freedesktop MIME spec: {mime_path}")


def generate_appimage_builder_script(output_dir: Path) -> None:
    """Generates an AppDir setup script for building a standalone Linux AppImage."""
    script_path = output_dir / "build_appimage.sh"
    content = """#!/usr/bin/env bash
set -e

APP_DIR="ImageSorter.AppDir"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/usr/bin"
mkdir -p "$APP_DIR/usr/share/applications"
mkdir -p "$APP_DIR/usr/share/icons/hicolor/256x256/apps"

cp -r dist/ImageSorter/* "$APP_DIR/usr/bin/"
cp dist/imagesorter.desktop "$APP_DIR/usr/share/applications/"
cp dist/imagesorter.desktop "$APP_DIR/"

cat << 'EOF' > "$APP_DIR/AppRun"
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/bin:$LD_LIBRARY_PATH"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
exec "$HERE/usr/bin/ImageSorter" "$@"
EOF

chmod +x "$APP_DIR/AppRun"

if command -v appimagetool >/dev/null 2>&1; then
    appimagetool "$APP_DIR" ImageSorter-x86_64.AppImage
    echo "AppImage created successfully: ImageSorter-x86_64.AppImage"
else
    echo "AppDir prepared at $APP_DIR. Install 'appimagetool' to package into .AppImage binary."
fi
"""
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    print(f"Generated AppImage helper script: {script_path}")


def build_executable() -> None:
    print("Starting build process for Image Sorter Enterprise...")

    root_dir = Path(__file__).resolve().parent
    main_script = root_dir / "src" / "imagesorter" / "main.py"

    if not main_script.exists():
        print(f"Error: Could not locate entrypoint at {main_script}")
        sys.exit(1)

    os.chdir(root_dir)
    models_dir = root_dir / "models"
    models_dir.mkdir(exist_ok=True)

    separator = ";" if sys.platform == "win32" else ":"
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", "ImageSorter",
        "--add-data", f"{models_dir}{separator}models",
        str(main_script)
    ]

    print(f"Running PyInstaller: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
        dist_dir = root_dir / "dist"
        generate_freedesktop_artifacts(dist_dir)
        generate_appimage_builder_script(dist_dir)
        print("\nBuild successful! Outputs located in the 'dist' directory.")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error code {e.returncode}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    build_executable()
