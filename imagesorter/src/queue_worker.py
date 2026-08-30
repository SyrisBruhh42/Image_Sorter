import errno
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Literal
from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, pyqtSlot
from send2trash import send2trash, TrashPermissionError
from src.ai_tagger import AITagger, write_metadata
from src.logger import logger
from src.settings_manager import SettingsManager


@dataclass(frozen=True)
class UndoToken:
    token_id: str
    action: Literal['move', 'copy', 'trash']
    original_path: str
    current_path: str
    timestamp: float


def atomic_move(src_path: str, dst_path: str) -> None:
    """
    Atomically moves src_path to dst_path.
    Attempts os.replace first for intra-device moves.
    If EXDEV (cross-device move) occurs, streams copy to a temporary file in dst_path's directory,
    flushes, fsyncs, copies permissions, atomically replaces with os.replace, fsyncs parent directory,
    and unlinks source.
    """
    dst_dir = os.path.dirname(dst_path) or "."
    os.makedirs(dst_dir, exist_ok=True)
    try:
        os.replace(src_path, dst_path)
    except OSError as e:
        if e.errno == errno.EXDEV:
            fd, temp_path = tempfile.mkstemp(dir=dst_dir, prefix="move_", suffix=".tmp")
            try:
                with open(src_path, 'rb') as f_in, os.fdopen(fd, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                    f_out.flush()
                    os.fsync(f_out.fileno())
                shutil.copystat(src_path, temp_path)
                os.replace(temp_path, dst_path)
                try:
                    dir_fd = os.open(dst_dir, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
                os.unlink(src_path)
            except Exception:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                raise
        else:
            raise


class WorkerSignals(QObject):
    """Signals to communicate back to the main UI thread."""
    finished = pyqtSignal(str, str)     # Emits (original_path, final_path) upon completion
    error = pyqtSignal(str, str)        # Emits (filepath, error_message)
    progress = pyqtSignal(str)          # Emits human-readable progress updates
    undo_record = pyqtSignal(object)    # Emits UndoToken object

class FileTaskRunnable(QRunnable):
    """
    A single file operation task designed for concurrent execution in QThreadPool.
    Implements antifragile retry logic, POSIX safe moves, trash fallback, and transactional UndoTokens.
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
        """Core transactional logic for moving, copying, trashing, or undoing."""
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"Source file missing: {self.filepath}")

        # Path sanitization (Zero-Trust principle)
        filepath = os.path.normpath(self.filepath)
        filename = os.path.basename(filepath)
        final_path = filepath
        undo_token: Optional[Dict[str, Any]] = None

        if self.task_type in ['move', 'copy']:
            if not self.dest_folder or not os.path.exists(self.dest_folder):
                raise FileNotFoundError(f"Destination folder missing or invalid: {self.dest_folder}")

            dest_folder = os.path.normpath(self.dest_folder)
            dest_path = os.path.join(dest_folder, filename)

            # Prevent overwriting with high-entropy collision resolution
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(filename)
                dest_path = os.path.join(dest_folder, f"{base}_{time.time_ns()}_{uuid.uuid4().hex[:6]}{ext}")
                filename = os.path.basename(dest_path)

            final_path = dest_path

            if self.task_type == 'move':
                atomic_move(filepath, dest_path)
                undo_token = UndoToken(
                    token_id=str(uuid.uuid4()),
                    action='move',
                    original_path=filepath,
                    current_path=dest_path,
                    timestamp=time.time()
                )
                self.signals.undo_record.emit(undo_token)
                self.signals.progress.emit(f"Moved {filename} to {dest_folder}")
            elif self.task_type == 'copy':
                shutil.copy2(filepath, dest_path)
                undo_token = UndoToken(
                    token_id=str(uuid.uuid4()),
                    action='copy',
                    original_path=filepath,
                    current_path=dest_path,
                    timestamp=time.time()
                )
                self.signals.undo_record.emit(undo_token)
                self.signals.progress.emit(f"Copied {filename} to {dest_folder}")

        elif self.task_type == 'trash':
            trash_folder = self.settings.get('directories', 'trash')
            if trash_folder and os.path.isdir(trash_folder):
                # Move to dedicated staging trash folder first to resolve collisions
                dest_path = os.path.join(trash_folder, filename)
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(filename)
                    dest_path = os.path.join(trash_folder, f"{base}_{time.time_ns()}_{uuid.uuid4().hex[:6]}{ext}")
                atomic_move(filepath, dest_path)
                undo_token = UndoToken(
                    token_id=str(uuid.uuid4()),
                    action='trash',
                    original_path=filepath,
                    current_path=dest_path,
                    timestamp=time.time()
                )
                self.signals.undo_record.emit(undo_token)
                self.signals.progress.emit(f"Moved {filename} to Trash folder")
                self.signals.finished.emit(filepath, dest_path)
                return
            else:
                # Fallback to OS system recycle bin via send2trash
                try:
                    send2trash(filepath)
                    self.signals.progress.emit(f"Trashed {filename}")
                    self.signals.finished.emit(filepath, filepath)
                    return
                except TrashPermissionError as e:
                    raise PermissionError(f"Trash permission denied: {e}")

        elif self.task_type == 'undo_move' or self.task_type == 'undo_trash':
            if not self.dest_folder:
                 raise ValueError("Original path (dest_folder) required to undo operation.")
            try:
                 atomic_move(filepath, self.dest_folder)
                 self.signals.progress.emit(f"Undid action: restored {filename}")
                 self.signals.finished.emit(filepath, self.dest_folder)
            except OSError as e:
                 logger.error(f"Error restoring file for {filename}: {e}")
                 self.signals.error.emit(filepath, f"Failed to restore file: {e}")
            return

        elif self.task_type == 'undo_copy':
            try:
                 os.remove(filepath)
                 self.signals.progress.emit(f"Undid copy: {filename}")
                 self.signals.finished.emit(filepath, filepath)
            except OSError as e:
                 logger.error(f"Error undoing copy for {filename}: {e}")
                 self.signals.error.emit(filepath, f"Failed to undo copy: {e}")
            return

        # Handle AI Tagging & Metadata
        if self.settings.get('ai_tagger', 'enabled') and self.ai_tagger:
            try:
                tags = self.ai_tagger.get_tags(final_path)
                if tags:
                    write_exif = self.settings.get('metadata', 'write_exif')
                    write_sidecar = self.settings.get('metadata', 'write_sidecar')
                    write_metadata(final_path, tags, write_exif, write_sidecar)
                    self.signals.progress.emit(f"Tagged {filename} with: {', '.join(tags)}")
            except Exception as tag_err:
                logger.error(f"Error writing metadata for {final_path}: {tag_err}")

        self.signals.finished.emit(filepath, final_path)

class QueueWorker(QObject):
    """
    Manages background file operations using a QThreadPool.
    Provides synergistic thread management based on hardware scan recommendations.
    """
    def __init__(self, settings_manager: SettingsManager) -> None:
        super().__init__()
        self.settings = settings_manager
        self.signals = WorkerSignals()

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
            task_type (str): 'move', 'copy', 'trash', etc.
            filepath (str): Source file path.
            dest_folder (Optional[str]): Destination folder path.
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

    def stop(self, timeout_ms: int = 3000) -> bool:
        """Waits for current tasks to finish up to timeout_ms, then clears pending tasks."""
        logger.info(f"Stopping QueueWorker (timeout: {timeout_ms}ms)...")
        finished = self.thread_pool.waitForDone(timeout_ms)
        if not finished:
            self.thread_pool.clear()
            logger.warning(
                f"QueueWorker timed out after {timeout_ms}ms. "
                f"{self.thread_pool.activeThreadCount()} active workers remaining."
            )
        else:
            logger.info("QueueWorker stopped cleanly.")
        return finished
