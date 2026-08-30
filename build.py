import importlib.util
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


def check_pyinstaller_installed() -> bool:
    """Preflight check to verify PyInstaller module availability."""
    return importlib.util.find_spec("PyInstaller") is not None


def ensure_icon_assets(root_dir: Path) -> Path:
    """Procedurally generates high-resolution icon assets if missing using Pillow."""
    resources_dir = root_dir / "src" / "imagesorter" / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    img_256_path = resources_dir / "imagesorter_256.png"
    img_512_path = resources_dir / "imagesorter_512.png"
    img_default_path = resources_dir / "imagesorter.png"
    ico_path = resources_dir / "imagesorter.ico"

    if not (img_256_path.exists() and img_512_path.exists() and img_default_path.exists() and ico_path.exists()):
        print("Generating procedural icon assets...")
        from PIL import Image, ImageDraw

        def create_app_icon(size: int) -> Image.Image:
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            pad = size // 16
            draw.rounded_rectangle(
                [pad, pad, size - pad, size - pad],
                radius=size // 8,
                fill=(30, 30, 35, 255),
                outline=(42, 130, 218, 255),
                width=max(2, size // 32)
            )
            # Inner stylized photo frame symbol
            inner_pad = size // 4
            draw.rectangle(
                [inner_pad, inner_pad, size - inner_pad, size - inner_pad],
                outline=(255, 255, 255, 220),
                width=max(2, size // 32)
            )
            # Mountain/triangle visual element inside frame
            draw.polygon(
                [
                    (inner_pad + size // 16, size - inner_pad - size // 16),
                    (size // 2, inner_pad + size // 8),
                    (size - inner_pad - size // 16, size - inner_pad - size // 16)
                ],
                fill=(42, 130, 218, 255)
            )
            return img

        img_512 = create_app_icon(512)
        img_512.save(img_512_path)

        img_256 = img_512.resize((256, 256), Image.Resampling.LANCZOS)
        img_256.save(img_256_path)

        # Main default png icon
        img_256.save(img_default_path)

        # Save multi-resolution ICO file
        ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img_512.save(ico_path, format="ICO", sizes=ico_sizes)
        print(f"Generated icons in {resources_dir}")

    return resources_dir


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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/ImageSorter.AppDir"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/usr/bin"
mkdir -p "$APP_DIR/usr/share/applications"
mkdir -p "$APP_DIR/usr/share/icons/hicolor/256x256/apps"

cp -r "$SCRIPT_DIR/ImageSorter"/* "$APP_DIR/usr/bin/"
cp "$SCRIPT_DIR/imagesorter.desktop" "$APP_DIR/usr/share/applications/"
cp "$SCRIPT_DIR/imagesorter.desktop" "$APP_DIR/"

if [ -f "$SCRIPT_DIR/../src/imagesorter/resources/imagesorter.png" ]; then
    cp "$SCRIPT_DIR/../src/imagesorter/resources/imagesorter.png" "$APP_DIR/imagesorter.png"
    cp "$SCRIPT_DIR/../src/imagesorter/resources/imagesorter.png" "$APP_DIR/.DirIcon"
    cp "$SCRIPT_DIR/../src/imagesorter/resources/imagesorter.png" "$APP_DIR/usr/share/icons/hicolor/256x256/apps/"
fi

cat << 'EOF' > "$APP_DIR/AppRun"
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/bin:$LD_LIBRARY_PATH"
export QT_PLUGIN_PATH="$HERE/usr/bin/PyQt6/Qt6/plugins:$HERE/usr/bin/PyQt6/Qt/plugins:$QT_PLUGIN_PATH"
export QT_QPA_PLATFORM_PLUGIN_PATH="$HERE/usr/bin/PyQt6/Qt6/plugins/platforms:$HERE/usr/bin/PyQt6/Qt/plugins/platforms:$QT_QPA_PLATFORM_PLUGIN_PATH"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
exec "$HERE/usr/bin/ImageSorter" "$@"
EOF

chmod +x "$APP_DIR/AppRun"

if command -v appimagetool >/dev/null 2>&1; then
    appimagetool "$APP_DIR" "$SCRIPT_DIR/ImageSorter-x86_64.AppImage"
    echo "AppImage created successfully: $SCRIPT_DIR/ImageSorter-x86_64.AppImage"
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

    # Root detection: locate repository root relative to build.py file location
    root_dir = Path(__file__).resolve().parent
    if not (root_dir / "src" / "imagesorter" / "main.py").exists():
        print(f"Error: Could not locate src/imagesorter/main.py under {root_dir}.")
        sys.exit(1)

    # Preflight check for PyInstaller
    if not check_pyinstaller_installed():
        print(
            "Error: PyInstaller is not installed in the active Python environment.\n"
            "Please install it via: pip install pyinstaller"
        )
        sys.exit(1)

    (root_dir / "models").mkdir(parents=True, exist_ok=True)
    ensure_icon_assets(root_dir)

    spec_path = root_dir / "ImageSorter.spec"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(spec_path)
    ]

    print(f"Running PyInstaller: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True, cwd=root_dir)
        dist_dir = root_dir / "dist"
        generate_freedesktop_artifacts(dist_dir)
        generate_appimage_builder_script(dist_dir)
        print("\nBuild successful! Outputs located in the 'dist' directory.")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error code {e.returncode}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    build_executable()
