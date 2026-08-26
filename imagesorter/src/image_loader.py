import os
import queue
from typing import Optional
from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtGui import QImage
from src.logger import logger

class ImageLoader(QThread):
    """
    Background thread for preloading images to achieve zero-latency navigation.
    Uses a thread-safe Queue and cancellation tokens.
    """
    image_loaded = pyqtSignal(str, QImage)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        """
        Initializes the ImageLoader thread.
        """
        super().__init__(parent)
        self.queue: queue.Queue = queue.Queue()
        self.running: bool = True
        self._cancel_token: int = 0

    def add_task(self, filepath: str) -> None:
        """
        Adds an image path to the preload queue.

        Args:
            filepath (str): The absolute path to the image.
        """
        # Pack the task with the current cancellation token
        self.queue.put((filepath, self._cancel_token))

    def flush(self) -> None:
        """
        Clears the queue and increments the cancellation token, discarding in-flight tasks.
        """
        self._cancel_token += 1
        # Clear the queue without blocking
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break
        logger.debug(f"ImageLoader flushed, new cancel token: {self._cancel_token}")

    def run(self) -> None:
        """
        Main loop for the background thread. Processes the queue sequentially.
        """
        while self.running:
            try:
                # Block for 50ms to allow smooth stopping
                task = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue

            filepath, token = task

            # Check if this task is stale (was queued before a flush)
            if token != self._cancel_token:
                self.queue.task_done()
                continue

            try:
                if os.path.exists(filepath):
                    img = QImage(filepath)
                    # Double check token after potentially slow IO
                    if token == self._cancel_token and not img.isNull():
                        self.image_loaded.emit(filepath, img)
            except Exception as e:
                logger.error(f"Error preloading image {filepath}: {e}")
            finally:
                self.queue.task_done()

    def stop(self) -> None:
        """
        Stops the background thread gracefully.
        """
        self.running = False
        self.wait()
