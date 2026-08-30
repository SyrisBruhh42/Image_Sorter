import os
import shutil
import uuid
from dataclasses import dataclass
from collections import OrderedDict
from typing import List, Dict, Optional, Any
import psutil
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QVBoxLayout, QHBoxLayout, QWidget, QMessageBox, QMenu,
    QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QProgressBar,
    QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox, QKeySequenceEdit
)
from PyQt6.QtGui import (
    QPixmap, QImageReader, QKeySequence, QAction, QPalette, QColor, QPainter,
    QTransform, QCursor, QImage, QWheelEvent, QMouseEvent
)
try:
    from PyQt6.QtGui import QAccessible, QAccessibleEvent  # type: ignore
    HAS_QACCESSIBLE = True
except ImportError:
    HAS_QACCESSIBLE = False
from PyQt6.QtCore import Qt, QSize, QEvent, QObject
from .ui_settings import SettingsWindow
from .queue_worker import QueueWorker
from .settings_manager import SettingsManager
from .logger import logger
from .image_loader import ImageLoader
import numpy as np


class ImageViewer(QGraphicsView):
    """
    Custom QGraphicsView for displaying images with pan/zoom and clipping analysis.
    """
    MIN_ZOOM: float = 0.05
    MAX_ZOOM: float = 32.0

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

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

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
        """Calculates and applies clipping overlay using downsampled vectorized numpy math."""
        if self.original_pixmap.isNull():
            return

        if self.clipping_pixmap.isNull():
            full_img = self.original_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)

            w, h = full_img.width(), full_img.height()
            max_w, max_h = 2560, 1440
            scale = min(1.0, max_w / max(1, w), max_h / max(1, h))

            if scale < 1.0:
                calc_img = full_img.scaled(
                    int(w * scale), int(h * scale),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation
                )
            else:
                calc_img = full_img

            ptr = calc_img.bits()
            ptr.setsize(calc_img.height() * calc_img.bytesPerLine())
            arr = np.frombuffer(ptr, np.uint8).reshape((calc_img.height(), calc_img.bytesPerLine() // 4, 4))

            b = arr[..., 0].astype(np.float32)
            g = arr[..., 1].astype(np.float32)
            r = arr[..., 2].astype(np.float32)

            y = 0.2126 * r + 0.7152 * g + 0.0722 * b
            max_c = np.maximum(np.maximum(r, g), b)
            min_c = np.minimum(np.minimum(r, g), b)

            overexposed = (y > 250) | (max_c > 254)
            underexposed = (y < 5) | (min_c < 5)

            overlay = QImage(calc_img.width(), calc_img.height(), QImage.Format.Format_ARGB32)
            overlay.fill(Qt.GlobalColor.transparent)

            ptr_out = overlay.bits()
            ptr_out.setsize(overlay.height() * overlay.bytesPerLine())
            arr_out = np.frombuffer(ptr_out, np.uint8).reshape((overlay.height(), overlay.bytesPerLine() // 4, 4))

            # Little-Endian ARGB32 (BGRA byte order): Red overlay [0, 0, 255, 255], Blue overlay [255, 0, 0, 255]
            arr_out[overexposed] = [0, 0, 255, 255]
            arr_out[underexposed] = [255, 0, 0, 255]

            if scale < 1.0:
                overlay = overlay.scaled(
                    w, h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.FastTransformation
                )

            result_img = full_img.copy()
            painter = QPainter(result_img)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(0, 0, overlay)
            painter.end()

            self.clipping_pixmap = QPixmap.fromImage(result_img)

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
        """Handles mouse wheel zooming bounded between MIN_ZOOM and MAX_ZOOM."""
        if self.locked_zoom_pan:
            return

        current_scale = self.transform().m11()
        factor = self.zoom_factor if event.angleDelta().y() > 0 else (1.0 / self.zoom_factor)
        new_scale = current_scale * factor

        if new_scale < self.MIN_ZOOM:
            factor = self.MIN_ZOOM / current_scale
        elif new_scale > self.MAX_ZOOM:
            factor = self.MAX_ZOOM / current_scale

        if abs(factor - 1.0) > 1e-5:
            self.scale(factor, factor)
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


@dataclass
class PendingOp:
    op_id: str
    action: str  # 'move', 'trash', 'undo_move', 'undo_trash', 'undo_copy'
    src_path: str  # canonical path
    raw_src_path: str
    original_index: int
    load_generation: int
    dest_folder: Optional[str] = None
    dest_path: Optional[str] = None
    original_path: Optional[str] = None
    raw_original_path: Optional[str] = None
    state: str = "pending"  # 'pending', 'finished', 'error'
    undo_token: Optional[Dict[str, Any]] = None
    finished_received: bool = False
    undo_record_received: bool = False


def _canonical_path(path: str) -> str:
    if not path:
        return ""
    try:
        return os.path.realpath(os.path.abspath(os.path.normpath(path)))
    except Exception:
        return os.path.normpath(path)


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
        self.worker.signals.finished.connect(self.on_worker_finished)
        self.worker.signals.error.connect(self.on_worker_error)
        self.worker.signals.undo_record.connect(self.on_undo_record_received)

        self.images: List[str] = []
        self.current_index: int = -1
        self.history: List[Dict[str, Any]] = []
        self.zen_mode: bool = False

        self.load_generation: int = 0
        self.pending_ops: Dict[str, PendingOp] = {}

        self.pixmap_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self.cache_bytes: int = 0
        self.max_cache_items: int = 25
        self._update_max_cache_bytes()

        self.loader = ImageLoader()
        self.loader.image_loaded.connect(self.on_image_preloaded)
        self.loader.start()

        self.apply_theme()
        self.init_ui()
        self.load_images()

    def clear_pixmap_cache(self) -> None:
        """Clears the pixmap cache and resets byte counter."""
        self.pixmap_cache.clear()
        self.cache_bytes = 0

    def _update_max_cache_bytes(self) -> None:
        custom_mb = self.settings.get('advanced', 'cache_size_mb')
        if custom_mb and str(custom_mb).isdigit():
            self.max_cache_bytes = int(custom_mb) * 1024 * 1024
        else:
            total_ram = psutil.virtual_memory().total
            self.max_cache_bytes = min(256 * 1024 * 1024, int(0.20 * total_ram))

    def _add_pixmap_to_cache(self, filepath: str, pixmap: QPixmap) -> None:
        if filepath in self.pixmap_cache:
            old_pixmap = self.pixmap_cache.pop(filepath)
            self.cache_bytes -= (old_pixmap.width() * old_pixmap.height() * 4)

        pixmap_size = pixmap.width() * pixmap.height() * 4
        self.pixmap_cache[filepath] = pixmap
        self.cache_bytes += pixmap_size

        while len(self.pixmap_cache) > 1 and (self.cache_bytes > self.max_cache_bytes or len(self.pixmap_cache) > self.max_cache_items):
            old_k, old_pm = self.pixmap_cache.popitem(last=False)
            self.cache_bytes -= (old_pm.width() * old_pm.height() * 4)

    def _get_pixmap_from_cache(self, filepath: str) -> Optional[QPixmap]:
        if filepath in self.pixmap_cache:
            self.pixmap_cache.move_to_end(filepath)
            return self.pixmap_cache[filepath]
        return None

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

    def announce_accessibility_event(self, widget: QWidget, message: str) -> None:
        """Announces accessibility event to screen readers using QAccessible."""
        widget.setAccessibleDescription(message)
        if HAS_QACCESSIBLE:
            try:
                event = QAccessibleEvent(widget, QAccessible.Event.Alert)
                QAccessible.updateAccessibility(event)
            except Exception:
                pass

    def on_image_preloaded(self, filepath: str, img: QImage) -> None:
        if filepath not in self.pixmap_cache:
            pixmap = QPixmap.fromImage(img)
            if not pixmap.isNull():
                self._add_pixmap_to_cache(filepath, pixmap)

    def preload_adjacent_images(self) -> None:
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
        """Applies visual theme palette with WCAG AAA contrast compliance."""
        theme = self.settings.get('ui', 'theme') or 'Dark'
        app = QApplication.instance()
        palette = QPalette()

        if theme == 'Dark':
            palette.setColor(QPalette.ColorRole.Window, QColor("#181818"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#FFFFFF"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#242424"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2D2D2D"))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#181818"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFFFF"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#FFFFFF"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#2D2D2D"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#FFFFFF"))
            palette.setColor(QPalette.ColorRole.BrightText, QColor("#FF4D4D"))
            palette.setColor(QPalette.ColorRole.Link, QColor("#3B82F6"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#3B82F6"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
            app.setStyleSheet("QWidget:focus { outline: 2px solid #3B82F6; }")
        elif theme == 'High Contrast':
            palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#FFFF00"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFF00"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#FFFF00"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#FFFF00"))
            palette.setColor(QPalette.ColorRole.BrightText, QColor("#FF0000"))
            palette.setColor(QPalette.ColorRole.Link, QColor("#00FFFF"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#00FFFF"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
            app.setStyleSheet("QWidget { border: 3px solid #FFFF00; } QWidget:focus { outline: 3px solid #00FFFF; }")
        else: # Light theme
            palette.setColor(QPalette.ColorRole.Window, QColor("#F8F9FA"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#0F172A"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#F1F5F9"))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#F8F9FA"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#0F172A"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#0F172A"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#E2E8F0"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#0F172A"))
            palette.setColor(QPalette.ColorRole.BrightText, QColor("#DC2626"))
            palette.setColor(QPalette.ColorRole.Link, QColor("#1D4ED8"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#1D4ED8"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
            app.setStyleSheet("QWidget:focus { outline: 2px solid #1D4ED8; }")

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
        self.clear_pixmap_cache()
        self.load_generation += 1
        self.pending_ops.clear()

        src_dir = self.settings.get('directories', 'source')
        if not src_dir or not os.path.isdir(src_dir):
            self.viewer.hide()
            self.empty_label.show()
            self.empty_label.setText("Source directory not configured or invalid.")
            return

        supported_formats = {fmt.data().decode().lower() for fmt in QImageReader.supportedImageFormats()}
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
            self.announce_accessibility_event(self.empty_label, "All done! No remaining images in queue.")
            return

        filepath = self.images[self.current_index]

        pixmap = self._get_pixmap_from_cache(filepath)
        if pixmap is None:
            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                self._add_pixmap_to_cache(filepath, pixmap)

        if pixmap is None or pixmap.isNull():
            self.viewer.hide()
            self.empty_label.show()
            err_msg = f"Failed to load image: {os.path.basename(filepath)}"
            self.empty_label.setText(err_msg)
            self.announce_accessibility_event(self.empty_label, err_msg)
            logger.warning(f"QPixmap failed to load valid image data from {filepath}")
            return

        self.empty_label.hide()
        self.viewer.show()
        self.viewer.set_image(pixmap)

        filename = os.path.basename(filepath)
        accessible_desc = f"Image {self.current_index + 1} of {len(self.images)}: {filename}"
        self.viewer.setAccessibleName(accessible_desc)
        self.empty_label.setAccessibleName(accessible_desc)
        self.setWindowTitle(f"Image Sorter - {filename} ({self.current_index + 1}/{len(self.images)})")
        self.announce_accessibility_event(self.viewer, accessible_desc)

        self.preload_adjacent_images()

    def _is_path_pending(self, filepath: str) -> bool:
        can_p = _canonical_path(filepath)
        for op in self.pending_ops.values():
            if op.load_generation == self.load_generation and op.state == "pending":
                if op.src_path == can_p:
                    return True
        return False

    def _find_next_non_pending_index(self, start_idx: int, direction: int = 1) -> int:
        if not self.images:
            return -1
        if direction >= 0:
            for i in range(max(0, start_idx), len(self.images)):
                if not self._is_path_pending(self.images[i]):
                    return i
        else:
            for i in range(min(start_idx, len(self.images) - 1), -1, -1):
                if not self._is_path_pending(self.images[i]):
                    return i
        return -1

    def advance_ui_after_pending_action(self) -> None:
        """Advances to nearest non-pending image after initiating an action."""
        next_idx = self._find_next_non_pending_index(self.current_index, direction=1)
        if next_idx == -1:
            next_idx = self._find_next_non_pending_index(self.current_index, direction=-1)

        if next_idx != -1:
            self.current_index = next_idx
        self.show_image()

    def trigger_file_action(self, action: str, filepath: str, dest_folder: Optional[str] = None) -> Optional[str]:
        """Submits a move or trash action with transactional pending op tracking and advances UI."""
        if not filepath:
            return None

        can_target = _canonical_path(filepath)
        orig_idx = -1
        for idx, img in enumerate(self.images):
            if _canonical_path(img) == can_target:
                orig_idx = idx
                break

        if orig_idx == -1:
            return None

        op_id = str(uuid.uuid4())
        pending_op = PendingOp(
            op_id=op_id,
            action=action,
            src_path=can_target,
            raw_src_path=filepath,
            original_index=orig_idx,
            load_generation=self.load_generation,
            dest_folder=_canonical_path(dest_folder) if dest_folder else None,
            state="pending"
        )
        self.pending_ops[op_id] = pending_op

        self.worker.add_task(action, filepath, dest_folder)

        if action == 'trash':
            trash_folder = self.settings.get('directories', 'trash')
            if not trash_folder or not os.path.isdir(trash_folder):
                msg = "Moved to system trash. Image Sorter Undo is unavailable for this item."
                self.statusBar().showMessage(msg, 4000)
                self.announce_accessibility_event(self.central_widget, msg)
            else:
                self.announce_accessibility_event(self.central_widget, f"Moved {os.path.basename(filepath)} to trash folder.")
        elif action == 'move':
            self.announce_accessibility_event(self.central_widget, f"Executed move for {os.path.basename(filepath)} to {dest_folder}.")

        self.advance_ui_after_pending_action()
        return op_id

    def is_input_focused(self) -> bool:
        """Determines if any input or editor widget currently has keyboard focus."""
        focus_widget = QApplication.focusWidget()
        if not focus_widget:
            return False
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox, QKeySequenceEdit)):
            return True
        if hasattr(focus_widget, "isReadOnly") and not focus_widget.isReadOnly():
            return True
        return False

    def keyPressEvent(self, event: QEvent) -> None:
        """Handles keyboard navigation and sorting adhering strictly to the Precedence Matrix."""
        if self.is_input_focused():
            super().keyPressEvent(event)
            return

        key = event.key()
        modifiers = event.modifiers()
        key_str = event.text().upper()

        # Level 0 (System Shortcuts with Modifiers)
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                self.undo_last_action()
                return
            elif key == Qt.Key.Key_S:
                self.open_settings()
                return
            elif key == Qt.Key.Key_C:
                if 0 <= self.current_index < len(self.images):
                    filepath = self.images[self.current_index]
                    QApplication.clipboard().setText(filepath)
                    msg = f"Copied filepath to clipboard: {os.path.basename(filepath)}"
                    self.statusBar().showMessage(msg, 3000)
                    self.announce_accessibility_event(self.central_widget, msg)
                return
            elif key == Qt.Key.Key_Q:
                self.close()
                return

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

        # Level 1 (Direct Navigation without Modifiers)
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Right):
            if 0 <= self.current_index < len(self.images):
                self.current_index += 1
                self.show_image()
            return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            if self.current_index > 0:
                self.current_index -= 1
                self.show_image()
            return

        if key == Qt.Key.Key_Delete:
            if 0 <= self.current_index < len(self.images):
                filepath = self.images[self.current_index]
                self.trigger_file_action('trash', filepath)
            return

        # Level 2 (Custom User Hotkeys - evaluated ONLY when NoModifier)
        if modifiers == Qt.KeyboardModifier.NoModifier:
            hotkeys = self.settings.get('hotkeys') or {}
            if key_str and key_str in hotkeys:
                if 0 <= self.current_index < len(self.images):
                    filepath = self.images[self.current_index]
                    config = hotkeys[key_str]
                    action = config.get('action', 'move')
                    folder = config.get('folder')

                    if action in ('move', 'trash'):
                        if action == 'move' and not folder:
                            msg = f"Warning: No destination folder set for hotkey '{key_str}'"
                            self.statusBar().showMessage(msg, 4000)
                            self.announce_accessibility_event(self.central_widget, msg)
                            return
                        self.trigger_file_action(action, filepath, folder)
                    else:
                        self.worker.add_task(action, filepath, folder)
                        self.announce_accessibility_event(self.central_widget, f"Executed {action} for {os.path.basename(filepath)} to {folder}.")
                        if config.get('auto_advance', True):
                            self.current_index += 1
                            self.show_image()
                return

            # Level 3 (Fallback Letters)
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

    def next_image_after_action(self) -> None:
        """Deprecated legacy helper retained for backward compatibility."""
        self.advance_ui_after_pending_action()

    def _find_matching_pending_op(self, path: str, action_types: Optional[List[str]] = None) -> Optional[PendingOp]:
        can_p = _canonical_path(path)
        for op in self.pending_ops.values():
            if op.load_generation == self.load_generation and op.state == "pending":
                if action_types and op.action not in action_types:
                    continue
                if op.src_path == can_p or (op.dest_path and _canonical_path(op.dest_path) == can_p):
                    return op
        return None

    def _match_pending_op(self, path: str) -> Optional[PendingOp]:
        can_p = _canonical_path(path)
        for op in self.pending_ops.values():
            if op.load_generation != self.load_generation or op.state != "pending":
                continue
            if op.src_path == can_p:
                return op
            if op.dest_path and _canonical_path(op.dest_path) == can_p:
                return op
            if op.original_path and _canonical_path(op.original_path) == can_p:
                return op
            if op.dest_folder and _canonical_path(os.path.dirname(can_p)) == op.dest_folder:
                base_src = os.path.splitext(os.path.basename(op.src_path))[0]
                base_fin = os.path.splitext(os.path.basename(can_p))[0]
                if base_fin == base_src or base_fin.startswith(f"{base_src}_"):
                    return op
        return None

    def on_undo_record_received(self, data: Dict[str, Any]) -> None:
        """Receives UndoToken from background worker and correlates with pending op."""
        orig_p = data.get('original') or ''
        curr_p = data.get('current') or data.get('new') or ''
        can_orig = _canonical_path(orig_p)
        can_curr = _canonical_path(curr_p)

        target_op = self._match_pending_op(can_orig) or self._match_pending_op(can_curr)

        if target_op:
            if target_op.undo_record_received:
                return  # Duplicate signal ignored
            target_op.undo_record_received = True
            target_op.undo_token = data
            if target_op.action in ('move', 'trash'):
                target_op.dest_path = can_curr
            if target_op.finished_received and target_op.state == "pending":
                target_op.state = "finished"
                if not any(t.get('token_id') == data.get('token_id') for t in self.history if 'token_id' in t):
                    self.history.append(data)
                    if len(self.history) > 50:
                        self.history.pop(0)
        else:
            # Uncorrelated token
            if not any(t.get('token_id') == data.get('token_id') for t in self.history if 'token_id' in t):
                self.history.append(data)
                if len(self.history) > 50:
                    self.history.pop(0)

    def on_worker_finished(self, finished_path: str) -> None:
        """Handles worker task completion with transactional queue updates."""
        can_finished = _canonical_path(finished_path)

        target_op = self._match_pending_op(can_finished)

        if target_op:
            if target_op.finished_received:
                return  # Duplicate finished signal ignored
            target_op.finished_received = True
            if target_op.action in ('move', 'trash'):
                target_op.dest_path = can_finished
                trash_folder = self.settings.get('directories', 'trash')
                is_system_trash = (target_op.action == 'trash' and (not trash_folder or not os.path.isdir(trash_folder)))

                if target_op.undo_record_received or is_system_trash:
                    target_op.state = "finished"
                    if target_op.undo_token and not any(t.get('token_id') == target_op.undo_token.get('token_id') for t in self.history if 'token_id' in t):
                        self.history.append(target_op.undo_token)
                        if len(self.history) > 50:
                            self.history.pop(0)
                # Remove source image from visible queue exactly once
                self.remove_image_from_queue(target_op.src_path)

            elif target_op.action in ('undo_move', 'undo_trash'):
                target_op.state = "finished"
                # Reinsert restored image at original recorded index without duplicates
                restored_path = target_op.raw_original_path or finished_path
                self.reinsert_image_at_index(restored_path, target_op.original_index)

            elif target_op.action == 'undo_copy':
                target_op.state = "finished"

    def on_worker_error(self, filepath: str, error: str) -> None:
        """Displays error messages, restores state on operation failures."""
        msg = f"Error processing {os.path.basename(filepath)}: {error}"
        self.statusBar().showMessage(msg, 5000)
        self.announce_accessibility_event(self.central_widget, msg)

        target_op = self._match_pending_op(filepath)

        if target_op:
            target_op.state = "error"

            if target_op.action in ('move', 'trash'):
                # Ensure the image is present in self.images and clear pending state
                self.reinsert_image_at_index(target_op.raw_src_path, target_op.original_index)
                self.show_image()

            elif target_op.action in ('undo_move', 'undo_trash', 'undo_copy'):
                # Restore failed undo token back to history stack
                if target_op.undo_token:
                    # Restore token at original history position (pop returned token to top if not present)
                    if not any(t.get('token_id') == target_op.undo_token.get('token_id') for t in self.history if 'token_id' in t):
                        self.history.append(target_op.undo_token)

    def remove_image_from_queue(self, canonical_path: str) -> None:
        """Removes an image matching canonical_path from self.images exactly once."""
        found_idx = -1
        for idx, img in enumerate(self.images):
            if _canonical_path(img) == canonical_path:
                found_idx = idx
                break

        if found_idx != -1:
            curr_img = self.images[self.current_index] if 0 <= self.current_index < len(self.images) else None
            self.images.pop(found_idx)
            if curr_img:
                can_curr = _canonical_path(curr_img)
                new_idx = -1
                for idx, img in enumerate(self.images):
                    if _canonical_path(img) == can_curr:
                        new_idx = idx
                        break
                if new_idx != -1:
                    self.current_index = new_idx
                else:
                    self.current_index = min(found_idx, len(self.images) - 1)
            else:
                self.current_index = min(self.current_index, len(self.images) - 1)
            self.show_image()

    def reinsert_image_at_index(self, filepath: str, original_index: int) -> None:
        """Reinserts filepath at original_index (clamped) without introducing duplicate entries."""
        can_p = _canonical_path(filepath)
        for img in self.images:
            if _canonical_path(img) == can_p:
                return  # Duplicate detected, do not reinsert

        clamped_idx = max(0, min(original_index, len(self.images)))
        self.images.insert(clamped_idx, filepath)

        if self.current_index >= clamped_idx:
            self.current_index += 1
        self.show_image()

    def undo_last_action(self) -> None:
        """Reverts last move, copy, or trash operation using UndoToken."""
        if not self.history:
            self.statusBar().showMessage("Nothing to undo.", 3000)
            return

        last_action = self.history.pop()
        action_type = last_action.get('action') or last_action.get('type')
        current_path = last_action.get('current') or last_action.get('new')
        original_path = last_action.get('original')

        can_orig = _canonical_path(original_path) if original_path else ""
        orig_idx = 0
        # Determine original index from pending ops if recorded previously
        for op in self.pending_ops.values():
            if op.src_path == can_orig:
                orig_idx = op.original_index
                break

        op_id = str(uuid.uuid4())

        if action_type in ('move', 'trash'):
            if current_path and original_path:
                pending_op = PendingOp(
                    op_id=op_id,
                    action=f"undo_{action_type}",
                    src_path=_canonical_path(current_path),
                    raw_src_path=current_path,
                    original_index=orig_idx,
                    load_generation=self.load_generation,
                    dest_folder=_canonical_path(original_path),
                    original_path=can_orig,
                    raw_original_path=original_path,
                    state="pending",
                    undo_token=last_action
                )
                self.pending_ops[op_id] = pending_op
                self.worker.add_task('undo_move', current_path, original_path)
                msg = f"Restoring {os.path.basename(original_path)} to original location..."
                self.statusBar().showMessage(msg, 3000)
                self.announce_accessibility_event(self.central_widget, msg)

        elif action_type == 'copy':
            if current_path:
                pending_op = PendingOp(
                    op_id=op_id,
                    action="undo_copy",
                    src_path=_canonical_path(current_path),
                    raw_src_path=current_path,
                    original_index=orig_idx,
                    load_generation=self.load_generation,
                    state="pending",
                    undo_token=last_action
                )
                self.pending_ops[op_id] = pending_op
                self.worker.add_task('undo_copy', current_path)
                msg = f"Undoing copy of {os.path.basename(current_path)}..."
                self.statusBar().showMessage(msg, 3000)
                self.announce_accessibility_event(self.central_widget, msg)

    def open_settings(self) -> None:
        """Opens settings configuration window as a modal dialog."""
        dialog = SettingsWindow(self.settings, parent=self)
        if dialog.exec():
            self.on_settings_closed()

    def on_settings_closed(self) -> None:
        """Called when settings window closes."""
        self._update_max_cache_bytes()
        self.apply_theme()
        font_size = self.settings.get('ui', 'font_size') or 24
        self.empty_label.setStyleSheet(f"font-size: {font_size}px; padding: 20px;")
        self.worker.refresh_settings()
        self.load_images()

    def on_worker_progress(self, msg: str) -> None:
        """Displays progress messages in status bar."""
        self.statusBar().showMessage(msg, 3000)

    def closeEvent(self, event: QEvent) -> None:
        """Ensures background threads are safely stopped upon window close."""
        self.worker.stop()
        if hasattr(self, 'loader'):
            self.loader.stop()
        super().closeEvent(event)
