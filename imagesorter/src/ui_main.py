import os
import shutil
from typing import List, Dict, Optional
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QVBoxLayout, QWidget, QMessageBox, QMenu, QApplication
)
from PyQt6.QtGui import QPixmap, QImageReader, QKeySequence, QAction, QPalette, QColor
from PyQt6.QtCore import Qt, QSize, QEvent
from src.ui_settings import SettingsWindow
from src.queue_worker import QueueWorker
from src.settings_manager import SettingsManager
from src.logger import logger

class MainViewer(QMainWindow):
    """
    Main application window for displaying and sorting images.
    Features robust accessibility, theming, and an undo stack.
    """
    def __init__(self, settings_manager: SettingsManager) -> None:
        super().__init__()
        self.settings = settings_manager

        # Initialize worker and connect signals
        self.worker = QueueWorker(self.settings)
        self.worker.signals.progress.connect(self.on_worker_progress)
        self.worker.signals.error.connect(self.on_worker_error)
        self.worker.signals.undo_record.connect(self.on_undo_record_received)

        self.images: List[str] = []
        self.current_index: int = -1
        self.history: List[Dict[str, str]] = []  # Undo stack

        self.apply_theme()
        self.init_ui()
        self.load_images()

    def init_ui(self) -> None:
        """Initializes the main UI components with accessibility features."""
        self.setWindowTitle("Image Sorter - Enterprise")

        if self.settings.get('ui', 'fullscreen'):
            self.showFullScreen()
        else:
            self.showMaximized()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # Image Display Label
        self.image_label = QLabel("No images loaded. Press 'S' to open settings.")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setAccessibleName("Image Display Area")
        self.image_label.setAccessibleDescription("Displays the current image to be sorted.")

        # Base styling, overridden by theme
        font_size = self.settings.get('ui', 'font_size') or 24
        self.image_label.setStyleSheet(f"font-size: {font_size}px; padding: 20px;")

        self.layout.addWidget(self.image_label)

        self.setup_menu()
        self.statusBar().showMessage("Ready", 3000)

    def apply_theme(self) -> None:
        """Applies the selected theme (Light, Dark, High Contrast) to the application."""
        theme = self.settings.get('ui', 'theme') or 'Dark'
        app = QApplication.instance()
        palette = QPalette()

        if theme == 'Dark':
            palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
            palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
            palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
        elif theme == 'High Contrast':
            palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
            palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.yellow)
            palette.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.black)
            palette.setColor(QPalette.ColorRole.AlternateBase, Qt.GlobalColor.black)
            palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.black)
            palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.yellow)
            palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.yellow)
            palette.setColor(QPalette.ColorRole.Button, Qt.GlobalColor.black)
            palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.yellow)
            palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
            palette.setColor(QPalette.ColorRole.Link, Qt.GlobalColor.cyan)
            palette.setColor(QPalette.ColorRole.Highlight, Qt.GlobalColor.cyan)
            palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
        else:
             # Default Light theme (system default)
             app.setPalette(app.style().standardPalette())
             return

        app.setPalette(palette)

    def setup_menu(self) -> None:
        """Sets up the application menu bar with shortcuts for accessibility."""
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        settings_action = QAction("&Settings", self)
        settings_action.setShortcut(QKeySequence("S"))
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        reload_action = QAction("&Reload Images", self)
        reload_action.setShortcut(QKeySequence("R"))
        reload_action.triggered.connect(self.load_images)
        file_menu.addAction(reload_action)

        undo_action = QAction("&Undo Last Action", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self.undo_last_action)
        file_menu.addAction(undo_action)

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Esc"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def load_images(self) -> None:
        """Loads images from the source directory specified in settings."""
        src_dir = self.settings.get('directories', 'source')
        if not src_dir or not os.path.exists(src_dir):
            self.image_label.setText("Source directory not set or invalid.\nPress 'S' to configure.")
            return

        self.image_label.setText("Loading images...")
        QApplication.processEvents()

        supported_formats = [fmt.data().decode() for fmt in QImageReader.supportedImageFormats()]

        self.images = []
        try:
             for f in os.listdir(src_dir):
                 ext = f.split('.')[-1].lower()
                 if ext in supported_formats:
                     self.images.append(os.path.join(src_dir, f))
        except OSError as e:
             logger.error(f"Error reading source directory: {e}")
             self.image_label.setText(f"Error reading directory:\n{e}")
             return

        if self.images:
            self.current_index = 0
            self.show_image()
        else:
            self.image_label.setText("No images found in source directory.")

    def show_image(self) -> None:
        """Displays the image at the current index."""
        if self.current_index < 0 or self.current_index >= len(self.images):
            self.image_label.setText("All done!")
            self.image_label.setPixmap(QPixmap())
            self.setWindowTitle("Image Sorter - All done!")
            return

        filepath = self.images[self.current_index]
        pixmap = QPixmap(filepath)

        if pixmap.isNull():
            logger.warning(f"Failed to load image: {filepath}")
            self.image_label.setText(f"Failed to load: {os.path.basename(filepath)}")
            return

        # Scale pixmap to fit window
        size = self.centralWidget().size()
        scaled_pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(scaled_pixmap)

        # Accessibility: Update label for screen readers
        filename = os.path.basename(filepath)
        self.image_label.setAccessibleName(f"Image {self.current_index + 1} of {len(self.images)}: {filename}")
        self.setWindowTitle(f"Image Sorter - {filename} ({self.current_index + 1}/{len(self.images)})")

    def resizeEvent(self, event: QEvent) -> None:
        """Handles window resize events to rescale the image."""
        super().resizeEvent(event)
        if self.images and 0 <= self.current_index < len(self.images):
            self.show_image()

    def keyPressEvent(self, event: QEvent) -> None:
        """Handles keyboard navigation and sorting actions."""
        key = event.key()
        key_str = event.text().upper()

        if key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showMaximized()
                self.settings.set('ui', 'fullscreen', False)
            else:
                self.close()
            return

        if key == Qt.Key.Key_S:
            self.open_settings()
            return

        if key == Qt.Key.Key_R:
            self.load_images()
            return

        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Z:
             self.undo_last_action()
             return

        if self.current_index < 0 or self.current_index >= len(self.images):
            return

        filepath = self.images[self.current_index]

        # Built-in QOL features
        if key == Qt.Key.Key_Space or key == Qt.Key.Key_Right:
            # Skip
            self.current_index += 1
            self.show_image()
            return

        if key == Qt.Key.Key_Left or key == Qt.Key.Key_Backspace:
            # Go back visually
            if self.current_index > 0:
                self.current_index -= 1
                self.show_image()
            return

        if key == Qt.Key.Key_Delete:
            # Trash
            self.worker.add_task('trash', filepath)
            self.next_image_after_action()
            return

        # Check Hotkeys
        hotkeys = self.settings.get('hotkeys') or {}
        if key_str in hotkeys:
            config = hotkeys[key_str]
            action = config.get('action', 'move')
            folder = config.get('folder')

            if not folder:
                 self.statusBar().showMessage(f"Warning: No destination folder set for hotkey '{key_str}'", 4000)
                 return

            self.worker.add_task(action, filepath, folder)
            self.next_image_after_action()

    def next_image_after_action(self) -> None:
        """Advances to the next image after an action is queued."""
        if 0 <= self.current_index < len(self.images):
            self.images.pop(self.current_index)
        self.show_image()

    def on_undo_record_received(self, data: Dict[str, str]) -> None:
         """Receives undo data from the worker thread."""
         self.history.append(data)
         # Limit history size to prevent memory bloat
         if len(self.history) > 50:
              self.history.pop(0)

    def undo_last_action(self) -> None:
         """Reverts the last move or copy operation."""
         if not self.history:
              self.statusBar().showMessage("Nothing to undo.", 3000)
              return

         last_action = self.history.pop()
         original = last_action['original']
         current = last_action['current']
         action = last_action['action']

         try:
              if action == 'move':
                   shutil.move(current, original)
                   self.statusBar().showMessage(f"Undid move: restored {os.path.basename(original)}", 4000)
              elif action == 'copy':
                   os.remove(current)
                   self.statusBar().showMessage(f"Undid copy: removed {os.path.basename(current)}", 4000)

              # Optionally, re-insert the image into our list so we can see it again
              if original not in self.images:
                   self.images.insert(self.current_index if self.current_index >= 0 else 0, original)
                   if self.current_index < 0:
                        self.current_index = 0
                   self.show_image()

         except OSError as e:
              logger.error(f"Failed to undo action: {e}")
              self.statusBar().showMessage(f"Error undoing action: {e}", 5000)

    def open_settings(self) -> None:
        """Opens the settings window."""
        self.settings_window = SettingsWindow(self.settings)
        self.settings_window.destroyed.connect(self.on_settings_closed)
        self.settings_window.show()

    def on_settings_closed(self) -> None:
        """Called when settings window closes to refresh state."""
        self.apply_theme()

        # Update image label font size in case it changed
        font_size = self.settings.get('ui', 'font_size') or 24
        self.image_label.setStyleSheet(f"font-size: {font_size}px; padding: 20px;")

        self.worker.refresh_settings()
        self.load_images()

    def on_worker_progress(self, msg: str) -> None:
        """Displays progress messages in the status bar."""
        self.statusBar().showMessage(msg, 3000)

    def on_worker_error(self, filepath: str, error: str) -> None:
        """Displays errors in the status bar."""
        self.statusBar().showMessage(f"Error: {error}", 5000)

    def closeEvent(self, event: QEvent) -> None:
        """Ensures background threads are stopped before closing."""
        self.worker.stop()
        super().closeEvent(event)
