from __future__ import annotations

import os
import sys

try:
    from .logger import logger
    from .settings_manager import SettingsManager
    from .ui_main import MainViewer
except ImportError:
    from imagesorter.logger import logger  # type: ignore
    from imagesorter.settings_manager import SettingsManager  # type: ignore
    from imagesorter.ui_main import MainViewer  # type: ignore


def configure_linux_platform() -> None:
    """Configures Linux display server fallback strategy if QT_QPA_PLATFORM is unset."""
    if not sys.platform.startswith("linux"):
        return

    if "QT_QPA_PLATFORM" in os.environ:
        logger.info(
            f"Preserving existing QT_QPA_PLATFORM='{os.environ['QT_QPA_PLATFORM']}'"
        )
        return

    wayland_display = os.environ.get("WAYLAND_DISPLAY", "").strip()
    x11_display = os.environ.get("DISPLAY", "").strip()

    if wayland_display and x11_display:
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
        logger.info("Set QT_QPA_PLATFORM='wayland;xcb' for Wayland/X11 display server synergy.")
    elif wayland_display:
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        logger.info("Set QT_QPA_PLATFORM='wayland' for Wayland display server.")
    elif x11_display:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        logger.info("Set QT_QPA_PLATFORM='xcb' for X11 display server.")
    else:
        logger.info("Neither WAYLAND_DISPLAY nor DISPLAY found. Leaving QT_QPA_PLATFORM unset for Qt default selection.")


def main(argv: list[str] | None = None) -> int:
    """Main application entry point.

    Returns:
        int: Exit status code (0 for success, 1 for failure).
    """
    args = sys.argv if argv is None else argv

    if any(arg in args[1:] for arg in ("-h", "--help")):
        print("Usage: imagesorter [options]\n\nOptions:\n  -h, --help  Show this help message and exit")
        return 0

    configure_linux_platform()

    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication(args)
        app.setApplicationName("Image Sorter")
        app.setOrganizationName("SyrisBruhh42")

        settings = SettingsManager()
        viewer = MainViewer(settings)
        viewer.show()

        return int(app.exec())
    except Exception as e:
        logger.exception("Qt initialization failed", exc_info=e)
        sys.stderr.write(
            f"Error: Qt application initialization failed: {e}\n"
            "If running in a headless or test environment, try setting QT_QPA_PLATFORM=offscreen.\n"
            "If running in a desktop environment, check that DISPLAY or WAYLAND_DISPLAY is correctly set.\n"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
