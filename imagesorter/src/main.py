import os
import sys
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication
from src.ui_main import MainViewer
from src.settings_manager import SettingsManager
from src.logger import logger


def main() -> None:
    # High-DPI PassThrough Scaling
    if hasattr(Qt.HighDpiScaleFactorRoundingPolicy, "PassThrough"):
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    # Ensure Wayland / X11 display server synergy strictly on Linux platforms
    if sys.platform.startswith("linux") and "QT_QPA_PLATFORM" not in os.environ:
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
            logger.info("Set QT_QPA_PLATFORM='wayland;xcb' for Wayland display server synergy.")
        elif os.environ.get("DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "xcb"
            logger.info("Set QT_QPA_PLATFORM='xcb' for X11 display server synergy.")

    app = QApplication(sys.argv)
    app.setApplicationName("Image Sorter")
    app.setOrganizationName("SyrisBruhh42")
    QGuiApplication.setDesktopFileName("imagesorter.desktop")

    settings = SettingsManager()

    viewer = MainViewer(settings)
    viewer.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
