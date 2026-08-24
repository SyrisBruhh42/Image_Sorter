import os
from typing import List, Optional
from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtGui import QImage
from src.logger import logger

class ImageLoader(QThread):
    """
    Background thread for preloading images to achieve zero-latency navigation.
    """
    image_loaded = pyqtSignal(str, QImage)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        """
        Initializes the ImageLoader thread.
        """
        super().__init__(parent)
        self.queue: List[str] = []
        self.running: bool = True

    def add_task(self, filepath: str) -> None:
        """
        Adds an image path to the preload queue.

        Args:
            filepath (str): The absolute path to the image.
        """
        if filepath not in self.queue:
            self.queue.append(filepath)

    def run(self) -> None:
        """
        Main loop for the background thread. Processes the queue sequentially.
        """
        while self.running:
            if not self.queue:
                self.msleep(50)
                continue

            filepath = self.queue.pop(0)
            try:
                if os.path.exists(filepath):
                    img = QImage(filepath)
                    if not img.isNull():
                        self.image_loaded.emit(filepath, img)
            except Exception as e:
                logger.error(f"Error preloading image {filepath}: {e}")

    def stop(self) -> None:
        """
        Stops the background thread gracefully.
        """
        self.running = False
        self.wait()
