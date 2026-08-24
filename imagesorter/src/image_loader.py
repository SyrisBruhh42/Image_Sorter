import os
from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtGui import QImage

class ImageLoader(QThread):
    image_loaded = pyqtSignal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue = []
        self.running = True

    def add_task(self, filepath):
        if filepath not in self.queue:
            self.queue.append(filepath)

    def run(self):
        while self.running:
            if not self.queue:
                self.msleep(50)
                continue

            filepath = self.queue.pop(0)
            if os.path.exists(filepath):
                img = QImage(filepath)
                if not img.isNull():
                    self.image_loaded.emit(filepath, img)

    def stop(self):
        self.running = False
        self.wait()
