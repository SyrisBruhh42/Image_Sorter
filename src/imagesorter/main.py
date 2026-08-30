import os
import sys
from PyQt6.QtWidgets import QApplication
from .ui_main import MainViewer
from .settings_manager import SettingsManager
from .logger import logger


def main() -> None:
    # Ensure display server synergy on Linux
    if sys.platform.startswith("linux"):
        if "QT_QPA_PLATFORM" not in os.environ:
            if "WAYLAND_DISPLAY" in os.environ:
                os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
                logger.info("Set QT_QPA_PLATFORM='wayland;xcb' for Linux Wayland display server.")
            elif "DISPLAY" in os.environ:
                os.environ["QT_QPA_PLATFORM"] = "xcb"
                logger.info("Set QT_QPA_PLATFORM='xcb' for Linux X11 display server.")

    app = QApplication(sys.argv)
    app.setApplicationName("Image Sorter")
    app.setOrganizationName("SyrisBruhh42")

    settings = SettingsManager()

    viewer = MainViewer(settings)
    viewer.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
