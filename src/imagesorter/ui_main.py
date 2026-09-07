from __future__ import annotations

import os
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import psutil
from PyQt6.QtGui import (
    QAction,
    QColor,
    QImage,
    QImageReader,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPalette,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtGui import QAccessible, QAccessibleEvent  # type: ignore
    HAS_QACCESSIBLE = True
except ImportError:
    HAS_QACCESSIBLE = False
import numpy as np
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSlot

from .image_loader import ImageLoader
from .logger import logger
from .queue_worker import QueueWorker
from .settings_manager import SettingsManager
from .ui_settings import SettingsWindow


class ImageViewer(QGraphicsView):
    """
    Custom QGraphicsView for displaying images with pan/zoom and clipping analysis.
    """
    MIN_ZOOM: float = 0.05
    MAX_ZOOM: float = 32.0

    def __init__(self, parent: QWidget | None = None) -> None:
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
    action: str  # 'move', 'copy', 'trash', 'undo_move', 'undo_trash', 'undo_copy'
    src_path: str  # canonical path
    raw_src_path: str
    original_index: int
    load_generation: int
    dest_folder: str | None = None
    dest_path: str | None = None
    original_path: str | None = None
    raw_original_path: str | None = None
    state: str = "pending"  # 'pending', 'finished', 'error'
    undo_token: dict[str, Any] | None = None
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
    def __init__(self, settings_manager: SettingsManager, initial_paths: list[str] | None = None) -> None:
        super().__init__()
        self.settings = settings_manager
        self.transient_paths: list[str] | None = None

        QApplication.instance().installEventFilter(self)

        self.worker = QueueWorker(self.settings)
        self.worker.signals.progress.connect(self.on_worker_progress)
        self.worker.signals.operation_result.connect(self.on_operation_result)

        self.images: list[str] = []
        self.current_index: int = -1
        self.history: list[dict[str, Any]] = []
        self.zen_mode: bool = False

        self.load_generation: int = 0
        self.pending_ops: dict[str, PendingOp] = {}
        self.pending_decoder_requests: dict[str, tuple[str, int]] = {}  # req_id -> (filepath, load_generation)

        self.pixmap_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self.cache_bytes: int = 0
        self.max_cache_items: int = 25
        self._update_max_cache_bytes()

        self.loader = ImageLoader()
        self.loader.image_ready.connect(self.on_image_ready)
        self.loader.start()

        self.apply_theme()
        self.init_ui()

        if initial_paths is not None:
            self.open_paths(initial_paths)
        else:
            self.load_images()

    def open_paths(self, paths: list[str]) -> None:
        """
        SHARED LAUNCH CONTRACT v1:
        Sets transient input view of image paths without updating persistent settings.
        """
        valid_paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
        self.transient_paths = valid_paths
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
            try:
                total_ram = psutil.virtual_memory().total
            except Exception:
                total_ram = 8 * 1024 * 1024 * 1024
            self.max_cache_bytes = min(256 * 1024 * 1024, int(0.20 * total_ram))

    def _add_pixmap_to_cache(self, filepath: str, pixmap: QPixmap) -> None:
        if filepath in self.pixmap_cache:
            old_pixmap = self.pixmap_cache.pop(filepath)
            self.cache_bytes -= (old_pixmap.width() * old_pixmap.height() * 4)

        pixmap_size = pixmap.width() * pixmap.height() * 4
        self.pixmap_cache[filepath] = pixmap
        self.cache_bytes += pixmap_size

        while len(self.pixmap_cache) > 1 and (self.cache_bytes > self.max_cache_bytes or len(self.pixmap_cache) > self.max_cache_items):
            _old_k, old_pm = self.pixmap_cache.popitem(last=False)
            self.cache_bytes -= (old_pm.width() * old_pm.height() * 4)

    def _get_pixmap_from_cache(self, filepath: str) -> QPixmap | None:
        if filepath in self.pixmap_cache:
            self.pixmap_cache.move_to_end(filepath)
            return self.pixmap_cache[filepath]
        return None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
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

    @pyqtSlot(dict)
    def on_image_ready(self, result: dict[str, Any]) -> None:
        """
        SHARED DECODER CONTRACT v1 signal handler:
        Creates QPixmap exclusively on GUI thread from QImage.
        """
        req_id = result.get('request_id')
        gen = result.get('generation', 0)
        filepath = result.get('filepath')
        qimg = result.get('image')

        if gen != self.load_generation:
            return  # Discard stale decoder results

        if req_id in self.pending_decoder_requests:
            _req_fp, req_gen = self.pending_decoder_requests.pop(req_id)
            if req_gen != self.load_generation:
                return

        if qimg is not None and isinstance(qimg, QImage) and not qimg.isNull() and filepath:
            pixmap = QPixmap.fromImage(qimg)
            if not pixmap.isNull():
                self._add_pixmap_to_cache(filepath, pixmap)
                if (0 <= self.current_index < len(self.images) and
                        _canonical_path(self.images[self.current_index]) == _canonical_path(filepath)):
                    self.show_image()

    def preload_adjacent_images(self) -> None:
        """Preloads adjacent images using extended or legacy ImageLoader contract."""
        for offset, prio in [(1, 1), (-1, 2), (2, 3)]:
            idx = self.current_index + offset
            if 0 <= idx < len(self.images):
                fp = self.images[idx]
                if (fp not in self.pixmap_cache and not self._is_path_pending(fp)
                        and not self._has_pending_decode(fp)):
                    req_id = self.loader.add_task(
                        fp, generation=self.load_generation, priority=prio
                    )
                    if req_id:
                        self.pending_decoder_requests[req_id] = (
                            fp, self.load_generation
                        )

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
        self.hud_widget.setStyleSheet("background-color: rgba(0, 0, 0, 180); color: white; border-radius: 5px; padding: 6px;")
        hud_layout = QVBoxLayout(self.hud_widget)
        hud_layout.setContentsMargins(10, 6, 10, 6)

        self.hud_filename = QLabel("No File")
        self.hud_filename.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.hud_filename.setAccessibleName("Current Image Filename")
        self.hud_filename.setAccessibleDescription("Displays current image file name and index.")

        self.hud_details = QLabel("")
        self.hud_details.setStyleSheet("font-size: 11px; color: #dddddd;")
        self.hud_details.setAccessibleName("Current Image Details")

        self.hud_status = QLabel("Ready")
        self.hud_status.setStyleSheet("font-size: 12px; color: #55ff55;")
        self.hud_status.setAccessibleName("Background Task Status")

        self.hud_progress = QProgressBar()
        self.hud_progress.setTextVisible(False)
        self.hud_progress.setFixedHeight(4)
        self.hud_progress.hide()
        self.hud_progress.setAccessibleName("Background Task Progress")

        hud_layout.addWidget(self.hud_filename)
        hud_layout.addWidget(self.hud_details)
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

    def position_hud(self) -> None:
        """Positions HUD overlay anchored at top-right corner of the window."""
        if not hasattr(self, 'hud_widget') or not self.hud_widget.isVisible():
            return
        margin = 15
        self.hud_widget.adjustSize()
        w = self.hud_widget.width()
        self.hud_widget.move(self.central_widget.width() - w - margin, margin + self.menuBar().height())
        self.hud_widget.raise_()

    def resizeEvent(self, event: QEvent) -> None:
        """Handles main window resize events."""
        super().resizeEvent(event)
        self.position_hud()

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
        settings_action.setToolTip("Open configuration menu. (Shortcut: S)")
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        reload_action = QAction("&Reload Images", self)
        reload_action.setToolTip("Refresh current directory. (Shortcut: R)")
        reload_action.triggered.connect(self.load_images)
        file_menu.addAction(reload_action)

        undo_action = QAction("&Undo Last Action", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.setToolTip("Revert last file action. (Shortcut: Ctrl+Z)")
        undo_action.triggered.connect(self.undo_last_action)
        file_menu.addAction(undo_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Esc"))
        exit_action.setToolTip("Safely close application. (Shortcut: Esc)")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu.addMenu("&View")

        self.locked_zoom_action = QAction("&Lock Pan/Zoom", self, checkable=True)
        self.locked_zoom_action.setToolTip("Keep current zoom level. (Shortcut: L)")
        self.locked_zoom_action.triggered.connect(self.toggle_locked_zoom)
        view_menu.addAction(self.locked_zoom_action)

        zen_action = QAction("&Zen Mode", self)
        zen_action.setToolTip("Hide UI elements. (Shortcut: Z)")
        zen_action.triggered.connect(self.toggle_zen_mode)
        view_menu.addAction(zen_action)

        nav_menu = menu.addMenu("&Navigate")

        next_action = QAction("&Next Image", self)
        next_action.setToolTip("Navigate to next image. (Shortcut: D / Right / Space)")
        next_action.triggered.connect(self.navigate_next)
        nav_menu.addAction(next_action)

        prev_action = QAction("&Previous Image", self)
        prev_action.setToolTip("Navigate to previous image. (Shortcut: A / Left / Backspace)")
        prev_action.triggered.connect(self.navigate_prev)
        nav_menu.addAction(prev_action)

        trash_action = QAction("&Trash Image", self)
        trash_action.setToolTip("Trash current image. (Shortcut: X / Delete)")
        trash_action.triggered.connect(self.action_trash_current)
        nav_menu.addAction(trash_action)

    def navigate_next(self) -> None:
        if not self.images:
            return
        next_idx = self._find_next_non_pending_index(self.current_index + 1, direction=1)
        if next_idx != -1:
            self.current_index = next_idx
            self.show_image()

    def navigate_prev(self) -> None:
        if not self.images:
            return
        prev_idx = self._find_next_non_pending_index(self.current_index - 1, direction=-1)
        if prev_idx != -1:
            self.current_index = prev_idx
            self.show_image()

    def action_trash_current(self) -> None:
        if 0 <= self.current_index < len(self.images):
            filepath = self.images[self.current_index]
            self.trigger_file_action('trash', filepath)

    def toggle_zen_mode(self) -> None:
        self.zen_mode = not self.zen_mode
        if self.zen_mode:
            self.menuBar().hide()
            self.statusBar().hide()
            self.hud_widget.hide()
            self.showFullScreen()
        else:
            self.menuBar().show()
            self.statusBar().show()
            self.update_hud()
            if not self.settings.get('ui', 'fullscreen'):
                self.showMaximized()

    def toggle_locked_zoom(self, checked: bool) -> None:
        self.viewer.locked_zoom_pan = checked
        if not checked:
            self.viewer.fit_to_window()

    def load_images(self) -> None:
        """Loads supported image files from transient launch paths or configured source directory."""
        self.load_generation += 1
        self.pending_decoder_requests.clear()
        if hasattr(self, 'loader'):
            self.loader.clear_tasks(new_generation=self.load_generation)
        self.clear_pixmap_cache()

        supported_formats = {fmt.data().decode().lower() for fmt in QImageReader.supportedImageFormats()}

        if self.transient_paths is not None:
            self.images = [p for p in self.transient_paths if os.path.isfile(p) and os.path.splitext(p)[1][1:].lower() in supported_formats]
            if self.images:
                self.current_index = 0
                self.show_image()
            else:
                self.current_index = -1
                self.images = []
                self.viewer.hide()
                self.hud_widget.hide()
                self.empty_label.show()
                self.empty_label.setText("No valid images in transient input paths.")
            return

        src_dir = self.settings.get('directories', 'source')
        if not src_dir or not os.path.isdir(src_dir):
            self.images = []
            self.current_index = -1
            self.viewer.hide()
            self.hud_widget.hide()
            self.empty_label.show()
            self.empty_label.setText("Source directory not configured or invalid.")
            return

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
                self.images = []
                self.viewer.hide()
                self.hud_widget.hide()
                self.empty_label.show()
                self.empty_label.setText("No images found in the source directory.")

        except OSError as e:
            logger.error(f"Failed to load images from {src_dir}: {e}")
            self.images = []
            self.current_index = -1
            self.viewer.hide()
            self.hud_widget.hide()
            self.empty_label.show()
            self.empty_label.setText(f"Error reading source directory:\n{e}")

    def update_hud(self) -> None:
        """Updates and renders HUD overlay status, dimensions, tags, and pending ops."""
        if self.zen_mode or not self.images or self.current_index < 0 or self.current_index >= len(self.images):
            self.hud_widget.hide()
            return

        filepath = self.images[self.current_index]
        filename = os.path.basename(filepath)
        total_count = len(self.images)
        self.hud_filename.setText(f"{filename} ({self.current_index + 1}/{total_count})")

        pixmap = self._get_pixmap_from_cache(filepath)
        dims_str = f"{pixmap.width()}x{pixmap.height()} px" if pixmap and not pixmap.isNull() else "Loading..."

        pending_count = sum(1 for op in self.pending_ops.values() if op.load_generation == self.load_generation and op.state == "pending")
        pending_str = f" | Pending: {pending_count}" if pending_count > 0 else ""

        ai_enabled = self.settings.get('ai_tagger', 'enabled')
        show_tags = self.settings.get('ui', 'show_tags')
        ai_str = ""
        if ai_enabled and show_tags:
            ai_str = " | AI Tagging Active"

        self.hud_details.setText(f"{dims_str}{pending_str}{ai_str}")

        if pending_count > 0:
            self.hud_status.setText("Processing background operations...")
            self.hud_status.setStyleSheet("font-size: 12px; color: #ffaa00;")
        else:
            self.hud_status.setText("Ready")
            self.hud_status.setStyleSheet("font-size: 12px; color: #55ff55;")

        self.hud_widget.show()
        self.position_hud()

    def show_image(self) -> None:
        """Displays image at current index or triggers asynchronous load."""
        non_pending_idx = self._find_next_non_pending_index(self.current_index, direction=1)
        if non_pending_idx == -1:
            non_pending_idx = self._find_next_non_pending_index(self.current_index, direction=-1)

        if non_pending_idx == -1:
            # All items in queue are pending or queue is empty
            self.viewer.hide()
            self.hud_widget.hide()
            self.empty_label.show()
            pending_count = sum(1 for op in self.pending_ops.values() if op.load_generation == self.load_generation and op.state == "pending")
            if pending_count > 0:
                msg = f"Processing remaining background operations ({pending_count} pending)..."
            else:
                msg = "All done! No remaining images in queue."
            self.empty_label.setText(msg)
            self.setWindowTitle("Image Sorter - Enterprise")
            self.announce_accessibility_event(self.empty_label, msg)
            return

        self.current_index = non_pending_idx
        filepath = self.images[self.current_index]

        pixmap = self._get_pixmap_from_cache(filepath)
        if pixmap is None:
            # Asynchronous load via ImageLoader
            if not self._has_pending_decode(filepath):
                requested_id = str(uuid.uuid4())
                req_id = self.loader.add_task(
                    filepath,
                    request_id=requested_id,
                    generation=self.load_generation,
                    priority=0,
                )
                if req_id:
                    self.pending_decoder_requests[req_id] = (
                        filepath, self.load_generation
                    )

        if pixmap is not None and not pixmap.isNull():
            self.empty_label.hide()
            self.viewer.show()
            self.viewer.set_image(pixmap)
            filename = os.path.basename(filepath)
            accessible_desc = f"Image {self.current_index + 1} of {len(self.images)}: {filename}"
            self.viewer.setAccessibleName(accessible_desc)
            self.empty_label.setAccessibleName(accessible_desc)
            self.setWindowTitle(f"Image Sorter - {filename} ({self.current_index + 1}/{len(self.images)})")
            self.announce_accessibility_event(self.viewer, accessible_desc)
            self.update_hud()
            self.preload_adjacent_images()

    def _is_path_pending(self, filepath: str) -> bool:
        can_p = _canonical_path(filepath)
        for op in self.pending_ops.values():
            if op.load_generation == self.load_generation and op.state == "pending":
                if op.src_path == can_p:
                    return True
        return False

    def _has_pending_decode(self, filepath: str) -> bool:
        """Return whether the current generation already owns a decode request."""
        canonical = _canonical_path(filepath)
        return any(
            generation == self.load_generation and _canonical_path(path) == canonical
            for path, generation in self.pending_decoder_requests.values()
        )

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
        self.show_image()

    def trigger_file_action(self, action: str, filepath: str, dest_folder: str | None = None) -> str | None:
        """
        Submits a move or trash action with transactional pending op tracking and advances UI.
        Prevents duplicate dispatches on already pending files.
        """
        if not filepath or self._is_path_pending(filepath):
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

        res_id = self.worker.add_task(
            action, filepath, dest_folder, operation_id=op_id
        )
        if res_id:
            pending_op.op_id = res_id

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
        return pending_op.op_id

    def is_input_focused(self) -> bool:
        """Determines if any input or editor widget currently has keyboard focus."""
        focus_widget = QApplication.focusWidget()
        if not focus_widget:
            return False
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox, QKeySequenceEdit)):
            return True
        return bool(hasattr(focus_widget, "isReadOnly") and not focus_widget.isReadOnly())

    def keyPressEvent(self, event: QEvent) -> None:
        """Handles keyboard navigation and sorting adhering strictly to Precedence Matrix."""
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
            self.navigate_next()
            return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self.navigate_prev()
            return

        if key == Qt.Key.Key_Delete:
            self.action_trash_current()
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
                            self.navigate_next()
                return

            # Level 3 (Fallback Letters)
            if key == Qt.Key.Key_A:
                self.navigate_prev()
                return

            if key == Qt.Key.Key_D:
                self.navigate_next()
                return

            if key == Qt.Key.Key_X:
                self.action_trash_current()
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

    def next_image_after_action(self) -> None:
        """Deprecated legacy helper retained for backward compatibility."""
        self.advance_ui_after_pending_action()

    def _match_pending_op_by_id_or_path(self, op_id: str | None, path: str | None) -> PendingOp | None:
        if op_id and op_id in self.pending_ops:
            return self.pending_ops[op_id]

        if not path:
            return None

        can_p = _canonical_path(path)
        for op in self.pending_ops.values():
            if op.state != "pending":
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

    @pyqtSlot(dict)
    def on_operation_result(self, result: dict[str, Any]) -> None:
        """
        SHARED OPERATION CONTRACT v1 signal handler:
        Settles operations exactly once by operation_id or path matching.
        """
        op_id = result.get('operation_id')
        action = result.get('action')
        source_path = result.get('source_path')
        dest_path = result.get('destination_path')
        state = result.get('state')
        undo_token = result.get('undo_token')
        error = result.get('error')

        op = self.pending_ops.get(op_id) if isinstance(op_id, str) else None

        if op and op.state == "pending":
            if state in ("completed", "completed_with_warning"):
                op.state = "finished"
                if undo_token and isinstance(undo_token, dict):
                    op.undo_token = undo_token
                    if not any(t.get('token_id') == undo_token.get('token_id') for t in self.history if 'token_id' in t):
                        self.history.append(undo_token)
                        if len(self.history) > 50:
                            self.history.pop(0)

                if action in ('move', 'trash'):
                    self.remove_image_from_queue(op.src_path)
                elif action in ('undo_move', 'undo_trash'):
                    restored_path = dest_path or op.raw_original_path or source_path
                    if restored_path:
                        self.reinsert_image_at_index(restored_path, op.original_index)

                if result.get('warning'):
                    self.statusBar().showMessage(str(result['warning']), 5000)

            elif state in ("failed", "recovery_required"):
                op.state = "error"
                if error:
                    msg = f"Error processing {os.path.basename(source_path or '')}: {error}"
                    self.statusBar().showMessage(msg, 5000)

                if action in ('move', 'trash'):
                    self.reinsert_image_at_index(op.raw_src_path, op.original_index)
                elif action in ('undo_move', 'undo_trash', 'undo_copy'):
                    if op.undo_token and not any(t.get('token_id') == op.undo_token.get('token_id') for t in self.history if 'token_id' in t):
                        self.history.append(op.undo_token)

            self.update_hud()
            self.show_image()

    def on_undo_record_received(self, data: dict[str, Any]) -> None:
        """Receives UndoToken from background worker and correlates with pending op."""
        orig_p = data.get('original') or ''
        curr_p = data.get('current') or data.get('new') or ''
        can_orig = _canonical_path(orig_p)
        can_curr = _canonical_path(curr_p)

        target_op = self._match_pending_op_by_id_or_path(None, can_orig) or self._match_pending_op_by_id_or_path(None, can_curr)

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

        target_op = self._match_pending_op_by_id_or_path(None, can_finished)

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

        self.update_hud()

    def on_worker_error(self, filepath: str, error: str) -> None:
        """Displays error messages, restores state on operation failures."""
        msg = f"Error processing {os.path.basename(filepath)}: {error}"
        self.statusBar().showMessage(msg, 5000)
        self.announce_accessibility_event(self.central_widget, msg)

        target_op = self._match_pending_op_by_id_or_path(None, filepath)

        if target_op:
            target_op.state = "error"

            if target_op.action in ('move', 'trash'):
                # Ensure the image is present in self.images and clear pending state
                self.reinsert_image_at_index(target_op.raw_src_path, target_op.original_index)

            elif target_op.action in ('undo_move', 'undo_trash', 'undo_copy'):
                # Restore failed undo token back to history stack
                if target_op.undo_token:
                    if not any(t.get('token_id') == target_op.undo_token.get('token_id') for t in self.history if 'token_id' in t):
                        self.history.append(target_op.undo_token)

        self.update_hud()
        self.show_image()

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
        """
        Reverts last move, copy, or trash operation.
        FORWARDS THE ENTIRE Undo token to worker.add_task(..., undo_token=last_action).
        """
        if not self.history:
            self.statusBar().showMessage("Nothing to undo.", 3000)
            return

        last_action = self.history.pop()
        action_type = last_action.get('action') or last_action.get('type')
        current_path = last_action.get('current') or last_action.get('new')
        original_path = last_action.get('original')

        can_orig = _canonical_path(original_path) if original_path else ""
        orig_idx = 0
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

                res_id = self.worker.add_task(
                    'undo_move',
                    current_path,
                    original_path,
                    undo_token=last_action,
                    operation_id=op_id,
                )
                if res_id:
                    pending_op.op_id = res_id

                msg = f"Restoring {os.path.basename(original_path)} to original location..."
                self.statusBar().showMessage(msg, 3000)
                self.announce_accessibility_event(self.central_widget, msg)

        elif action_type == 'copy' and current_path:
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

            res_id = self.worker.add_task(
                'undo_copy',
                current_path,
                undo_token=last_action,
                operation_id=op_id,
            )
            if res_id:
                pending_op.op_id = res_id

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
