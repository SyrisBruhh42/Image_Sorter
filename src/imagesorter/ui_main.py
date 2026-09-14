from __future__ import annotations

import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

import psutil
from PyQt6.QtGui import (
    QAction,
    QColor,
    QImage,
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
    QCheckBox,
    QComboBox,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
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
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSlot

from .image_identity import ImageCacheKey, cache_key, matches_metadata
from .image_loader import ImageLoader
from .launch_requests import is_supported_image
from .logger import logger
from .operation_contracts import TaskOptions
from .queue_worker import QueueWorker
from .settings_manager import SettingsManager
from .ui_settings import SettingsWindow


class ImageViewer(QGraphicsView):
    """
    Custom QGraphicsView for displaying images with pan/zoom and clipping analysis.
    """
    MIN_ZOOM: float = 0.05
    MAX_ZOOM: float = 32.0

    def paintEvent(self, event):
        super().paintEvent(event)
        window = self.window()
        if not self.original_pixmap.isNull() and hasattr(window, "_record_ready"):
            window._record_ready()

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
    original_path: str | None = None
    raw_original_path: str | None = None
    state: str = "pending"  # 'pending', 'finished', 'error'
    undo_token: dict[str, Any] | None = None


@dataclass
class HeldMove:
    operation_id: str
    source: str
    generation: int
    original_index: int
    pixmap: QPixmap
    destination: str | None = None
    completed: bool = False


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
    Provides accessible labels, focus isolation, theming, and an undo stack.
    """
    def __init__(self, settings_manager: SettingsManager, initial_paths: list[str] | None = None) -> None:
        super().__init__()
        self.settings = settings_manager
        self.transient_paths: list[str] | None = None

        QApplication.instance().installEventFilter(self)

        self.worker = QueueWorker(self.settings)
        self.worker.signals.progress.connect(self.on_worker_progress)
        self.worker.signals.operation_result.connect(self.on_operation_result)
        self.worker.signals.recovery_summary.connect(self.on_recovery_summary)
        self._closing = False
        self._ready_recorded = False
        self._frame_path = None
        self._frame_index = 0
        self._frame_count = 1
        self._frame_loop_count = 0
        self._frame_total_plays = 1
        self._frame_duration_ms = 100
        self._frame_metadata = {}
        self._frame_requests = {}
        self._frame_request = None
        self._frame_pixmap = None
        self._frame_playing = False
        self._frame_repeats = 0
        self._frame_batches = {}
        self._frame_buffering = False
        self._frame_tick_started = None
        self._recovery_records = []
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.timeout.connect(self._advance_frame)

        self.images: list[str] = []
        self.current_index: int = -1
        self.history: list[dict[str, Any]] = []
        self._settled_operations: set[str] = set()
        self._latest_undo_tokens: dict[str, dict[str, Any]] = {}
        self._consumed_undo_tokens: set[str] = set()
        self._undo_inflight: set[str] = set()
        self._held_move: HeldMove | None = None
        self._view_complete = False
        self._recovery_generations: dict[str, int] = {}
        self.zen_mode: bool = False

        self.load_generation: int = 0
        self.pending_ops: dict[str, PendingOp] = {}
        self.pending_decoder_requests: dict[str, tuple[str, int]] = {}  # req_id -> (filepath, load_generation)

        self.pixmap_cache: OrderedDict[ImageCacheKey, QPixmap] = OrderedDict()
        self._decode_contexts = {}
        self._uncached_key = self._uncached_pixmap = None
        self._frame_base_key = None
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
        self._frame_metadata.clear()
        self._decode_contexts.clear()
        for request_id in self._frame_batches:
            self.loader.cancel_request(request_id)
        self._frame_batches.clear()
        self._uncached_key = self._uncached_pixmap = None
        self._frame_base_key = None
        self._set_frame_playing(False)
        if self._frame_request:
            self.loader.cancel_request(self._frame_request)
        self._frame_path, self._frame_request, self._frame_pixmap = None, None, None
        self._frame_index = 0

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

    def _cache_key(self, filepath, *, frame=0, target_size=None):
        try:
            return cache_key(filepath, frame=frame, target_size=target_size)
        except (OSError, ValueError, RuntimeError):
            return None

    def _add_pixmap_to_cache(self, filepath: str, pixmap: QPixmap, *, key=None) -> bool:
        key = key or self._cache_key(filepath)
        if key is None:
            return False
        if key in self.pixmap_cache:
            old_pixmap = self.pixmap_cache.pop(key)
            self.cache_bytes -= (old_pixmap.width() * old_pixmap.height() * 4)

        pixmap_size = pixmap.width() * pixmap.height() * 4
        if pixmap_size > self.max_cache_bytes or self.max_cache_items < 1:
            return False  # Display may retain its current frame; cache never exceeds its budget.
        self.pixmap_cache[key] = pixmap
        self.cache_bytes += pixmap_size

        while self.pixmap_cache and (self.cache_bytes > self.max_cache_bytes or len(self.pixmap_cache) > self.max_cache_items):
            _old_k, old_pm = self.pixmap_cache.popitem(last=False)
            self._frame_metadata.pop(_old_k, None)
            self.cache_bytes -= (old_pm.width() * old_pm.height() * 4)
        return True

    def _get_pixmap_from_cache(self, filepath: str) -> QPixmap | None:
        key = self._cache_key(filepath)
        if key is None:
            return None
        for stale in [item for item in self.pixmap_cache if item.path == key.path and
                      (item.source != key.source or item.decoder != key.decoder)]:
            old = self.pixmap_cache.pop(stale)
            self.cache_bytes -= old.width() * old.height() * 4
            self._frame_metadata.pop(stale, None)
        if key in self.pixmap_cache:
            self.pixmap_cache.move_to_end(key)
            return self.pixmap_cache[key]
        if key == self._uncached_key:
            return self._uncached_pixmap
        return None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            # Settings owns its unsaved checkbox and Alt override; an application
            # filter must not apply the older persisted setting ahead of it.
            if isinstance(obj, QWidget) and isinstance(obj.window(), SettingsWindow):
                return False
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
        error = result.get('error')

        if gen != self.load_generation or self._held_move is not None:
            return  # Discard stale decoder results

        metadata = result.get("metadata", {})
        if req_id in self._frame_batches:
            expected = self._frame_batches.pop(req_id)
            if (filepath != self._frame_path or expected != self._frame_base_key or
                    expected != self._cache_key(filepath)):
                return
            if error:
                self._set_frame_playing(False)
                self.statusBar().showMessage("Playback buffer failed: " + str(error), 7000)
                return
            for item in metadata.get("frames", []):
                key = replace(expected, frame=item["frame"])
                image = item.get("image")
                if image is None or image.isNull() or not matches_metadata(key, item):
                    self._set_frame_playing(False)
                    self.statusBar().showMessage("Playback discarded a changed frame identity", 7000)
                    return
                if not self._add_pixmap_to_cache(filepath, QPixmap.fromImage(image), key=key):
                    self._set_frame_playing(False)
                    self.statusBar().showMessage("A frame exceeds the cache budget; playback paused. Step and seek remain available.", 7000)
                    return
                self._frame_metadata[key] = {name: value for name, value in item.items() if name != "image"}
            self._schedule_frame_playback()
            return
        context = self._decode_contexts.pop(req_id, None)
        if not error and (context is None or context != self._cache_key(filepath, frame=context.frame, target_size=context.preview)
                          or not matches_metadata(context, metadata)):
            self.pending_decoder_requests.pop(req_id, None)
            self._frame_requests.pop(req_id, None)
            if req_id == self._frame_request:
                self._frame_request = None
                self._set_frame_playing(False)
            if filepath and 0 <= self.current_index < len(self.images) and self.images[self.current_index] == filepath:
                QTimer.singleShot(0, self.show_image)
            return  # A late result may never relabel changed bytes or decoder versions.
        if req_id in self._frame_requests:
            path, frame = self._frame_requests.pop(req_id)
            if req_id != self._frame_request or path != self._frame_path:
                return
            self._frame_request = None
            if error or qimg is None or qimg.isNull():
                self._set_frame_playing(False)
                self.statusBar().showMessage(str(error or "Frame decode failed"), 7000)
                return
            pixmap = QPixmap.fromImage(qimg)
            if self._add_pixmap_to_cache(path, pixmap, key=context):
                self._frame_metadata[context] = metadata
            self._present_frame(frame, pixmap, metadata=metadata)
            self._schedule_frame_playback()
            return

        if req_id in self.pending_decoder_requests:
            _req_fp, req_gen = self.pending_decoder_requests.pop(req_id)
            if req_gen != self.load_generation:
                return

        if qimg is not None and isinstance(qimg, QImage) and not qimg.isNull() and filepath:
            pixmap = QPixmap.fromImage(qimg)
            if not pixmap.isNull():
                admitted = self._add_pixmap_to_cache(filepath, pixmap, key=context)
                if admitted:
                    self._frame_metadata[context] = metadata
                if (0 <= self.current_index < len(self.images) and
                        _canonical_path(self.images[self.current_index]) == _canonical_path(filepath)):
                    self._frame_metadata[context] = metadata
                    self._uncached_key, self._uncached_pixmap = (None, None) if admitted else (context, pixmap)
                    self.show_image()
        elif error and filepath and (
            0 <= self.current_index < len(self.images)
            and _canonical_path(self.images[self.current_index]) == _canonical_path(filepath)
        ):
            self.viewer.hide()
            self.hud_widget.hide()
            self.empty_label.setText(str(error))
            self.empty_label.show()
            self.statusBar().showMessage(str(error), 7000)

    def preload_adjacent_images(self) -> None:
        """Preloads adjacent images using extended or legacy ImageLoader contract."""
        for offset, prio in [(1, 1), (-1, 2), (2, 3)]:
            idx = self.current_index + offset
            if 0 <= idx < len(self.images):
                fp = self.images[idx]
                if (self._get_pixmap_from_cache(fp) is None and not self._is_path_pending(fp)
                        and not self._has_pending_decode(fp)):
                    req_id = self.loader.add_task(
                        fp, generation=self.load_generation, priority=prio
                    )
                    if req_id:
                        self._decode_contexts[req_id] = self._cache_key(fp)
                        self.pending_decoder_requests[req_id] = (
                            fp, self.load_generation
                        )

    def init_ui(self) -> None:
        """Initializes the main UI with accessible widget names and descriptions."""
        self.setWindowTitle("Image Sorter")

        if self.settings.get('ui', 'fullscreen'):
            self.showFullScreen()
        else:
            self.showMaximized()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # Compact image/status HUD overlay
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

        hud_layout.addWidget(self.hud_filename)
        hud_layout.addWidget(self.hud_details)
        hud_layout.addWidget(self.hud_status)
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

        self.frame_bar = QWidget(self)
        frame_layout = QHBoxLayout(self.frame_bar)
        self.frame_previous = QPushButton("Previous frame/page")
        self.frame_previous.setAccessibleName("Previous Frame Button")
        self.frame_previous.setAccessibleDescription("Navigates to the previous frame or page of multi-frame images.")
        self.frame_play = QPushButton("Play")
        self.frame_play.setAccessibleName("Play Pause Animation Button")
        self.frame_play.setAccessibleDescription("Toggles playback of animated or multi-frame image formats.")
        self.frame_next = QPushButton("Next frame/page")
        self.frame_next.setAccessibleName("Next Frame Button")
        self.frame_next.setAccessibleDescription("Navigates to the next frame or page of multi-frame images.")
        self.frame_seek = QSpinBox()
        self.frame_seek.setPrefix("Frame/page ")
        self.frame_seek.setAccessibleName("Frame or page number")
        self.frame_loop = QCheckBox("Loop continuously")
        self.frame_loop.setAccessibleName("Loop Continuously Checkbox")
        self.frame_loop.setAccessibleDescription("Toggles continuous looping playback for multi-frame images.")
        self.frame_previous.clicked.connect(lambda: self._seek_frame(self._frame_index - 1))
        self.frame_next.clicked.connect(lambda: self._seek_frame(self._frame_index + 1))
        self.frame_play.clicked.connect(lambda: self._set_frame_playing(not self._frame_playing))
        self.frame_seek.valueChanged.connect(lambda value: self._seek_frame(value - 1))
        for widget in (self.frame_previous, self.frame_play, self.frame_next, self.frame_seek, self.frame_loop):
            frame_layout.addWidget(widget)
        self.layout.addWidget(self.frame_bar)
        self.frame_bar.hide()

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
        """Applies the selected light, dark, or high-contrast palette."""
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
        undo_action.setToolTip("Revert last file action. (Shortcut: Ctrl+Z)")
        undo_action.triggered.connect(self.undo_last_action)
        file_menu.addAction(undo_action)

        recovery_action = QAction("Review preserved recovery records", self)
        recovery_action.triggered.connect(self.show_recovery_records)
        file_menu.addAction(recovery_action)

        inference_action = QAction("Last optional inference receipt", self)
        inference_action.triggered.connect(self.show_inference_receipt)
        file_menu.addAction(inference_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setToolTip("Leave Zen/fullscreen, then close the application. (Shortcut: Esc)")
        exit_action.triggered.connect(self.handle_escape)
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
        self._navigate(1)

    def navigate_prev(self) -> None:
        self._navigate(-1)

    def _navigate(self, direction: int) -> None:
        held = self._held_move
        if held is not None:
            # The held image occupies the gap between its original neighbours.
            source_index = next((i for i, path in enumerate(self.images)
                                 if _canonical_path(path) == held.source), None)
            start = (source_index + direction if source_index is not None else
                     held.original_index if direction > 0 else held.original_index - 1)
            self._held_move = None
        else:
            start = self.current_index + direction
        index = self._find_next_non_pending_index(start, direction)
        if index != -1:
            self._view_complete = False
            self.current_index = index
            self.show_image()
        elif direction > 0:
            self._view_complete = True
            self.current_index = len(self.images)
            self.show_image()
        elif held is not None:
            # Previous at the beginning keeps the item; Next at the end completes.
            self._held_move = held
            self._show_held_move()

    def handle_escape(self) -> None:
        if self.zen_mode:
            self.toggle_zen_mode()
        elif self.isFullScreen():
            self.showMaximized()
            self.settings.set('ui', 'fullscreen', False)
        else:
            self.close()

    def _belongs_to_current_view(self, filepath: str) -> bool:
        canonical = _canonical_path(filepath)
        if self.transient_paths is not None:
            return any(_canonical_path(path) == canonical for path in self.transient_paths)
        source = self.settings.get('directories', 'source')
        return bool(source and _canonical_path(os.path.dirname(filepath)) == _canonical_path(source))

    def _show_held_move(self) -> None:
        held = self._held_move
        if held is None:
            return
        self.frame_bar.hide()
        self.empty_label.hide()
        self.viewer.show()
        if self.viewer.original_pixmap.cacheKey() != held.pixmap.cacheKey():
            self.viewer.set_image(held.pixmap)
        state = 'Moved' if held.completed else 'Moving'
        label = f"{state}: {held.destination or held.source} — read-only snapshot"
        self.setWindowTitle(f"Image Sorter - {label}")
        self.viewer.setAccessibleName(label)
        self.hud_filename.setText(os.path.basename(held.destination or held.source))
        self.hud_details.setText(label)
        self.hud_status.setText('Use Next/Previous to continue, or Undo to restore.')
        self.hud_status.setStyleSheet('font-size: 12px; color: #ffaa00;')
        self.hud_widget.setVisible(not self.zen_mode)
        self.position_hud()

    def action_trash_current(self) -> None:
        if self._held_move is None and 0 <= self.current_index < len(self.images):
            self.trigger_file_action('trash', self.images[self.current_index])

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
        self._held_move = None
        self._view_complete = False
        self.load_generation += 1
        self.pending_decoder_requests.clear()
        if hasattr(self, 'loader'):
            self.loader.clear_tasks(new_generation=self.load_generation)
        self.clear_pixmap_cache()
        self.viewer.set_image(QPixmap())
        self.frame_bar.hide()

        if self.transient_paths is not None:
            self.images = [
                p for p in self.transient_paths if os.path.isfile(p) and is_supported_image(p)
            ]
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
                    if is_supported_image(filepath):
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
        """Display current image dimensions and operation status."""
        if self._held_move is not None:
            self._show_held_move()
            return
        if self.zen_mode or self._view_complete or not self.images or self.current_index < 0 or self.current_index >= len(self.images):
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
        ai_str = ""
        if ai_enabled and self.settings.get('ui', 'show_tags'):
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
        if self._held_move is not None:
            self._show_held_move()
            return
        if self._view_complete:
            self._set_frame_playing(False)
            self.viewer.hide()
            self.hud_widget.hide()
            self.frame_bar.hide()
            self.empty_label.show()
            pending = sum(op.state == 'pending' for op in self.pending_ops.values())
            self.empty_label.setText(f'Review complete; {pending} background operations pending.' if pending else 'All done!')
            self.setWindowTitle('Image Sorter - Review complete')
            return
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
            self.setWindowTitle("Image Sorter")
            self.announce_accessibility_event(self.empty_label, msg)
            return

        self.current_index = non_pending_idx
        filepath = self.images[self.current_index]
        if self._uncached_key is not None and self._uncached_key.path != filepath:
            self._frame_metadata.pop(self._uncached_key, None)
            self._uncached_key = self._uncached_pixmap = None

        pixmap = self._get_pixmap_from_cache(filepath)
        base_key = self._cache_key(filepath)
        if filepath != self._frame_path or base_key != self._frame_base_key:
            self._set_frame_playing(False)
            for request_id in self._frame_batches:
                self.loader.cancel_request(request_id)
            self._frame_batches.clear()
            if self._frame_request:
                self.loader.cancel_request(self._frame_request)
            self._frame_request = None
            self._frame_path, self._frame_index = filepath, 0
            self._frame_base_key = base_key
            self._frame_pixmap = None
            self._frame_repeats = 0
            self._frame_count, self._frame_loop_count, self._frame_duration_ms = 1, 0, 100
            self._frame_total_plays = 1
        frame_metadata = self._frame_metadata.get(base_key)
        if frame_metadata is not None:
            self._frame_count = max(1, min(100000, int(frame_metadata.get("frame_count", 1))))
            self._frame_loop_count = frame_metadata.get("loop_count", 0)
            # New helpers normalize format-specific loop fields into total
            # plays. Preserve compatibility with older, non-qualified replies.
            raw_loop = frame_metadata.get("loop_count")
            fallback_plays = 1 if raw_loop is None else 0 if raw_loop == 0 else raw_loop + (1 if filepath.lower().endswith(".gif") else 0)
            self._frame_total_plays = frame_metadata.get("total_plays", fallback_plays)
            if self._frame_index == 0:
                self._frame_duration_ms = max(1, int(frame_metadata.get("duration_ms", 100) or 100))
        self.frame_bar.setVisible(self._frame_count > 1)
        self.frame_seek.blockSignals(True)
        self.frame_seek.setRange(1, self._frame_count)
        self.frame_seek.setValue(self._frame_index + 1)
        self.frame_seek.blockSignals(False)
        if self._frame_pixmap is not None:
            pixmap = self._frame_pixmap
        if pixmap is None:
            # Do not leave the previous image visible under a new current-file
            # identity while a cancellable decode is still pending.
            self.viewer.hide()
            self.hud_widget.hide()
            self.empty_label.setText("Loading image…")
            self.empty_label.show()
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
                    self._decode_contexts[req_id] = self._cache_key(filepath)
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
            if op.load_generation == self.load_generation and op.state == "pending" and op.action != "copy":
                if op.src_path == can_p:
                    return True
        return False

    def _set_frame_playing(self, playing):
        self._frame_playing = bool(playing and self._frame_count > 1 and not self._closing)
        self._frame_buffering = False
        self._frame_timer.stop()
        if hasattr(self, "frame_play"):
            self.frame_play.setText("Pause" if self._frame_playing else "Play")
        if self._frame_playing and not self._frame_request:
            self._schedule_frame_playback()

    def _present_frame(self, frame, pixmap, *, metadata=None):
        self._frame_index, self._frame_pixmap = frame, pixmap
        if metadata is None:
            metadata = self._frame_metadata.get(replace(self._frame_base_key, frame=frame), {})
        self._frame_duration_ms = max(1, int(metadata.get("duration_ms", 100) or 100))
        transform = self.viewer.transform()
        center = self.viewer.mapToScene(self.viewer.viewport().rect().center())
        self.viewer.set_image(pixmap)
        self.viewer.setTransform(transform)
        self.viewer.centerOn(center)
        self.frame_seek.blockSignals(True)
        self.frame_seek.setValue(frame + 1)
        self.frame_seek.blockSignals(False)
        from .diagnostics import record
        record("frame_selected", filepath=self._frame_path, frame=frame,
               playback=self._frame_playing, buffering=self._frame_buffering)

    def _cached_frame(self, frame):
        if self._frame_base_key is None:
            return None
        key = replace(self._frame_base_key, frame=frame)
        pixmap = self.pixmap_cache.get(key)
        if pixmap is not None:
            self.pixmap_cache.move_to_end(key)
        return pixmap

    def _prefetch_frames(self):
        if self._frame_batches or not self._frame_base_key or self._frame_count <= 1:
            return
        pixmap = self._frame_pixmap or self.viewer.original_pixmap
        frame_bytes = max(1, pixmap.width() * pixmap.height() * 4)
        capacity = min(16, self.max_cache_items - 1, self.max_cache_bytes // frame_bytes - 1, self._frame_count - 1)
        batch_limit = min(8, (64 * 1024 * 1024) // frame_bytes, capacity)
        if batch_limit < 1:
            self._set_frame_playing(False)
            self.statusBar().showMessage("Playback needs more cache space for this image; step and seek remain available.", 7000)
            return
        ahead = [(self._frame_index + offset) % self._frame_count for offset in range(1, capacity + 1)]
        missing = [frame for frame in ahead if self._cached_frame(frame) is None]
        if not missing or len(ahead) - len(missing) > capacity // 2:
            return
        request_id = self.loader.add_task(self._frame_path, generation=self.load_generation, priority=0,
                                          frames=tuple(missing[:batch_limit]))
        if request_id:
            self._frame_batches[request_id] = self._frame_base_key

    def _schedule_frame_playback(self):
        if not self._frame_playing or self._closing or self._frame_request:
            return
        if self._frame_base_key != self._cache_key(self._frame_path):
            self._set_frame_playing(False)
            self.show_image()
            return
        self._prefetch_frames()
        if not self._frame_playing:
            return
        next_frame = (self._frame_index + 1) % self._frame_count
        if self._cached_frame(next_frame) is None:
            self._frame_buffering = True
            self._frame_timer.stop()
            self.frame_play.setText("Pause — buffering")
            self.statusBar().showMessage("Buffering frames; playback timing starts when the next frame is ready.")
            return
        if self._frame_timer.isActive():
            return  # A background batch must not restart the current frame's clock.
        self._frame_buffering = False
        self.frame_play.setText("Pause")
        self._frame_tick_started = time.monotonic()
        self._frame_timer.start(self._frame_duration_ms)

    def _seek_frame(self, frame, *, playback=False):
        if not playback:
            self._set_frame_playing(False)
            self._frame_repeats = 0
        if self._closing or not self._frame_path or not 0 <= frame < self._frame_count:
            return
        if self._frame_request:
            self.loader.cancel_request(self._frame_request)
            self._frame_request = None
        if self._frame_base_key != self._cache_key(self._frame_path):
            self._set_frame_playing(False)
            self.show_image()
            return
        pixmap = self._cached_frame(frame)
        if pixmap is not None:
            self._present_frame(frame, pixmap)
            self._schedule_frame_playback()
            return
        if playback:
            self._schedule_frame_playback()
            return
        request_id = self.loader.add_task(self._frame_path, generation=self.load_generation,
                                          priority=0, frame=frame)
        if request_id:
            self._decode_contexts[request_id] = self._cache_key(self._frame_path, frame=frame)
            self._frame_requests[request_id] = (self._frame_path, frame)
            self._frame_request = request_id

    def _advance_frame(self):
        if not self._frame_playing:
            return
        frame = self._frame_index + 1
        if frame >= self._frame_count:
            total_plays = self._frame_total_plays
            if not self.frame_loop.isChecked() and total_plays > 0 and self._frame_repeats >= total_plays - 1:
                self._set_frame_playing(False)
                return
            frame = 0
            self._frame_repeats += 1
        self._seek_frame(frame, playback=True)

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

    def trigger_file_action(self, action: str, filepath: str, dest_folder: str | None = None,
                            *, auto_advance: bool = True) -> str | None:
        """Admit one generation-bound operation before changing the current view."""
        if self._held_move is not None or not filepath or any(
                op.state == 'pending' and op.src_path == _canonical_path(filepath)
                for op in self.pending_ops.values()):
            return None
        canonical = _canonical_path(filepath)
        index = next((i for i, path in enumerate(self.images) if _canonical_path(path) == canonical), -1)
        if index < 0:
            return None
        held_pixmap = None
        if action == 'move' and not auto_advance:
            if index != self.current_index or not self.viewer.isVisible() or self.viewer.original_pixmap.isNull():
                self.statusBar().showMessage('Wait for this image to finish loading before moving without advancing.', 5000)
                return None
            pixmap = self.viewer.original_pixmap
            # One independent snapshot, bounded to 4 million pixels / 16 MiB.
            ratio = min(1.0, (4_000_000 / max(1, pixmap.width() * pixmap.height())) ** 0.5)
            held_pixmap = pixmap.scaled(max(1, int(pixmap.width() * ratio)),
                                       max(1, int(pixmap.height() * ratio)),
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation).copy()
        op_id = str(uuid.uuid4())
        op = PendingOp(op_id, action, canonical, filepath, index, self.load_generation,
                       dest_folder=_canonical_path(dest_folder) if dest_folder else None)
        self.pending_ops[op_id] = op
        try:
            admitted = self.worker.add_task(action, filepath, dest_folder, operation_id=op_id,
                                           task_options=TaskOptions(view_generation=self.load_generation))
        except (RuntimeError, OverflowError, OSError, ValueError, TypeError) as exc:
            self.pending_ops.pop(op_id, None)
            self.statusBar().showMessage(f'Operation was not queued: {exc}', 5000)
            return None
        if not admitted:
            self.pending_ops.pop(op_id, None)
            return None
        if admitted != op_id:
            self.pending_ops.pop(op_id)
            op.op_id = admitted
            self.pending_ops[admitted] = op
        if held_pixmap is not None:
            # Cancel frame/decode work so a late callback cannot replace the snapshot.
            self.pending_decoder_requests.clear()
            self.loader.clear_tasks(new_generation=self.load_generation)
            self.clear_pixmap_cache()
            self._held_move = HeldMove(admitted, canonical, self.load_generation, index, held_pixmap)
        if action == 'trash' and not self.settings.get('directories', 'trash'):
            self.statusBar().showMessage('Queued for system trash. Image Sorter Undo will be unavailable if it succeeds.', 4000)
        else:
            self.announce_accessibility_event(self.central_widget, f'Queued {action} for {os.path.basename(filepath)}.')
        if action in ('move', 'trash'):
            self.show_image()
        elif auto_advance:
            self.navigate_next()
        return admitted

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
                if self._held_move is not None or 0 <= self.current_index < len(self.images):
                    filepath = ((self._held_move.destination or self._held_move.source) if self._held_move else self.images[self.current_index])
                    QApplication.clipboard().setText(filepath)
                    msg = f"Copied filepath to clipboard: {os.path.basename(filepath)}"
                    self.statusBar().showMessage(msg, 3000)
                    self.announce_accessibility_event(self.central_widget, msg)
                return
            elif key == Qt.Key.Key_Q:
                self.close()
                return

        if key == Qt.Key.Key_Escape:
            self.handle_escape()
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

                    self.trigger_file_action(action, filepath, folder,
                                             auto_advance=config.get('auto_advance', True))
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

    @pyqtSlot(dict)
    def on_operation_result(self, result: dict[str, Any]) -> None:
        """Settle durable results once; only the owning generation may change its view."""
        op_id = result.get('operation_id')
        action = result.get('action')
        state = result.get('state')
        if not isinstance(op_id, str) or state not in ('completed', 'completed_with_warning', 'failed', 'recovery_required', 'cancelled'):
            return
        if op_id in self._settled_operations:
            return
        op = self.pending_ops.get(op_id)
        if op is not None:
            if action not in (op.action, 'undo_move' if op.action == 'undo_trash' else op.action):
                return
            generation = result.get('view_generation')
            if generation is not None and generation != op.load_generation:
                return
        elif action not in ('metadata', 'recover'):
            return
        self._settled_operations.add(op_id)
        token = result.get('undo_token')
        success = state in ('completed', 'completed_with_warning')
        detail = result.get('error') or result.get('warning')
        if action == 'recover':
            if resolved := result.get('resolved_operation_id'):
                self._recovery_records = [row for row in self._recovery_records if row['operation_id'] != resolved]
            if self._recovery_generations.pop(op_id, None) == self.load_generation:
                self.statusBar().showMessage('Recovery completed.' if success else 'Recovery not completed: ' + str(detail or state), 7000)
            return
        if action == 'metadata':
            if success and token:
                self._remember_undo(token, add_to_history=False)
            if detail and result.get('view_generation') == self.load_generation:
                self.statusBar().showMessage('Image operation completed; optional metadata failed: ' + str(detail), 7000)
            return
        current_view = op.load_generation == self.load_generation
        op.state = 'finished' if success else 'error'
        if action.startswith('undo_'):
            undo_id = (op.undo_token or {}).get('token_id')
            self._undo_inflight.discard(undo_id)
            if success:
                self._consumed_undo_tokens.add(undo_id)
                self.history = [item for item in self.history if item.get('token_id') != undo_id]
            elif op.undo_token:
                self._remember_undo(op.undo_token)
        elif success and token:
            op.undo_token = self._remember_undo(token)
        if not current_view:
            return
        held = self._held_move
        owns_hold = held is not None and held.operation_id == op_id
        if success:
            if action in ('move', 'trash'):
                if owns_hold:
                    held.completed = True
                    held.destination = result.get('destination_path')
                self.remove_image_from_queue(op.src_path)
            elif action in ('undo_move', 'undo_trash'):
                restored = result.get('destination_path') or op.raw_original_path
                if restored and self._belongs_to_current_view(restored):
                    self._held_move = None
                    self._view_complete = False
                    self.reinsert_image_at_index(restored, op.original_index)
                    self.current_index = next(i for i, path in enumerate(self.images) if _canonical_path(path) == _canonical_path(restored))
        elif action in ('move', 'trash') and self._belongs_to_current_view(op.raw_src_path):
            self._view_complete = False
            if owns_hold:
                self._held_move = None
            self.reinsert_image_at_index(op.raw_src_path, op.original_index)
            self.current_index = next(i for i, path in enumerate(self.images) if _canonical_path(path) == op.src_path)
        self.show_image()
        self.update_hud()
        if detail:
            self.statusBar().showMessage(str(detail), 7000)

    def _remember_undo(self, token: dict[str, Any], *, add_to_history: bool = True) -> dict[str, Any]:
        """Keep revisions monotonic even when metadata and primary replies reorder."""
        token_id = token.get('token_id')
        if not isinstance(token_id, str):
            return token
        previous = self._latest_undo_tokens.get(token_id)
        if previous is None or int(token.get('revision', 1)) > int(previous.get('revision', 1)):
            self._latest_undo_tokens[token_id] = dict(token)
        newest = self._latest_undo_tokens[token_id]
        if token_id in self._consumed_undo_tokens or token_id in self._undo_inflight:
            return newest
        for index, item in enumerate(self.history):
            if item.get('token_id') == token_id:
                self.history[index] = newest
                break
        else:
            if add_to_history:
                self.history.append(newest)
                self.history = self.history[-50:]
        return newest

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
            if self._held_move is not None and found_idx < self._held_move.original_index:
                self._held_move.original_index -= 1
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
        if self._held_move is not None and clamped_idx <= self._held_move.original_index:
            self._held_move.original_index += 1

        if self.current_index >= clamped_idx:
            self.current_index += 1
        self.show_image()

    def undo_last_action(self) -> None:
        """Retain the newest complete Undo token until admission succeeds."""
        if self._held_move is not None and not self._held_move.completed:
            self.statusBar().showMessage('Wait for this move to finish before using Undo.', 4000)
            return
        if not self.history:
            self.statusBar().showMessage('Nothing to undo.', 3000)
            return
        token = self.history[-1]
        token_id = token.get('token_id')
        action = token.get('action') or token.get('type')
        current = token.get('current') or token.get('new')
        original = token.get('original')
        if action not in ('move', 'trash', 'copy') or not current or not original:
            self.statusBar().showMessage('Undo record is incomplete; no files changed.', 5000)
            return
        original_index = next((op.original_index for op in self.pending_ops.values()
                               if op.src_path == _canonical_path(original)), 0)
        op_id = str(uuid.uuid4())
        undo_action = 'undo_copy' if action == 'copy' else 'undo_move'
        op = PendingOp(op_id, undo_action, _canonical_path(current), current,
                       original_index, self.load_generation,
                       original_path=_canonical_path(original), raw_original_path=original,
                       undo_token=token)
        self.pending_ops[op_id] = op
        try:
            admitted = self.worker.add_task(undo_action, current,
                                           original if action != 'copy' else None,
                                           undo_token=token, operation_id=op_id,
                                           task_options=TaskOptions(view_generation=self.load_generation))
        except (RuntimeError, OverflowError, OSError, ValueError, TypeError) as exc:
            self.pending_ops.pop(op_id, None)
            self.statusBar().showMessage(f'Undo was not queued: {exc}', 5000)
            return
        if not admitted:
            self.pending_ops.pop(op_id, None)
            return
        if admitted != op_id:
            self.pending_ops.pop(op_id)
            op.op_id = admitted
            self.pending_ops[admitted] = op
        self.history = [item for item in self.history if item.get('token_id') != token_id]
        self._undo_inflight.add(token_id)
        self.statusBar().showMessage(f'Queued Undo for {os.path.basename(original)}.', 3000)

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
        """Quiesce readers asynchronously; durable writer drains independently."""
        if not self._closing:
            if scenario := getattr(self, "_native_scenario_runner", None):
                scenario.abort()
            self._closing = True
            self._set_frame_playing(False)
            self._close_started = time.monotonic()
            self.worker.shutdown()
            self.loader.request_stop()
            for dialog in self.findChildren(SettingsWindow):
                dialog.close()
            self.statusBar().showMessage("Closing; accepted file operations finish safely in the background.")
        settings_running = any(dialog.isVisible() or
                               bool(dialog.components_panel.process) or
                               bool(dialog.check_worker and dialog.check_worker.isRunning()) or
                               bool(dialog.downloader and dialog.downloader.isRunning())
                               for dialog in self.findChildren(SettingsWindow))
        if self.loader.isRunning() or self.worker.readers_running() or settings_running:
            event.ignore()
            QTimer.singleShot(50, self.close)
            return
        from .diagnostics import record
        record("gui_shutdown", elapsed_ms=round((time.monotonic() - self._close_started) * 1000),
               mutation_pending_ids=list(self.worker.client.pending))
        super().closeEvent(event)

    def _record_ready(self):
        if self._ready_recorded or not self.images or self.current_index < 0:
            return
        self._ready_recorded = True
        import json

        from . import __file__ as module_origin
        from .diagnostics import record
        from .paths import get_resource_dir
        identity_path = get_resource_dir() / "build_identity.json"
        try:
            build_identity = json.loads(identity_path.read_text())
        except (OSError, ValueError):
            build_identity = None
        record("image_presented", backend=QApplication.platformName(), module_origin=module_origin,
               filepath=self.images[self.current_index], generation=self.load_generation,
               width=self.viewer.original_pixmap.width(), height=self.viewer.original_pixmap.height(),
               build_identity=build_identity)
        if getattr(self, "_acceptance_exit_after_ready", False):
            QTimer.singleShot(150, self.close)

    def on_recovery_summary(self, summary):
        if summary.get("type") == "recovery_reset":
            self._recovery_records.clear()
            return
        if summary.get("type") == "history":
            result = summary.get("result", {})
            token = result.get("undo_token")
            if token:
                self._remember_undo(token)
            return
        records = summary.get("recovery", [])
        if records:
            by_id = {row["operation_id"]: row for row in self._recovery_records}
            by_id.update({row["operation_id"]: row for row in records})
            self._recovery_records = list(by_id.values())
            self.statusBar().showMessage(f"{len(self._recovery_records)} unresolved operations. Review preserved files before sorting.")

    def show_recovery_records(self):
        from .ui_recovery import RecoveryDialog
        dialog = RecoveryDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def request_recovery(self, record):
        from .ui_recovery import eligible
        if not eligible(record) or self.worker.client.pending:
            self.statusBar().showMessage("Rollback unavailable for this record or while accepted work is pending.", 5000)
            return None
        answer = QMessageBox.question(self, "Confirm recorded rollback",
            "Request rollback of this recorded interrupted transaction?\n\n" + str(record["source_path"]) +
            "\n\nThe service will verify recorded file identities. Changed or ambiguous files will be preserved and reported, not guessed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return None
        try:
            operation_id = self.worker.add_recovery_task(record, task_options=TaskOptions(view_generation=self.load_generation))
        except (RuntimeError, OverflowError, OSError, ValueError, TypeError) as exc:
            self.statusBar().showMessage(f'Recovery was not queued: {exc}', 7000)
            return None
        if operation_id:
            self._recovery_generations[operation_id] = self.load_generation
        return operation_id

    def show_inference_receipt(self):
        import json
        receipt = getattr(self.worker, "last_inference_receipt", None)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Last optional inference")
        dialog.setText("No optional inference has completed in this session." if not receipt else
                       "Provider: " + str(receipt.get("provider")) + "\nFallback: " + str(receipt.get("fallback_reason") or "None"))
        if receipt:
            dialog.setDetailedText(json.dumps(receipt, indent=2))
        dialog.exec()
