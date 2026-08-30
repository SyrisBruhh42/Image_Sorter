import os
import queue
import threading
from typing import Optional
from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtGui import QImage
from src.logger import logger

class ImageLoader(QThread):
    """
    Background thread for preloading images to achieve zero-latency navigation.
    Delegates rendering/caching to UI layer and uses thread-safe queue.Queue with cancellation token.
    """
    image_loaded = pyqtSignal(str, QImage)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._queue: queue.Queue[Optional[tuple]] = queue.Queue()
        self.running: bool = True
        self.current_generation: int = 0
        self._lock = threading.Lock()

    def add_task(self, filepath: str) -> None:
        """Adds an image path with current generation to the preload queue in a thread-safe manner."""
        if not filepath:
            return
        with self._lock:
            gen = self.current_generation
        self._queue.put((gen, filepath))

    def clear_tasks(self) -> None:
        """Flushes in-flight preload tasks by incrementing generation counter."""
        with self._lock:
            self.current_generation += 1

    def run(self) -> None:
        """Main processing loop."""
        while self.running:
            try:
                task = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if task is None:
                break

            task_gen, filepath = task
            with self._lock:
                is_stale = (task_gen != self.current_generation)

            if not is_stale and os.path.exists(filepath):
                try:
                    img = QImage(filepath)
                    if not img.isNull():
                        with self._lock:
                            if task_gen == self.current_generation:
                                self.image_loaded.emit(filepath, img)
                except Exception as e:
                    logger.error(f"Error preloading image {filepath}: {e}")
            self._queue.task_done()

    def stop(self) -> None:
        """Stops the thread safely."""
        self.running = False
        self.clear_tasks()
        self._queue.put(None)
        self.wait()
