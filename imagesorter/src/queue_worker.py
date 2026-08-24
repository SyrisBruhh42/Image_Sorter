import os
import shutil
import time
from typing import Dict, Any, Optional, List
from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, pyqtSlot
from send2trash import send2trash, TrashPermissionError
from src.ai_tagger import AITagger, write_metadata
from src.logger import logger
from src.settings_manager import SettingsManager

class WorkerSignals(QObject):
    """Signals to communicate back to the main UI thread."""
    finished = pyqtSignal(str)          # Emits filepath upon successful completion
    error = pyqtSignal(str, str)        # Emits (filepath, error_message)
    progress = pyqtSignal(str)          # Emits human-readable progress updates
    undo_record = pyqtSignal(dict)      # Emits data for the undo stack

class FileTaskRunnable(QRunnable):
    """
    A single file operation task designed for concurrent execution in QThreadPool.
    Implements antifragile retry logic and robust path sanitization.
    """
    def __init__(self, task_type: str, filepath: str, dest_folder: Optional[str],
                 settings: SettingsManager, ai_tagger: Optional[AITagger], signals: WorkerSignals) -> None:
        super().__init__()
        self.task_type = task_type
        self.filepath = filepath
        self.dest_folder = dest_folder
        self.settings = settings
        self.ai_tagger = ai_tagger
        self.signals = signals
        self.max_retries = 3

    @pyqtSlot()
    def run(self) -> None:
        """Executes the file task with retries."""
        for attempt in range(self.max_retries):
            try:
                self._execute()
                return  # Success, exit loop
            except PermissionError as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Permission error on {self.filepath}, retrying ({attempt+1}/{self.max_retries})...")
                    time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                else:
                    logger.error(f"Failed to process {self.filepath} after {self.max_retries} attempts: {e}")
                    self.signals.error.emit(self.filepath, f"Permission Denied: {e}")
            except Exception as e:
                logger.error(f"Unexpected error processing {self.filepath}: {e}", exc_info=True)
                self.signals.error.emit(self.filepath, str(e))
                return  # Non-retryable error

    def _execute(self) -> None:
        """The core logic for moving, copying, or trashing."""
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"Source file missing: {self.filepath}")

        # Path sanitization (Zero-Trust principle)
        filepath = os.path.normpath(self.filepath)
        filename = os.path.basename(filepath)
        final_path = filepath
        undo_data = None

        if self.task_type in ['move', 'copy']:
            if not self.dest_folder or not os.path.exists(self.dest_folder):
                raise FileNotFoundError(f"Destination folder missing or invalid: {self.dest_folder}")

            dest_folder = os.path.normpath(self.dest_folder)
            dest_path = os.path.join(dest_folder, filename)

            # Prevent overwriting
            if os.path.exists(dest_path):
                 base, ext = os.path.splitext(filename)
                 dest_path = os.path.join(dest_folder, f"{base}_{int(time.time())}{ext}")
                 filename = os.path.basename(dest_path)

            final_path = dest_path

            if self.task_type == 'move':
                shutil.move(filepath, dest_path)
                undo_data = {'action': 'move', 'original': filepath, 'current': dest_path}
                self.signals.progress.emit(f"Moved {filename} to {dest_folder}")
            elif self.task_type == 'copy':
                shutil.copy2(filepath, dest_path)
                undo_data = {'action': 'copy', 'original': filepath, 'current': dest_path}
                self.signals.progress.emit(f"Copied {filename} to {dest_folder}")

        elif self.task_type == 'trash':
            try:
                send2trash(filepath)
                # Note: Trashed items generally can't be safely 'undone' cross-platform in python easily
                # without OS specific APIs, so we omit undo data for trash.
                self.signals.progress.emit(f"Trashed {filename}")
                self.signals.finished.emit(filepath)
                return
            except TrashPermissionError as e:
                 raise PermissionError(f"Trash permission denied: {e}")

        # Handle AI Tagging & Metadata
        if self.settings.get('ai_tagger', 'enabled') and self.ai_tagger:
            tags = self.ai_tagger.get_tags(final_path)
            if tags:
                write_exif = self.settings.get('metadata', 'write_exif')
                write_sidecar = self.settings.get('metadata', 'write_sidecar')
                write_metadata(final_path, tags, write_exif, write_sidecar)
                self.signals.progress.emit(f"Tagged {filename} with: {', '.join(tags)}")

        if undo_data:
             self.signals.undo_record.emit(undo_data)

        self.signals.finished.emit(final_path)

class QueueWorker(QObject):
    """
    Manages background file operations using a QThreadPool.
    Provides synergistic thread management based on hardware scan recommendations.
    """
    def __init__(self, settings_manager: SettingsManager) -> None:
        super().__init__()
        self.settings = settings_manager
        self.signals = WorkerSignals()

        # Determine optimal threads (Antifragility via resource management)
        # Default to 2 if hardware scan wasn't run or failed
        max_threads = self.settings.get('advanced', 'worker_threads') or 2
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(int(max_threads))
        logger.info(f"QueueWorker initialized with max {self.thread_pool.maxThreadCount()} threads.")

        self.ai_tagger: Optional[AITagger] = None
        self._init_ai()

    def _init_ai(self) -> None:
        """Initializes or disables the AI tagger based on settings."""
        is_enabled = self.settings.get('ai_tagger', 'enabled')
        if is_enabled and self.ai_tagger is None:
            logger.info("Initializing AI Tagger in QueueWorker.")
            self.ai_tagger = AITagger()
        elif not is_enabled and self.ai_tagger is not None:
             logger.info("Disabling AI Tagger in QueueWorker.")
             self.ai_tagger = None

    def refresh_settings(self) -> None:
        """Called when settings are updated to apply new configurations."""
        self._init_ai()
        new_threads = self.settings.get('advanced', 'worker_threads')
        if new_threads and int(new_threads) != self.thread_pool.maxThreadCount():
            self.thread_pool.setMaxThreadCount(int(new_threads))
            logger.info(f"Updated QueueWorker max threads to {new_threads}.")

    def add_task(self, task_type: str, filepath: str, dest_folder: Optional[str] = None) -> None:
        """
        Submits a new task to the thread pool for execution.

        Args:
            task_type (str): 'move', 'copy', or 'trash'.
            filepath (str): Source file path.
            dest_folder (Optional[str]): Destination folder path (for move/copy).
        """
        logger.debug(f"Adding task: {task_type} {filepath}")
        task = FileTaskRunnable(
            task_type=task_type,
            filepath=filepath,
            dest_folder=dest_folder,
            settings=self.settings,
            ai_tagger=self.ai_tagger,
            signals=self.signals
        )
        self.thread_pool.start(task)

    def stop(self) -> None:
        """Waits for current tasks to finish and stops accepting new ones."""
        logger.info("Stopping QueueWorker, waiting for tasks to finish...")
        self.thread_pool.waitForDone()
        logger.info("QueueWorker stopped.")
