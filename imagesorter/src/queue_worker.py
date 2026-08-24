import os
import shutil
import time
from PyQt6.QtCore import QThread, pyqtSignal, QObject
from send2trash import send2trash
from src.ai_tagger import AITagger, write_metadata

class WorkerSignals(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str, str)
    progress = pyqtSignal(str)

class QueueWorker(QThread):
    def __init__(self, settings_manager):
        super().__init__()
        self.settings = settings_manager
        self.queue = []
        self.signals = WorkerSignals()
        self.running = True
        self.ai_tagger = None
        self._init_ai()

    def _init_ai(self):
        if self.settings.get('ai_tagger', 'enabled') and self.ai_tagger is None:
            self.ai_tagger = AITagger()
        elif not self.settings.get('ai_tagger', 'enabled'):
            self.ai_tagger = None

    def refresh_settings(self):
        self._init_ai()

    def add_task(self, task_type, filepath, dest_folder=None, tags=None):
        self.queue.append({
            'type': task_type,
            'filepath': filepath,
            'dest_folder': dest_folder,
            'tags': tags
        })

    def run(self):
        while self.running:
            if not self.queue:
                time.sleep(0.1)
                continue

            task = self.queue.pop(0)
            self._process_task(task)

    def _process_task(self, task):
        task_type = task['type']
        filepath = task['filepath']

        try:
            if not os.path.exists(filepath):
                self.signals.error.emit(filepath, "File not found.")
                return

            filename = os.path.basename(filepath)

            final_path = filepath
            if task_type in ['move', 'copy']:
                dest_folder = task['dest_folder']
                if not dest_folder or not os.path.exists(dest_folder):
                    self.signals.error.emit(filepath, f"Destination folder missing or invalid: {dest_folder}")
                    return

                dest_path = os.path.join(dest_folder, filename)
                final_path = dest_path

                if task_type == 'move':
                    shutil.move(filepath, dest_path)
                    self.signals.progress.emit(f"Moved {filename} to {dest_folder}")
                elif task_type == 'copy':
                    shutil.copy2(filepath, dest_path)
                    self.signals.progress.emit(f"Copied {filename} to {dest_folder}")

            elif task_type == 'trash':
                send2trash(filepath)
                self.signals.progress.emit(f"Trashed {filename}")
                self.signals.finished.emit(filepath)
                return

            # Handle AI Tagging & Metadata
            if self.settings.get('ai_tagger', 'enabled') and self.ai_tagger:
                tags = self.ai_tagger.get_tags(final_path)
                if tags:
                    write_exif = self.settings.get('metadata', 'write_exif')
                    write_sidecar = self.settings.get('metadata', 'write_sidecar')
                    write_metadata(final_path, tags, write_exif, write_sidecar)
                    self.signals.progress.emit(f"Tagged {filename} with: {', '.join(tags)}")

            self.signals.finished.emit(final_path)

        except Exception as e:
            self.signals.error.emit(filepath, str(e))

    def stop(self):
        self.running = False
        self.wait()
