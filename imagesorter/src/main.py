import sys
from PyQt6.QtWidgets import QApplication
from src.ui_main import MainViewer
from src.settings_manager import SettingsManager

def main():
    app = QApplication(sys.argv)

    settings = SettingsManager()

    viewer = MainViewer(settings)
    viewer.show()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
