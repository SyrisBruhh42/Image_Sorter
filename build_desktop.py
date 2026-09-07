from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

DESKTOP_ENTRY = """[Desktop Entry]
Version=1.0
Type=Application
Name=Image Sorter
GenericName=Desktop Image Sorting
Comment=Keyboard-first image sorting with optional local AI tagging
Exec=ImageSorter %F
Icon=imagesorter
Terminal=false
Categories=Graphics;Viewer;Photography;
MimeType=image/jpeg;image/png;image/webp;image/bmp;image/gif;image/tiff;
Keywords=image;photo;sorter;triage;exif;tagger;
"""

APPIMAGETOOL_VERSION = "1.9.1"
APPIMAGETOOL_X86_64_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/"
    f"{APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
)
APPIMAGETOOL_X86_64_SHA256 = (
    "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
)


class BuildError(RuntimeError):
    """Raised when a requested artifact cannot be produced or verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_pyinstaller_installed() -> bool:
    return importlib.util.find_spec("PyInstaller") is not None


def ensure_icon_assets(root_dir: Path) -> Path:
    """Generate deterministic local icons when authored assets are absent."""
    resources_dir = root_dir / "src" / "imagesorter" / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "256": resources_dir / "imagesorter_256.png",
        "512": resources_dir / "imagesorter_512.png",
        "default": resources_dir / "imagesorter.png",
        "ico": resources_dir / "imagesorter.ico",
    }
    if all(path.exists() for path in paths.values()):
        return resources_dir

    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [32, 32, 480, 480], radius=64, fill=(30, 30, 35, 255),
        outline=(42, 130, 218, 255), width=16,
    )
    draw.rectangle([128, 128, 384, 384], outline=(255, 255, 255, 220), width=16)
    draw.polygon([(160, 352), (256, 192), (352, 352)], fill=(42, 130, 218, 255))
    image.save(paths["512"])
    image_256 = image.resize((256, 256), Image.Resampling.LANCZOS)
    image_256.save(paths["256"])
    image_256.save(paths["default"])
    image.save(
        paths["ico"],
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return resources_dir


def generate_freedesktop_artifacts(output_dir: Path) -> None:
    """Write the desktop entry used by both AppDir and installations."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "imagesorter.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")


def prepare_appdir(dist_dir: Path, root_dir: Path) -> Path:
    """Create an AppDir from the verified PyInstaller onedir output."""
    executable_dir = dist_dir / "ImageSorter"
    executable = executable_dir / "ImageSorter"
    if not executable.is_file():
        raise BuildError(f"PyInstaller executable is missing: {executable}")

    app_dir = dist_dir / "ImageSorter.AppDir"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    bin_dir = app_dir / "usr" / "bin"
    applications_dir = app_dir / "usr" / "share" / "applications"
    icon_dir = app_dir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    for directory in (bin_dir, applications_dir, icon_dir):
        directory.mkdir(parents=True, exist_ok=True)

    for item in executable_dir.iterdir():
        target = bin_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    desktop_source = dist_dir / "imagesorter.desktop"
    if not desktop_source.is_file():
        raise BuildError("Desktop entry must be generated before preparing AppDir")
    shutil.copy2(desktop_source, applications_dir / desktop_source.name)
    shutil.copy2(desktop_source, app_dir / desktop_source.name)

    icon_source = root_dir / "src" / "imagesorter" / "resources" / "imagesorter.png"
    if not icon_source.is_file():
        raise BuildError(f"Application icon is missing: {icon_source}")
    shutil.copy2(icon_source, app_dir / "imagesorter.png")
    shutil.copy2(icon_source, app_dir / ".DirIcon")
    shutil.copy2(icon_source, icon_dir / "imagesorter.png")

    app_run = app_dir / "AppRun"
    app_run.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="$HERE/usr/bin:${PATH:-}"
export LD_LIBRARY_PATH="$HERE/usr/bin:${LD_LIBRARY_PATH:-}"
exec "$HERE/usr/bin/ImageSorter" "$@"
""",
        encoding="utf-8",
    )
    app_run.chmod(0o755)
    return app_dir


def _validate_appimagetool(path: Path) -> Path:
    if not path.is_file():
        raise BuildError(f"appimagetool does not exist: {path}")
    actual = _sha256(path)
    if actual != APPIMAGETOOL_X86_64_SHA256:
        raise BuildError(
            "appimagetool checksum mismatch: "
            f"expected {APPIMAGETOOL_X86_64_SHA256}, got {actual}"
        )
    path.chmod(path.stat().st_mode | 0o100)
    return path


def get_appimagetool_executable(
    root_dir: Path, explicit_tool: Path | None = None
) -> Path:
    """Return only the digest-pinned upstream 1.9.1 x86_64 tool."""
    if platform.machine().lower() not in ("x86_64", "amd64"):
        raise BuildError("The pinned AppImage build currently supports x86_64 only")
    if explicit_tool is not None:
        return _validate_appimagetool(explicit_tool.resolve())

    path_tool = shutil.which("appimagetool")
    if path_tool:
        return _validate_appimagetool(Path(path_tool).resolve())

    cache_dir = root_dir / ".build-tools"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"appimagetool-{APPIMAGETOOL_VERSION}-x86_64.AppImage"
    if cached.exists():
        return _validate_appimagetool(cached)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=cache_dir, prefix="appimagetool-", suffix=".download"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            APPIMAGETOOL_X86_64_URL,
            headers={"User-Agent": "ImageSorter-build/1"},
        )
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        _validate_appimagetool(temporary)
        temporary.replace(cached)
        return cached
    except Exception as exc:
        raise BuildError(f"Unable to fetch verified appimagetool: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def build_appimage(
    root_dir: Path, dist_dir: Path, *, explicit_tool: Path | None = None
) -> Path:
    """Build and verify the requested Linux x86_64 AppImage."""
    if not sys.platform.startswith("linux"):
        raise BuildError("--appimage is supported only on Linux")
    app_dir = prepare_appdir(dist_dir, root_dir)
    output = dist_dir / "ImageSorter-x86_64.AppImage"
    output.unlink(missing_ok=True)
    tool = get_appimagetool_executable(root_dir, explicit_tool)
    environment = os.environ.copy()
    environment.update({"ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"})
    subprocess.run(
        [str(tool), str(app_dir), str(output)],
        check=True,
        cwd=dist_dir,
        env=environment,
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise BuildError("appimagetool returned success without producing an AppImage")
    output.chmod(output.stat().st_mode | 0o100)
    return output


def build_executable(
    *, build_appimage_flag: bool = False, explicit_tool: Path | None = None
) -> list[Path]:
    """Build the onedir executable and every explicitly requested artifact."""
    root_dir = Path(__file__).resolve().parent
    if not (root_dir / "src" / "imagesorter" / "main.py").is_file():
        raise BuildError(f"Could not locate src/imagesorter/main.py under {root_dir}")
    if not check_pyinstaller_installed():
        raise BuildError("PyInstaller is not installed in the active Python environment")

    ensure_icon_assets(root_dir)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            str(root_dir / "ImageSorter.spec"),
        ],
        check=True,
        cwd=root_dir,
    )
    dist_dir = root_dir / "dist"
    executable = dist_dir / "ImageSorter" / (
        "ImageSorter.exe" if sys.platform == "win32" else "ImageSorter"
    )
    if not executable.is_file():
        raise BuildError(f"PyInstaller did not produce {executable}")
    generate_freedesktop_artifacts(dist_dir)
    outputs = [executable, dist_dir / "imagesorter.desktop"]
    if build_appimage_flag:
        outputs.append(
            build_appimage(root_dir, dist_dir, explicit_tool=explicit_tool)
        )
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Image Sorter desktop artifacts")
    parser.add_argument("--appimage", action="store_true", help="also require an AppImage")
    parser.add_argument(
        "--appimagetool", type=Path, help="explicit digest-verified appimagetool path"
    )
    args = parser.parse_args(argv)
    try:
        outputs = build_executable(
            build_appimage_flag=args.appimage,
            explicit_tool=args.appimagetool,
        )
    except (BuildError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print("Build succeeded with verified outputs:")
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
