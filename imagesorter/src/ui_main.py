import os
import logging
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QVBoxLayout, QWidget, QMessageBox, QMenu, QGraphicsView, QGraphicsScene
)
from PyQt6.QtWidgets import QGraphicsPixmapItem
from PyQt6.QtGui import QPixmap, QImageReader, QKeySequence, QAction, QPainter, QTransform, QCursor, QImage, qRgb
from src.image_loader import ImageLoader
import numpy as np
from PyQt6.QtCore import Qt, QSize
from src.ui_settings import SettingsWindow
from src.queue_worker import QueueWorker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ImageViewer(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background-color: black; border: none;")

        self.zoom_factor = 1.15
        self.locked_zoom_pan = False
        self.is_smart_zoom = False
        self.saved_transform = QTransform()

        self.show_clipping = False
        self.original_pixmap = QPixmap()
        self.clipping_pixmap = QPixmap()

    def set_image(self, pixmap):
        self.original_pixmap = pixmap
        self.clipping_pixmap = QPixmap() # Reset cache

        if self.show_clipping:
            self.apply_clipping_overlay()
        else:
            self.pixmap_item.setPixmap(self.original_pixmap)

        self.scene.setSceneRect(self.pixmap_item.boundingRect())

        if not self.locked_zoom_pan:
            self.fit_to_window()

    def toggle_clipping_warnings(self):
        self.show_clipping = not self.show_clipping
        if self.show_clipping:
            self.apply_clipping_overlay()
        else:
            self.pixmap_item.setPixmap(self.original_pixmap)

    def apply_clipping_overlay(self):
        if self.original_pixmap.isNull():
            return

        if not self.clipping_pixmap.isNull():
            self.pixmap_item.setPixmap(self.clipping_pixmap)
            return

        image = self.original_pixmap.toImage()
        image = image.convertToFormat(QImage.Format.Format_RGB32)

        # Simple implementation for clipping:
        # Over-exposed (r,g,b > 250) -> Red
        # Under-exposed (r,g,b < 5) -> Blue

        width = image.width()
        height = image.height()

        # We need a fast way to do this in python, QImage pixel manipulation is slow
        # Convert to numpy array
        ptr = image.bits()
        ptr.setsize(height * width * 4)
        arr = np.ndarray(shape=(height, width, 4), buffer=ptr, dtype=np.uint8)

        # Format_RGB32 in PyQt is BGRA
        b = arr[:, :, 0]
        g = arr[:, :, 1]
        r = arr[:, :, 2]

        # Overexposed
        over_mask = (r > 250) & (g > 250) & (b > 250)
        # Underexposed
        under_mask = (r < 5) & (g < 5) & (b < 5)

        # Apply red to overexposed
        arr[over_mask, 0] = 0   # b
        arr[over_mask, 1] = 0   # g
        arr[over_mask, 2] = 255 # r

        # Apply blue to underexposed
        arr[under_mask, 0] = 255 # b
        arr[under_mask, 1] = 0   # g
        arr[under_mask, 2] = 0   # r

        self.clipping_pixmap = QPixmap.fromImage(image)
        self.pixmap_item.setPixmap(self.clipping_pixmap)

    def fit_to_window(self):
        if self.pixmap_item.pixmap().isNull():
            return
        self.fitInView(self.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.saved_transform = self.transform()
        self.is_smart_zoom = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.locked_zoom_pan and not self.is_smart_zoom and not self.pixmap_item.pixmap().isNull():
            self.fit_to_window()

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
        self.is_smart_zoom = False
        if not self.locked_zoom_pan:
            self.saved_transform = self.transform()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.toggle_smart_zoom()
        super().mousePressEvent(event)

    def toggle_smart_zoom(self):
        if self.pixmap_item.pixmap().isNull():
            return

        if self.is_smart_zoom:
            self.setTransform(self.saved_transform)
            self.is_smart_zoom = False
        else:
            self.saved_transform = self.transform()
            self.resetTransform()

            # center on cursor
            cursor_pos = self.mapFromGlobal(QCursor.pos())
            scene_pos = self.mapToScene(cursor_pos)
            self.centerOn(scene_pos)
            self.is_smart_zoom = True

class MainViewer(QMainWindow):
    def __init__(self, settings_manager):
        super().__init__()
        self.settings = settings_manager
        self.worker = QueueWorker(self.settings)
        self.worker.signals.progress.connect(self.on_worker_progress)
        self.worker.signals.error.connect(self.on_worker_error)
        self.worker.signals.undo_record.connect(self.on_undo_record)
        self.worker.start()

        self.images = []
        self.current_index = -1
        self.history = []  # For undo functionality

        self.zen_mode = False

        # Zero-Latency Caching
        self.pixmap_cache = {}

        self.loader = ImageLoader()
        self.loader.image_loaded.connect(self.on_image_preloaded)
        self.loader.start()

        self.init_ui()
        self.load_images()

    def on_image_preloaded(self, filepath, img):
        if filepath not in self.pixmap_cache:
            self.pixmap_cache[filepath] = QPixmap.fromImage(img)

    def preload_adjacent_images(self):
        # Keep cache manageable (store only recent adjacent paths)
        # Actually a simple LRU or just bounding size is better, but since this is file-path based now,
        # we'll just clear it if it gets too large
        if len(self.pixmap_cache) > 10:
            # We don't have a strict order, but since we rely on it mainly for immediate next/prev,
            # this is just to prevent infinite memory growth
            self.pixmap_cache.clear()

        # Preload next image
        if self.current_index + 1 < len(self.images):
            next_path = self.images[self.current_index + 1]
            if next_path not in self.pixmap_cache:
                self.loader.add_task(next_path)

        # Preload previous image
        if self.current_index - 1 >= 0:
            prev_path = self.images[self.current_index - 1]
            if prev_path not in self.pixmap_cache:
                self.loader.add_task(prev_path)

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
        self.central_widget.setStyleSheet("background-color: black;")

        # Add text label for empty state
        self.empty_label = QLabel("No images loaded. Press 'S' to open settings.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("background-color: black; color: white; font-size: 24px;")
        self.layout.addWidget(self.empty_label)

        self.viewer = ImageViewer()
        self.layout.addWidget(self.viewer)
        self.viewer.hide()

        self.setup_menu()

    def setup_menu(self):
        self.main_menu = self.menuBar()
        file_menu = self.main_menu.addMenu("File")

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

        edit_menu = self.main_menu.addMenu("Edit")
        undo_action = QAction("Undo (Ctrl+Z)", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self.undo_last_action)
        edit_menu.addAction(undo_action)

        view_menu = self.main_menu.addMenu("View")

        self.locked_zoom_action = QAction("Locked Zoom/Pan (L)", self)
        self.locked_zoom_action.setCheckable(True)
        self.locked_zoom_action.setShortcut(QKeySequence("L"))
        self.locked_zoom_action.triggered.connect(self.toggle_locked_zoom)
        view_menu.addAction(self.locked_zoom_action)

        zen_mode_action = QAction("Zen Mode (Z)", self)
        zen_mode_action.setShortcut(QKeySequence("Z"))
        zen_mode_action.triggered.connect(self.toggle_zen_mode)
        view_menu.addAction(zen_mode_action)

        clipping_action = QAction("Clipping Warnings (C)", self)
        clipping_action.setShortcut(QKeySequence("C"))
        clipping_action.triggered.connect(self.viewer.toggle_clipping_warnings)
        view_menu.addAction(clipping_action)

    def toggle_locked_zoom(self, checked):
        self.viewer.locked_zoom_pan = checked
        if not checked and self.viewer.isVisible():
            self.viewer.fit_to_window()

    def toggle_zen_mode(self):
        self.zen_mode = not self.zen_mode
        if self.zen_mode:
            self.main_menu.hide()
            self.statusBar().hide()
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
            self.showFullScreen()
        else:
            self.main_menu.show()
            self.statusBar().show()
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.FramelessWindowHint)
            if self.settings.get('ui', 'fullscreen'):
                self.showFullScreen()
            else:
                self.showMaximized()


    def load_images(self):

        src_dir = self.settings.get('directories', 'source')
        if not src_dir or not os.path.exists(src_dir):
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText("Source directory not set or invalid.\nPress 'S' to configure.")
            return

        supported_formats = [fmt.data().decode() for fmt in QImageReader.supportedImageFormats()]

        self.images = []
        for f in os.listdir(src_dir):
            ext = f.split('.')[-1].lower()
            if ext in supported_formats:
                self.images.append(os.path.join(src_dir, f))

        if self.images:
            self.current_index = 0
            self.empty_label.hide()
            self.viewer.show()
            self.show_image()
        else:
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText("No images found in source directory.")

    def show_image(self):
        if self.current_index < 0 or self.current_index >= len(self.images):
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText("All done!")
            self.setWindowTitle("Image Sorter - All done!")
            return

        filepath = self.images[self.current_index]

        # Use cache if available
        if filepath in self.pixmap_cache:
            pixmap = self.pixmap_cache[filepath]
        else:
            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                self.pixmap_cache[filepath] = pixmap

        if pixmap.isNull():
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText(f"Failed to load: {os.path.basename(filepath)}")
            return

        self.empty_label.hide()
        self.viewer.show()
        self.viewer.set_image(pixmap)
        self.setWindowTitle(f"Image Sorter - {os.path.basename(filepath)} ({self.current_index + 1}/{len(self.images)})")

        # Preload adjacent images in background cache
        self.preload_adjacent_images()

    def keyPressEvent(self, event):
        key = event.key()
        key_str = event.text().upper()

        if key == Qt.Key.Key_Escape:
            if self.zen_mode:
                self.toggle_zen_mode()
                return
            if self.isFullScreen():
                self.showMaximized()
                self.settings.set('ui', 'fullscreen', False)
            else:
                self.close()
            return

        if key == Qt.Key.Key_Z:
            self.toggle_zen_mode()
            return

        if key == Qt.Key.Key_L:
            self.locked_zoom_action.setChecked(not self.locked_zoom_action.isChecked())
            self.toggle_locked_zoom(self.locked_zoom_action.isChecked())
            return

        if key == Qt.Key.Key_X:
            self.viewer.toggle_smart_zoom()
            return

        if key == Qt.Key.Key_C:
            self.viewer.toggle_clipping_warnings()
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
            self.current_index += 1
            self.show_image()
            return

        if key == Qt.Key.Key_Left or key == Qt.Key.Key_Backspace:
            if self.current_index > 0:
                self.current_index -= 1
                self.show_image()
            return

        if key == Qt.Key.Key_Delete:
            self.worker.add_task('trash', filepath)
            self.next_image_after_action()
            return

        hotkeys = self.settings.get('hotkeys') or {}
        if key_str in hotkeys:
            config = hotkeys[key_str]
            self.worker.add_task(
                config.get('action', 'move'),
                filepath,
                config.get('folder')
            )

            if config.get('auto_advance', True):
                if config.get('action') == 'move':
                    self.next_image_after_action()
                else:
                    # If it's just a copy, we advance the index visually without removing
                    self.current_index += 1
                    self.show_image()

    def next_image_after_action(self):
        if 0 <= self.current_index < len(self.images):
            self.images.pop(self.current_index)
        self.show_image()

    def open_settings(self):
        self.settings_window = SettingsWindow(self.settings)
        self.settings_window.destroyed.connect(self.on_settings_closed)
        self.settings_window.show()

    def on_settings_closed(self):
        self.worker.refresh_settings()

    def on_undo_record(self, record):
        self.history.append(record)
        # Cap history at 50
        if len(self.history) > 50:
            self.history.pop(0)

    def undo_last_action(self):
        if not self.history:
            self.statusBar().showMessage("Nothing to undo.", 3000)
            return

        last_action = self.history.pop()

        if last_action['type'] == 'move':
            # Send reverse move to worker
            self.worker.add_task('undo_move', last_action['new'], last_action['original'])
            # We also need to re-add it to our images list if it belongs to the current source dir
            src_dir = self.settings.get('directories', 'source')
            if os.path.dirname(last_action['original']) == src_dir:
                self.images.insert(self.current_index, last_action['original'])
                self.show_image()

        elif last_action['type'] == 'copy':
            self.worker.add_task('undo_copy', last_action['new'])

    def on_worker_progress(self, msg):
        self.statusBar().showMessage(msg, 3000)

    def on_worker_error(self, filepath, error):
        self.statusBar().showMessage(f"Error: {error}", 5000)

    def closeEvent(self, event):
        self.worker.stop()
        if hasattr(self, 'loader'):
            self.loader.stop()
        super().closeEvent(event)
