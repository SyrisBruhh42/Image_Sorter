import os
import shutil
from typing import List, Dict, Optional, Any
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QVBoxLayout, QHBoxLayout, QWidget, QMessageBox, QMenu,
    QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QProgressBar,
    QLineEdit, QTextEdit, QSpinBox, QComboBox
)
from PyQt6.QtGui import (
    QPixmap, QImageReader, QKeySequence, QAction, QPalette, QColor, QPainter,
    QTransform, QCursor, QImage, QWheelEvent, QMouseEvent
)
from PyQt6.QtCore import Qt, QSize, QEvent, QObject
from src.ui_settings import SettingsWindow
from src.queue_worker import QueueWorker
from src.settings_manager import SettingsManager
from src.logger import logger
from src.image_loader import ImageLoader
import numpy as np


class ImageViewer(QGraphicsView):
    """
    Custom QGraphicsView for displaying images with pan/zoom and clipping analysis.
    """
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.scene: QGraphicsScene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item: QGraphicsPixmapItem = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background-color: black; border: none;")

        self.setAccessibleName("Image Canvas Viewport")
        self.setAccessibleDescription("Interactive viewport supporting smooth zoom, pan, and exposure clipping overlays.")

        self.zoom_factor: float = 1.15
        self.locked_zoom_pan: bool = False
        self.is_smart_zoom: bool = False
        self.saved_transform: QTransform = QTransform()

        self.show_clipping: bool = False
        self.original_pixmap: QPixmap = QPixmap()
        self.clipping_pixmap: QPixmap = QPixmap()

    def set_image(self, pixmap: QPixmap) -> None:
        """Sets the image to display."""
        self.original_pixmap = pixmap
        self.clipping_pixmap = QPixmap()

        if self.show_clipping:
            self.apply_clipping_overlay()
        else:
            self.pixmap_item.setPixmap(self.original_pixmap)

        self.scene.setSceneRect(self.pixmap_item.boundingRect())

        if not self.locked_zoom_pan:
            self.fit_to_window()

    def toggle_clipping_warnings(self) -> None:
        """Toggles the display of over/under-exposure warnings."""
        self.show_clipping = not self.show_clipping
        if self.show_clipping:
            self.apply_clipping_overlay()
        else:
            self.pixmap_item.setPixmap(self.original_pixmap)

    def apply_clipping_overlay(self) -> None:
        """Calculates and applies clipping overlay using numpy."""
        if self.original_pixmap.isNull():
            return

        if self.clipping_pixmap.isNull():
            img = self.original_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            ptr = img.bits()
            ptr.setsize(img.height() * img.bytesPerLine())
            arr = np.frombuffer(ptr, np.uint8).reshape((img.height(), img.bytesPerLine() // 4, 4))

            b = arr[..., 0]
            g = arr[..., 1]
            r = arr[..., 2]

            overexposed = (r > 250) & (g > 250) & (b > 250)
            underexposed = (r < 5) & (g < 5) & (b < 5)

            overlay = QImage(img.width(), img.height(), QImage.Format.Format_ARGB32)
            overlay.fill(Qt.GlobalColor.transparent)

            ptr_out = overlay.bits()
            ptr_out.setsize(overlay.height() * overlay.bytesPerLine())
            arr_out = np.frombuffer(ptr_out, np.uint8).reshape((overlay.height(), overlay.bytesPerLine() // 4, 4))

            arr_out[overexposed] = [0, 0, 255, 255]
            arr_out[underexposed] = [255, 0, 0, 255]

            painter = QPainter(img)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(0, 0, overlay)
            painter.end()

            self.clipping_pixmap = QPixmap.fromImage(img)

        self.pixmap_item.setPixmap(self.clipping_pixmap)

    def fit_to_window(self) -> None:
        """Fits the image into the view."""
        if not self.pixmap_item.pixmap() or self.pixmap_item.pixmap().isNull():
            return
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.saved_transform = self.transform()
        self.is_smart_zoom = False

    def resizeEvent(self, event: QEvent) -> None:
        """Handles resize events."""
        super().resizeEvent(event)
        if not self.locked_zoom_pan and not self.is_smart_zoom:
            self.fit_to_window()
        else:
            self.setTransform(self.saved_transform)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handles mouse wheel zooming."""
        if self.locked_zoom_pan:
            return

        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
        self.saved_transform = self.transform()
        self.is_smart_zoom = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Handles smart zooming on double click."""
        if self.locked_zoom_pan:
            return

        if self.is_smart_zoom:
            self.fit_to_window()
        else:
            self.resetTransform()
            self.centerOn(self.mapToScene(event.pos()))
            self.saved_transform = self.transform()
            self.is_smart_zoom = True


class MainViewer(QMainWindow):
    """
    Main application window for displaying and sorting images.
    Features WCAG AAA accessibility, focus isolation, theming, and an undo stack.
    """
    def __init__(self, settings_manager: SettingsManager) -> None:
        super().__init__()
        self.settings = settings_manager

        QApplication.instance().installEventFilter(self)

        self.worker = QueueWorker(self.settings)
        self.worker.signals.progress.connect(self.on_worker_progress)
        self.worker.signals.error.connect(self.on_worker_error)
        self.worker.signals.undo_record.connect(self.on_undo_record_received)

        self.images: List[str] = []
        self.current_index: int = -1
        self.history: List[Dict[str, Any]] = []
        self.zen_mode: bool = False

        self.pixmap_cache: Dict[str, QPixmap] = {}
        self.loader = ImageLoader()
        self.loader.image_loaded.connect(self.on_image_preloaded)
        self.loader.start()

        self.apply_theme()
        self.init_ui()
        self.load_images()

    def eventFilter(self, obj: 'QObject', event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            tooltips_enabled = self.settings.get('ui', 'tooltips_enabled')
            if tooltips_enabled is None:
                tooltips_enabled = True
            if not tooltips_enabled:
                modifiers = QApplication.keyboardModifiers()
                if not (modifiers & Qt.KeyboardModifier.AltModifier):
                    return True
        return super().eventFilter(obj, event)

    def on_image_preloaded(self, filepath: str, img: QImage) -> None:
        if filepath not in self.pixmap_cache:
            self.pixmap_cache[filepath] = QPixmap.fromImage(img)

    def preload_adjacent_images(self) -> None:
        if len(self.pixmap_cache) > 10:
            self.pixmap_cache.clear()

        if self.current_index + 1 < len(self.images):
            self.loader.add_task(self.images[self.current_index + 1])

        if self.current_index - 1 >= 0:
            self.loader.add_task(self.images[self.current_index - 1])

    def init_ui(self) -> None:
        """Initializes main UI with WCAG AAA accessibility properties."""
        self.setWindowTitle("Image Sorter - Enterprise")

        if self.settings.get('ui', 'fullscreen'):
            self.showFullScreen()
        else:
            self.showMaximized()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # Enterprise HUD overlay
        self.hud_widget = QWidget(self.central_widget)
        self.hud_widget.setStyleSheet("background-color: rgba(0, 0, 0, 180); color: white; border-radius: 5px; padding: 5px;")
        hud_layout = QVBoxLayout(self.hud_widget)
        hud_layout.setContentsMargins(10, 5, 10, 5)

        self.hud_filename = QLabel("No File")
        self.hud_filename.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.hud_filename.setAccessibleName("Current Image Filename")
        self.hud_filename.setAccessibleDescription("Displays the current image file name and index.")
        self.hud_filename.setToolTip("Current file name and index in the directory.")

        self.hud_filepath = QLabel("")
        self.hud_filepath.setStyleSheet("font-size: 10px; color: #aaaaaa;")
        self.hud_filepath.setAccessibleName("Current Image Filepath")
        self.hud_filepath.setAccessibleDescription("Displays the full absolute path of the loaded image.")
        self.hud_filepath.setToolTip("Full absolute path to the current image.")

        self.hud_status = QLabel("Ready")
        self.hud_status.setStyleSheet("font-size: 12px; color: #55ff55;")
        self.hud_status.setAccessibleName("Background Task Status")
        self.hud_status.setAccessibleDescription("Displays real-time background processing operations.")
        self.hud_status.setToolTip("Current background operation status.")

        self.hud_progress = QProgressBar()
        self.hud_progress.setTextVisible(False)
        self.hud_progress.setFixedHeight(4)
        self.hud_progress.hide()
        self.hud_progress.setAccessibleName("Background Task Progress")

        hud_layout.addWidget(self.hud_filename)
        hud_layout.addWidget(self.hud_filepath)
        hud_layout.addWidget(self.hud_status)
        hud_layout.addWidget(self.hud_progress)
        self.hud_widget.hide()

        # Viewer
        self.viewer = ImageViewer(self)
        self.viewer.hide()

        self.empty_label = QLabel("No images loaded. Press 'S' to open settings.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setAccessibleName("Image Viewer Display State")
        self.empty_label.setAccessibleDescription("Main image container displaying loaded image or empty state notification.")
        self.empty_label.setToolTip("The source directory has no supported images. Open settings to configure a valid source path.")
        font_size = self.settings.get('ui', 'font_size') or 24
        self.empty_label.setStyleSheet(f"font-size: {font_size}px; padding: 20px;")

        self.layout.addWidget(self.empty_label)
        self.layout.addWidget(self.viewer)

        self.setup_menu()
        self.statusBar().showMessage("Ready", 3000)

    def apply_theme(self) -> None:
        """Applies visual theme palette."""
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
            app.setPalette(app.style().standardPalette())
            return

        app.setPalette(palette)

    def setup_menu(self) -> None:
        """Sets up application menu bar with accessible shortcuts."""
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        settings_action = QAction("&Settings", self)
        settings_action.setShortcut(QKeySequence("S"))
        settings_action.setToolTip("Open the configuration menu to adjust directories, AI settings, and hotkeys. (Shortcut: S)")
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        reload_action = QAction("&Reload Images", self)
        reload_action.setShortcut(QKeySequence("R"))
        reload_action.setToolTip("Refresh the current directory to discover new images or update the queue. (Shortcut: R)")
        reload_action.triggered.connect(self.load_images)
        file_menu.addAction(reload_action)

        undo_action = QAction("&Undo Last Action", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.setToolTip("Revert the last file move, copy, or trash operation. (Shortcut: Ctrl+Z)")
        undo_action.triggered.connect(self.undo_last_action)
        file_menu.addAction(undo_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Esc"))
        exit_action.setToolTip("Safely close the application and stop background workers. (Shortcut: Esc)")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu.addMenu("&View")

        self.locked_zoom_action = QAction("&Lock Pan/Zoom", self, checkable=True)
        self.locked_zoom_action.setShortcut(QKeySequence("L"))
        self.locked_zoom_action.setToolTip("Keep current zoom level and pan position when navigating between images. (Shortcut: L)")
        self.locked_zoom_action.triggered.connect(self.toggle_locked_zoom)
        view_menu.addAction(self.locked_zoom_action)

        zen_action = QAction("&Zen Mode", self)
        zen_action.setShortcut(QKeySequence("Z"))
        zen_action.setToolTip("Hide all UI elements for immersive viewing. (Shortcut: Z)")
        zen_action.triggered.connect(self.toggle_zen_mode)
        view_menu.addAction(zen_action)

    def toggle_zen_mode(self) -> None:
        self.zen_mode = not self.zen_mode
        if self.zen_mode:
            self.menuBar().hide()
            self.statusBar().hide()
            self.showFullScreen()
        else:
            self.menuBar().show()
            self.statusBar().show()
            if not self.settings.get('ui', 'fullscreen'):
                self.showMaximized()

    def toggle_locked_zoom(self, checked: bool) -> None:
        self.viewer.locked_zoom_pan = checked
        if not checked:
            self.viewer.fit_to_window()

    def load_images(self) -> None:
        """Loads supported image files from configured source directory."""
        if hasattr(self, 'loader'):
            self.loader.clear_tasks()
        self.pixmap_cache.clear()

        src_dir = self.settings.get('directories', 'source')
        if not src_dir or not os.path.isdir(src_dir):
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText("Source directory not configured or invalid.")
            return

        supported_formats = [fmt.data().decode().lower() for fmt in QImageReader.supportedImageFormats()]
        self.images = []

        try:
            for f in os.listdir(src_dir):
                filepath = os.path.join(src_dir, f)
                if os.path.isfile(filepath):
                    ext = os.path.splitext(f)[1][1:].lower()
                    if ext in supported_formats:
                        self.images.append(filepath)

            self.images.sort()
            if self.images:
                self.current_index = 0
                self.show_image()
            else:
                self.current_index = -1
                self.viewer.hide()
                self.empty_label.show()
                self.empty_label.setText("No images found in the source directory.")

        except OSError as e:
            logger.error(f"Failed to load images from {src_dir}: {e}")
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText(f"Error reading source directory:\n{e}")

    def show_image(self) -> None:
        """Displays image at current index."""
        if not self.images or self.current_index < 0 or self.current_index >= len(self.images):
            self.viewer.hide()
            self.hud_widget.hide()
            self.empty_label.show()
            self.empty_label.setText("All done!")
            self.setWindowTitle("Image Sorter - Enterprise")
            return

        filepath = self.images[self.current_index]

        if filepath in self.pixmap_cache:
            pixmap = self.pixmap_cache[filepath]
        else:
            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                self.pixmap_cache[filepath] = pixmap

        if pixmap.isNull():
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText(f"Failed to load image: {os.path.basename(filepath)}")
            logger.warning(f"QPixmap failed to load valid image data from {filepath}")
            return

        self.empty_label.hide()
        self.viewer.show()
        self.viewer.set_image(pixmap)

        filename = os.path.basename(filepath)
        self.empty_label.setAccessibleName(f"Image {self.current_index + 1} of {len(self.images)}: {filename}")
        self.setWindowTitle(f"Image Sorter - {filename} ({self.current_index + 1}/{len(self.images)})")

        self.preload_adjacent_images()

    def keyPressEvent(self, event: QEvent) -> None:
        """Handles keyboard navigation and sorting with focus isolation to prevent input collision."""
        focus_widget = QApplication.focusWidget()
        if focus_widget and isinstance(focus_widget, (QLineEdit, QTextEdit, QSpinBox, QComboBox)):
            super().keyPressEvent(event)
            return

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

        if key == Qt.Key.Key_C:
            self.viewer.toggle_clipping_warnings()
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
            action = config.get('action', 'move')
            folder = config.get('folder')

            if not folder:
                self.statusBar().showMessage(f"Warning: No destination folder set for hotkey '{key_str}'", 4000)
                return

            self.worker.add_task(action, filepath, folder)

            if config.get('auto_advance', True):
                if action in ('move', 'trash'):
                    self.next_image_after_action()
                else:
                    self.current_index += 1
                    self.show_image()

    def next_image_after_action(self) -> None:
        """Advances to next image after action."""
        if 0 <= self.current_index < len(self.images):
            self.images.pop(self.current_index)
        self.show_image()

    def on_undo_record_received(self, data: Dict[str, Any]) -> None:
        """Receives UndoToken from background worker."""
        self.history.append(data)
        if len(self.history) > 50:
            self.history.pop(0)

    def undo_last_action(self) -> None:
        """Reverts last move, copy, or trash operation using UndoToken."""
        if not self.history:
            self.statusBar().showMessage("Nothing to undo.", 3000)
            return

        last_action = self.history.pop()
        action_type = last_action.get('action') or last_action.get('type')
        current_path = last_action.get('current') or last_action.get('new')
        original_path = last_action.get('original')

        if action_type in ('move', 'trash'):
            if current_path and original_path:
                self.worker.add_task('undo_move', current_path, original_path)
                self.statusBar().showMessage("Undoing move... Press 'R' to reload folder.", 3000)

        elif action_type == 'copy':
            if current_path:
                self.worker.add_task('undo_copy', current_path)
                self.statusBar().showMessage("Undoing copy...", 3000)

    def open_settings(self) -> None:
        """Opens settings configuration window."""
        self.settings_window = SettingsWindow(self.settings)
        self.settings_window.destroyed.connect(self.on_settings_closed)
        self.settings_window.show()

    def on_settings_closed(self) -> None:
        """Called when settings window closes."""
        self.apply_theme()
        font_size = self.settings.get('ui', 'font_size') or 24
        self.empty_label.setStyleSheet(f"font-size: {font_size}px; padding: 20px;")
        self.worker.refresh_settings()
        self.load_images()

    def on_worker_progress(self, msg: str) -> None:
        """Displays progress messages in status bar."""
        self.statusBar().showMessage(msg, 3000)

    def on_worker_error(self, filepath: str, error: str) -> None:
        """Displays error messages in status bar."""
        self.statusBar().showMessage(f"Error: {error}", 5000)

    def closeEvent(self, event: QEvent) -> None:
        """Ensures background threads are safely stopped upon window close."""
        self.worker.stop()
        if hasattr(self, 'loader'):
            self.loader.stop()
        super().closeEvent(event)
