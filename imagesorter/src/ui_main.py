import os
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QVBoxLayout, QWidget, QMessageBox, QMenu
)
from PyQt6.QtGui import QPixmap, QImageReader, QKeySequence, QAction
from PyQt6.QtCore import Qt, QSize
from src.ui_settings import SettingsWindow
from src.queue_worker import QueueWorker

class MainViewer(QMainWindow):
    def __init__(self, settings_manager):
        super().__init__()
        self.settings = settings_manager
        self.worker = QueueWorker(self.settings)
        self.worker.signals.progress.connect(self.on_worker_progress)
        self.worker.signals.error.connect(self.on_worker_error)
        self.worker.start()

        self.images = []
        self.current_index = -1
        self.history = []  # For undo functionality

        self.init_ui()
        self.load_images()

    def init_ui(self):
        self.setWindowTitle("Image Sorter")

        if self.settings.get('ui', 'fullscreen'):
            self.showFullScreen()
        else:
            self.showMaximized()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.image_label = QLabel("No images loaded. Press 'S' to open settings.")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background-color: black; color: white; font-size: 24px;")
        self.layout.addWidget(self.image_label)

        self.setup_menu()

    def setup_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("File")

        settings_action = QAction("Settings (S)", self)
        settings_action.setShortcut(QKeySequence("S"))
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        reload_action = QAction("Reload Images (R)", self)
        reload_action.setShortcut(QKeySequence("R"))
        reload_action.triggered.connect(self.load_images)
        file_menu.addAction(reload_action)

        exit_action = QAction("Exit (Esc)", self)
        exit_action.setShortcut(QKeySequence("Esc"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def load_images(self):
        src_dir = self.settings.get('directories', 'source')
        if not src_dir or not os.path.exists(src_dir):
            self.image_label.setText("Source directory not set or invalid.\nPress 'S' to configure.")
            return

        supported_formats = [fmt.data().decode() for fmt in QImageReader.supportedImageFormats()]

        self.images = []
        for f in os.listdir(src_dir):
            ext = f.split('.')[-1].lower()
            if ext in supported_formats:
                self.images.append(os.path.join(src_dir, f))

        if self.images:
            self.current_index = 0
            self.show_image()
        else:
            self.image_label.setText("No images found in source directory.")

    def show_image(self):
        if self.current_index < 0 or self.current_index >= len(self.images):
            self.image_label.setText("All done!")
            self.image_label.setPixmap(QPixmap())
            self.setWindowTitle("Image Sorter - All done!")
            return

        filepath = self.images[self.current_index]
        pixmap = QPixmap(filepath)

        if pixmap.isNull():
            self.image_label.setText(f"Failed to load: {os.path.basename(filepath)}")
            return

        # Scale pixmap to fit window
        size = self.centralWidget().size()
        scaled_pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(scaled_pixmap)
        self.setWindowTitle(f"Image Sorter - {os.path.basename(filepath)} ({self.current_index + 1}/{len(self.images)})")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.images and self.current_index >= 0 and self.current_index < len(self.images):
            self.show_image()

    def keyPressEvent(self, event):
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
            # Go back (visual only, doesn't undo file operations yet)
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
            self.worker.add_task(
                config.get('action', 'move'),
                filepath,
                config.get('folder')
            )
            self.next_image_after_action()

    def next_image_after_action(self):
        # Remove the processed image from the list so backwards navigation doesn't break
        if 0 <= self.current_index < len(self.images):
            self.images.pop(self.current_index)
            # We don't increment current_index because the next image slides into this spot

        self.show_image()

    def open_settings(self):
        self.settings_window = SettingsWindow(self.settings)
        self.settings_window.destroyed.connect(self.on_settings_closed)
        self.settings_window.show()

    def on_settings_closed(self):
        self.worker.refresh_settings()

    def on_worker_progress(self, msg):
        self.statusBar().showMessage(msg, 3000)

    def on_worker_error(self, filepath, error):
        self.statusBar().showMessage(f"Error: {error}", 5000)

    def closeEvent(self, event):
        self.worker.stop()
        super().closeEvent(event)
