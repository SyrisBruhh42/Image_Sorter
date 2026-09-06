#!/usr/bin/env python3
"""Read-only system diagnostic script for Linux / KDE Plasma / Qt environment.

Reports OS distribution, KDE Plasma version, Qt version, display session type,
Python dependency versions, and ONNX execution providers without exposing
private image paths, credentials, or unrelated environment variables.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from typing import Any


def sanitize_string(text: str) -> str:
    """Sanitize text to redact user home directories and sensitive patterns.

    Args:
        text: The string to sanitize.

    Returns:
        The sanitized string.
    """
    if not text:
        return ""

    user = os.environ.get("USER") or os.environ.get("USERNAME")
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")

    sanitized = text
    if home:
        sanitized = sanitized.replace(home, "~")
    if user and len(user) > 1:
        # Redact instances of /home/username or /Users/username if home didn't catch it
        sanitized = re.sub(rf"/(home|Users)/{re.escape(user)}\b", r"/\1/[REDACTED_USER]", sanitized)

    return sanitized


def get_distro_info() -> dict[str, str]:
    """Retrieve Linux distribution information from /etc/os-release.

    Returns:
        Dictionary containing OS identification key-value pairs.
    """
    info: dict[str, str] = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }

    os_release_path = "/etc/os-release"
    if os.path.exists(os_release_path):
        try:
            with open(os_release_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        v = v.strip("\"'")
                        if k in ("NAME", "VERSION", "ID", "VERSION_ID", "PRETTY_NAME"):
                            info[k] = v
        except OSError:
            pass

    return info


def run_command(cmd: list[str]) -> str | None:
    """Execute a read-only system binary and capture stdout safely.

    Args:
        cmd: Binary command arguments list.

    Returns:
        Stripped output string if successful, None otherwise.
    """
    executable = cmd[0]
    if not shutil.which(executable):
        return None

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            return sanitize_string(res.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        pass

    return None


def get_kde_plasma_info() -> dict[str, str | None]:
    """Retrieve KDE Plasma and Frameworks version details.

    Returns:
        Dictionary containing KDE version outputs.
    """
    return {
        "plasmashell": run_command(["plasmashell", "--version"]),
        "kf6-config": run_command(["kf6-config", "--version"]),
        "kf5-config": run_command(["kf5-config", "--version"]),
        "kinfo": run_command(["kinfo"]),
    }


def get_qt_info() -> dict[str, str | None]:
    """Retrieve Qt and PyQt6 runtime version details.

    Returns:
        Dictionary containing Qt version strings.
    """
    qt_info: dict[str, str | None] = {
        "pyqt6_installed": "false",
        "qt_version_str": None,
        "pyqt_version_str": None,
    }

    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        qt_info["pyqt6_installed"] = "true"
        qt_info["qt_version_str"] = QT_VERSION_STR
        qt_info["pyqt_version_str"] = PYQT_VERSION_STR
    except ImportError:
        pass

    return qt_info


def get_display_session_info() -> dict[str, str | None]:
    """Retrieve display session environment details without leaking secrets.

    Returns:
        Dictionary of display-related environment variables.
    """
    return {
        "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE"),
        "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
        "DISPLAY": os.environ.get("DISPLAY"),
        "XDG_CURRENT_DESKTOP": os.environ.get("XDG_CURRENT_DESKTOP"),
        "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM"),
        "KDE_FULL_SESSION": os.environ.get("KDE_FULL_SESSION"),
        "KDE_SESSION_VERSION": os.environ.get("KDE_SESSION_VERSION"),
    }


def get_runtime_dependencies_info() -> dict[str, dict[str, Any]]:
    """Collect Python environment and core dependency versions.

    Returns:
        Dictionary of package availability and version strings.
    """
    pkg_import_map = {
        "PyQt6": "PyQt6",
        "Pillow": "PIL",
        "piexif": "piexif",
        "onnxruntime": "onnxruntime",
        "numpy": "numpy",
        "psutil": "psutil",
        "Send2Trash": "send2trash",
        "pytest": "pytest",
    }

    deps: dict[str, dict[str, Any]] = {
        "python": {
            "version": sys.version.split()[0],
            "executable": sanitize_string(sys.executable),
        }
    }

    for name, mod_name in pkg_import_map.items():
        try:
            mod = __import__(mod_name)
            version = getattr(mod, "__version__", "installed")
            deps[name] = {"available": True, "version": version}
        except ImportError:
            deps[name] = {"available": False, "version": None}

    return deps


def get_onnx_providers_info() -> dict[str, Any]:
    """Collect available ONNX Execution Providers safely.

    Returns:
        Dictionary detailing ONNX providers and hardware acceleration availability.
    """
    info: dict[str, Any] = {
        "onnxruntime_available": False,
        "providers": [],
    }

    try:
        import onnxruntime as ort
        info["onnxruntime_available"] = True
        info["providers"] = ort.get_available_providers()
    except ImportError:
        pass

    # Try hardware_scan if available in project
    try:
        from imagesorter.hardware_scan import get_prioritized_providers
        info["prioritized_providers"] = get_prioritized_providers()
    except (ImportError, Exception):
        pass

    return info


def generate_diagnostic_report() -> dict[str, Any]:
    """Assemble complete system diagnostic report.

    Returns:
        Comprehensive diagnostic dictionary.
    """
    return {
        "distro": get_distro_info(),
        "kde_plasma": get_kde_plasma_info(),
        "qt": get_qt_info(),
        "display_session": get_display_session_info(),
        "runtime_dependencies": get_runtime_dependencies_info(),
        "onnx_providers": get_onnx_providers_info(),
    }


def main() -> None:
    """CLI entry point for system diagnostics."""
    parser = argparse.ArgumentParser(
        description="Read-only system diagnostic tool for ImageSorter (Ubuntu/KDE/Qt)."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostics as a JSON object.",
    )
    args = parser.parse_args()

    report = generate_diagnostic_report()

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print("==================================================")
    print(" ImageSorter System Diagnostics (P07)")
    print("==================================================")

    distro = report["distro"]
    print("\n--- OS & Distribution ---")
    print(f"  OS Name:     {distro.get('PRETTY_NAME', distro.get('NAME', distro.get('system')))}")
    print(f"  Kernel:      {distro.get('release')} ({distro.get('machine')})")

    kde = report["kde_plasma"]
    print("\n--- KDE Plasma & Desktop Environment ---")
    print(f"  Plasmashell: {kde.get('plasmashell') or 'Not found'}")
    print(f"  KF6 Config:  {kde.get('kf6-config') or 'Not found'}")
    print(f"  KF5 Config:  {kde.get('kf5-config') or 'Not found'}")

    display = report["display_session"]
    print("\n--- Display Session ---")
    print(f"  Session Type:      {display.get('XDG_SESSION_TYPE') or 'Unset'}")
    print(f"  Desktop:           {display.get('XDG_CURRENT_DESKTOP') or 'Unset'}")
    print(f"  WAYLAND_DISPLAY:   {display.get('WAYLAND_DISPLAY') or 'Unset'}")
    print(f"  DISPLAY:           {display.get('DISPLAY') or 'Unset'}")
    print(f"  QT_QPA_PLATFORM:   {display.get('QT_QPA_PLATFORM') or 'Unset'}")

    qt = report["qt"]
    print("\n--- Qt & PyQt6 ---")
    print(f"  PyQt6 Available:   {qt.get('pyqt6_installed')}")
    print(f"  Qt Version:        {qt.get('qt_version_str') or 'N/A'}")
    print(f"  PyQt Version:      {qt.get('pyqt_version_str') or 'N/A'}")

    deps = report["runtime_dependencies"]
    print("\n--- Python Runtime Dependencies ---")
    print(f"  Python:            {deps['python']['version']} ({deps['python']['executable']})")
    for name, data in deps.items():
        if name == "python":
            continue
        avail = "YES" if data["available"] else "NO"
        ver = data["version"] or "N/A"
        print(f"  {name:<18} Available: {avail:<4} Version: {ver}")

    onnx = report["onnx_providers"]
    print("\n--- ONNX Execution Providers ---")
    print(f"  ONNXRuntime:       {'Available' if onnx['onnxruntime_available'] else 'Not available'}")
    print(f"  Providers:         {', '.join(onnx.get('providers', [])) or 'None'}")
    if "prioritized_providers" in onnx:
        print(f"  Prioritized:       {', '.join(onnx['prioritized_providers'])}")

    print("\n==================================================")


if __name__ == "__main__":
    main()
