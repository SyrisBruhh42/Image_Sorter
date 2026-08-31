from __future__ import annotations

import os
import queue
import threading

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage

from .logger import logger


class ImageLoader(QThread):
    """
    Background thread for preloading images to achieve zero-latency navigation.
    Delegates rendering/caching to UI layer and uses thread-safe queue.Queue with cancellation token.
    """
    image_loaded = pyqtSignal(str, QImage)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self.running: bool = True
        self._lock = threading.Lock()

    def add_task(self, filepath: str) -> None:
        """Adds an image path to the preload queue in a thread-safe manner."""
        if not filepath:
            return
        self._queue.put(filepath)

    def clear_tasks(self) -> None:
        """Flushes in-flight preload tasks when directory changes."""
        with self._queue.mutex:
            self._queue.queue.clear()

    def run(self) -> None:
        """Main processing loop."""
        while self.running:
            try:
                filepath = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if filepath is None:
                break

            try:
                if os.path.exists(filepath):
                    img = QImage(filepath)
                    if not img.isNull():
                        self.image_loaded.emit(filepath, img)
            except Exception as e:
                logger.error(f"Error preloading image {filepath}: {e}")
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        """Stops the thread safely."""
        self.running = False
        self.clear_tasks()
        self._queue.put(None)
        self.wait()
