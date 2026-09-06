from __future__ import annotations

import hashlib
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
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
  </mime-type>
</mime-info>
"""

# Pinned appimagetool x86_64 SHA-256 checksum (AppImageKit Continuous Release)
APPIMAGETOOL_X86_64_URL = "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGETOOL_X86_64_SHA256 = "b90f4a8b18967545fda78a445b27680a1642f1ef9488ced28b65398f2be7add2"


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
                width=max(2, size // 32),
            )
            inner_pad = size // 4
            draw.rectangle(
                [inner_pad, inner_pad, size - inner_pad, size - inner_pad],
                outline=(255, 255, 255, 220),
                width=max(2, size // 32),
            )
            draw.polygon(
                [
                    (inner_pad + size // 16, size - inner_pad - size // 16),
                    (size // 2, inner_pad + size // 8),
                    (size - inner_pad - size // 16, size - inner_pad - size // 16),
                ],
                fill=(42, 130, 218, 255),
            )
            return img

        img_512 = create_app_icon(512)
        img_512.save(img_512_path)

        img_256 = img_512.resize((256, 256), Image.Resampling.LANCZOS)
        img_256.save(img_256_path)
        img_256.save(img_default_path)

        ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img_512.save(ico_path, format="ICO", sizes=ico_sizes)
        print(f"Generated icons in {resources_dir}")

    return resources_dir


def generate_freedesktop_artifacts(output_dir: Path) -> None:
    """Generates standard Freedesktop .desktop and MIME spec files."""
    desktop_path = output_dir / "imagesorter.desktop"
    mime_path = output_dir / "imagesorter-mime.xml"

    with open(desktop_path, "w", encoding="utf-8") as f:
        f.write(DESKTOP_ENTRY)
    print(f"Generated Freedesktop desktop file: {desktop_path}")

    with open(mime_path, "w", encoding="utf-8") as f:
        f.write(MIME_XML)
    print(f"Generated Freedesktop MIME spec: {mime_path}")


def prepare_appdir(dist_dir: Path, root_dir: Path) -> Path:
    """Prepares standard AppDir filesystem hierarchy for AppImage packaging."""
    app_dir = dist_dir / "ImageSorter.AppDir"
    if app_dir.exists():
        shutil.rmtree(app_dir)

    bin_dir = app_dir / "usr" / "bin"
    apps_dir = app_dir / "usr" / "share" / "applications"
    icons_dir = app_dir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"

    bin_dir.mkdir(parents=True, exist_ok=True)
    apps_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)

    exe_dir = dist_dir / "ImageSorter"
    if exe_dir.exists():
        for item in exe_dir.iterdir():
            target = bin_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

    desktop_src = dist_dir / "imagesorter.desktop"
    if desktop_src.exists():
        shutil.copy2(desktop_src, apps_dir / "imagesorter.desktop")
        shutil.copy2(desktop_src, app_dir / "imagesorter.desktop")

    icon_src = root_dir / "src" / "imagesorter" / "resources" / "imagesorter.png"
    if icon_src.exists():
        shutil.copy2(icon_src, app_dir / "imagesorter.png")
        shutil.copy2(icon_src, app_dir / ".DirIcon")
        shutil.copy2(icon_src, icons_dir / "imagesorter.png")

    app_run_content = """#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/bin:$LD_LIBRARY_PATH"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
exec "$HERE/usr/bin/ImageSorter" "$@"
"""
    app_run_path = app_dir / "AppRun"
    with open(app_run_path, "w", encoding="utf-8") as f:
        f.write(app_run_content)
    os.chmod(app_run_path, 0o755)

    return app_dir


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
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
exec "$HERE/usr/bin/ImageSorter" "$@"
EOF

chmod +x "$APP_DIR/AppRun"

ARCH="$(uname -m)"
if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
    APPIMAGE_NAME="ImageSorter-x86_64.AppImage"
else
    APPIMAGE_NAME="ImageSorter-${ARCH}.AppImage"
fi

if command -v appimagetool >/dev/null 2>&1; then
    appimagetool "$APP_DIR" "$SCRIPT_DIR/$APPIMAGE_NAME"
    echo "AppImage created successfully: $SCRIPT_DIR/$APPIMAGE_NAME"
else
    echo "AppDir prepared at $APP_DIR. Run 'python3 build.py --appimage' to build the binary."
fi
"""
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    print(f"Generated AppImage helper script: {script_path}")


def get_appimagetool_executable(root_dir: Path) -> Path | None:
    """Locates or downloads a checksum-verified appimagetool binary."""
    tool_bin = shutil.which("appimagetool")
    if tool_bin:
        return Path(tool_bin)

    cache_dir = root_dir / ".cache" / "build_tools"
    cache_dir.mkdir(parents=True, exist_ok=True)

    arch = platform.machine().lower()
    if arch not in ("x86_64", "amd64"):
        print(f"Warning: Automatic appimagetool download only supported on x86_64, got '{arch}'.")
        return None

    local_tool = cache_dir / "appimagetool-x86_64.AppImage"

    def is_valid_tool(path: Path) -> bool:
        if not path.exists():
            return False
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest() == APPIMAGETOOL_X86_64_SHA256

    if is_valid_tool(local_tool):
        return local_tool

    print(f"Downloading checksum-verified appimagetool from {APPIMAGETOOL_X86_64_URL}...")
    try:
        tmp_tool = local_tool.with_suffix(".tmp")
        urllib.request.urlretrieve(APPIMAGETOOL_X86_64_URL, tmp_tool)
        os.chmod(tmp_tool, 0o755)
        if is_valid_tool(tmp_tool):
            tmp_tool.replace(local_tool)
            return local_tool
        else:
            print("Error: SHA-256 verification failed for downloaded appimagetool!")
            if tmp_tool.exists():
                tmp_tool.unlink()
            return None
    except Exception as e:
        print(f"Failed to download appimagetool: {e}")
        return None


def build_appimage(root_dir: Path, dist_dir: Path) -> None:
    """Builds a standalone Linux AppImage from prepared AppDir."""
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        appimage_filename = "ImageSorter-x86_64.AppImage"
    else:
        appimage_filename = f"ImageSorter-{arch}.AppImage"

    app_dir = prepare_appdir(dist_dir, root_dir)
    appimage_output = dist_dir / appimage_filename

    tool_path = get_appimagetool_executable(root_dir)
    if not tool_path:
        print(
            "Error: 'appimagetool' was not found on PATH and could not be fetched.\n"
            "An AppDir has been prepared at dist/ImageSorter.AppDir."
        )
        return

    cmd = [str(tool_path), str(app_dir), str(appimage_output)]
    env = os.environ.copy()
    env["ARCH"] = "x86_64" if arch in ("x86_64", "amd64") else arch
    env["APPIMAGE_EXTRACT_AND_RUN"] = "1"

    print(f"Running appimagetool: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, cwd=dist_dir, env=env)
        print(f"\nAppImage successfully created at: {appimage_output}")
    except subprocess.CalledProcessError as e:
        print(f"\nAppImage build failed with exit code {e.returncode}")


def build_executable(build_appimage_flag: bool = False) -> None:
    print("Starting build process for Image Sorter Enterprise...")

    root_dir = Path(__file__).resolve().parent
    if not (root_dir / "src" / "imagesorter" / "main.py").exists():
        print(f"Error: Could not locate src/imagesorter/main.py under {root_dir}.")
        sys.exit(1)

    if not check_pyinstaller_installed():
        print(
            "Error: PyInstaller is not installed in the active Python environment.\n"
            "Please install it via: pip install pyinstaller"
        )
        sys.exit(1)

    (root_dir / "models").mkdir(parents=True, exist_ok=True)
    ensure_icon_assets(root_dir)

    spec_path = root_dir / "ImageSorter.spec"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec_path)]

    print(f"Running PyInstaller: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True, cwd=root_dir)
        dist_dir = root_dir / "dist"
        generate_freedesktop_artifacts(dist_dir)
        generate_appimage_builder_script(dist_dir)

        if build_appimage_flag:
            if not sys.platform.startswith("linux"):
                print("Warning: --appimage build flag requested on non-Linux platform; skipping AppImage generation.")
            else:
                build_appimage(root_dir, dist_dir)

        print("\nBuild successful! Outputs located in the 'dist' directory.")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error code {e.returncode}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    appimage_requested = "--appimage" in sys.argv
    build_executable(build_appimage_flag=appimage_requested)
