import os
import sys
from PyQt6.QtWidgets import QApplication
from src.ui_main import MainViewer
from src.settings_manager import SettingsManager
from src.logger import logger


def main() -> None:
    # Ensure Wayland / X11 fallback synergy on Linux display servers
    if sys.platform != "win32" and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
        logger.info("Set QT_QPA_PLATFORM='wayland;xcb' for Linux display server synergy.")

    app = QApplication(sys.argv)
    app.setApplicationName("Image Sorter")
    app.setOrganizationName("SyrisBruhh42")

    settings = SettingsManager()

    viewer = MainViewer(settings)
    viewer.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
