import os
import shutil
import time
import uuid
import hashlib
from typing import Dict, Any, Optional, List, Union
from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, pyqtSlot
from send2trash import send2trash, TrashPermissionError
from .ai_tagger import AITagger, write_metadata
from .logger import logger
from .settings_manager import SettingsManager


def _compute_provenance(filepath: str) -> Dict[str, Any]:
    """Computes file metadata and SHA-256 digest for provenance tracking."""
    st = os.stat(filepath)
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return {
        "size": st.st_size,
        "mtime": st.st_mtime,
        "dev": st.st_dev,
        "ino": st.st_ino,
        "sha256": h.hexdigest(),
        "is_symlink": os.path.islink(filepath),
        "is_reg": os.path.isfile(filepath) and not os.path.islink(filepath),
    }


def _verify_provenance(filepath: str, provenance: Dict[str, Any]) -> None:
    """Verifies that the target file matches recorded provenance attributes."""
    if os.path.islink(filepath):
        raise ValueError(f"File is a symbolic link: {filepath}")
    if not os.path.isfile(filepath):
        raise ValueError(f"File is not a regular file: {filepath}")

    st = os.stat(filepath)
    if "size" in provenance and st.st_size != provenance["size"]:
        raise ValueError(f"File size mismatch for {filepath}: expected {provenance['size']}, got {st.st_size}")

    if "sha256" in provenance:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        digest = h.hexdigest()
        if digest != provenance["sha256"]:
            raise ValueError(f"SHA-256 digest mismatch for {filepath}: file contents altered.")


def _reserve_candidate_path(dest_folder: str, filename: str) -> str:
    """
    Atomically creates and reserves an unpopulated destination file path using incrementing suffixes.
    e.g. photo.jpg, photo_1.jpg, photo_2.jpg
    """
    base, ext = os.path.splitext(filename)
    counter = 0
    while True:
        candidate_name = filename if counter == 0 else f"{base}_{counter}{ext}"
        candidate_path = os.path.join(dest_folder, candidate_name)
        try:
            fd = os.open(candidate_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
            os.close(fd)
            return candidate_path
        except FileExistsError:
            counter += 1


def _atomic_copy_stream(src_path: str, candidate_path: str, is_move: bool) -> None:
    """
    Streams content to a temporary file in destination directory, flushes and fsyncs,
    atomically replaces the reserved candidate path, and conditionally unlinks the source.
    """
    dest_dir = os.path.dirname(candidate_path)
    temp_path = os.path.join(dest_dir, f".tmp_{uuid.uuid4().hex}")
    try:
        with open(src_path, "rb") as f_src, open(temp_path, "wb") as f_dst:
            shutil.copyfileobj(f_src, f_dst, length=65536)
            f_dst.flush()
            os.fsync(f_dst.fileno())

        try:
            shutil.copystat(src_path, temp_path)
        except OSError:
            pass

        os.replace(temp_path, candidate_path)

        if is_move:
            os.remove(src_path)
    except Exception as e:
        if os.path.lexists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        if os.path.lexists(candidate_path):
            try:
                os.remove(candidate_path)
            except OSError:
                pass
        raise e


class WorkerSignals(QObject):
    """Signals to communicate back to the main UI thread."""
    finished = pyqtSignal(str)          # Emits filepath upon successful completion
    error = pyqtSignal(str, str)        # Emits (filepath, error_message)
    progress = pyqtSignal(str)          # Emits human-readable progress updates
    undo_record = pyqtSignal(dict)      # Emits standard UndoToken dictionary

class FileTaskRunnable(QRunnable):
    """
    A single file operation task designed for concurrent execution in QThreadPool.
    Implements antifragile retry logic, POSIX safe moves, trash fallback, and transactional UndoTokens.
    """
    def __init__(self, task_type: str, filepath: str, dest_folder: Optional[Union[str, Dict[str, Any]]],
                 settings: SettingsManager, ai_tagger: Optional[AITagger], signals: WorkerSignals,
                 undo_token: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self.task_type = task_type
        if isinstance(dest_folder, dict):
            self.undo_token: Optional[Dict[str, Any]] = dest_folder
            self.dest_folder: Optional[str] = self.undo_token.get("original")
        else:
            self.undo_token = undo_token
            self.dest_folder = dest_folder
        self.filepath = filepath
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
        filepath = os.path.normpath(self.filepath)

        if not os.path.lexists(filepath):
            raise FileNotFoundError(f"Source file missing: {filepath}")
        if os.path.islink(filepath):
            raise ValueError(f"Source file is a symbolic link: {filepath}")
        if not os.path.isfile(filepath):
            raise ValueError(f"Source path is not a regular file: {filepath}")

        final_path = filepath
        undo_token: Optional[Dict[str, Any]] = None

        if self.task_type in ['move', 'copy']:
            if not self.dest_folder:
                raise ValueError("Destination folder required for move/copy operation.")

            dest_folder = os.path.normpath(self.dest_folder)
            if not os.path.exists(dest_folder) or not os.path.isdir(dest_folder) or os.path.islink(dest_folder):
                raise FileNotFoundError(f"Destination folder missing or invalid: {dest_folder}")

            filename = os.path.basename(filepath)
            intended_dest = os.path.join(dest_folder, filename)

            # Rejection: Same file or effective location
            if os.path.realpath(filepath) == os.path.realpath(intended_dest):
                raise ValueError(f"Source and destination resolve to the same effective location: {filepath}")
            if os.path.exists(intended_dest) and os.path.samefile(filepath, intended_dest):
                raise ValueError(f"Source and destination resolve to the same file: {filepath}")

            # Atomic path reservation
            candidate_path = _reserve_candidate_path(dest_folder, filename)
            final_path = candidate_path

            # Stream copy & atomic finalize
            _atomic_copy_stream(filepath, candidate_path, is_move=(self.task_type == 'move'))

            provenance = _compute_provenance(candidate_path)
            undo_token = {
                'token_id': str(uuid.uuid4()),
                'action': self.task_type,
                'original': filepath,
                'current': candidate_path,
                'timestamp': time.time(),
                'provenance': provenance
            }

            action_verb = "Moved" if self.task_type == 'move' else "Copied"
            self.signals.progress.emit(f"{action_verb} {os.path.basename(candidate_path)} to {dest_folder}")

        elif self.task_type == 'trash':
            trash_folder = self.settings.get('directories', 'trash')
            if trash_folder and os.path.exists(trash_folder) and os.path.isdir(trash_folder) and not os.path.islink(trash_folder):
                trash_folder = os.path.normpath(trash_folder)
                filename = os.path.basename(filepath)
                intended_dest = os.path.join(trash_folder, filename)

                if os.path.realpath(filepath) == os.path.realpath(intended_dest):
                    raise ValueError(f"Source file is already in the trash folder: {filepath}")
                if os.path.exists(intended_dest) and os.path.samefile(filepath, intended_dest):
                    raise ValueError(f"Source file is already in the trash folder: {filepath}")

                candidate_path = _reserve_candidate_path(trash_folder, filename)
                final_path = candidate_path

                _atomic_copy_stream(filepath, candidate_path, is_move=True)

                provenance = _compute_provenance(candidate_path)
                undo_token = {
                    'token_id': str(uuid.uuid4()),
                    'action': 'trash',
                    'original': filepath,
                    'current': candidate_path,
                    'timestamp': time.time(),
                    'provenance': provenance
                }
                self.signals.progress.emit(f"Moved {os.path.basename(candidate_path)} to Trash folder")
                self.signals.finished.emit(final_path)
                if undo_token:
                    self.signals.undo_record.emit(undo_token)
                return
            else:
                try:
                    send2trash(filepath)
                    self.signals.progress.emit(f"Trashed {os.path.basename(filepath)}")
                    self.signals.finished.emit(filepath)
                    return
                except TrashPermissionError as e:
                    raise PermissionError(f"Trash permission denied: {e}")

        elif self.task_type in ['undo_move', 'undo_trash']:
            if not self.dest_folder:
                raise ValueError("Original path (dest_folder) required to undo operation.")

            original_path = os.path.normpath(self.dest_folder)

            if self.undo_token and 'provenance' in self.undo_token:
                _verify_provenance(filepath, self.undo_token['provenance'])

            if os.path.lexists(original_path):
                raise FileExistsError(f"Cannot restore file: destination path already exists: {original_path}")

            orig_dir = os.path.dirname(original_path)
            if not os.path.exists(orig_dir) or not os.path.isdir(orig_dir) or os.path.islink(orig_dir):
                raise FileNotFoundError(f"Original directory missing or invalid: {orig_dir}")

            try:
                fd = os.open(original_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
                os.close(fd)
            except FileExistsError:
                raise FileExistsError(f"Cannot restore file: destination path already exists: {original_path}")

            _atomic_copy_stream(filepath, original_path, is_move=True)
            self.signals.progress.emit(f"Undid action: restored {os.path.basename(original_path)}")
            self.signals.finished.emit(original_path)
            return

        elif self.task_type == 'undo_copy':
            if self.undo_token and 'provenance' in self.undo_token:
                _verify_provenance(filepath, self.undo_token['provenance'])

            os.remove(filepath)
            self.signals.progress.emit(f"Undid copy: {os.path.basename(filepath)}")
            self.signals.finished.emit(filepath)
            return

        # Handle AI Tagging & Metadata
        if self.settings.get('ai_tagger', 'enabled') and self.ai_tagger:
            tags = self.ai_tagger.get_tags(final_path)
            if tags:
                write_exif = self.settings.get('metadata', 'write_exif')
                write_sidecar = self.settings.get('metadata', 'write_sidecar')
                write_metadata(final_path, tags, write_exif, write_sidecar)
                self.signals.progress.emit(f"Tagged {os.path.basename(final_path)} with: {', '.join(tags)}")

        if undo_token:
            self.signals.undo_record.emit(undo_token)

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

    def add_task(self, task_type: str, filepath: str, dest_folder: Optional[Union[str, Dict[str, Any]]] = None, undo_token: Optional[Dict[str, Any]] = None) -> None:
        """
        Submits a new task to the thread pool for execution.

        Args:
            task_type (str): 'move', 'copy', 'trash', etc.
            filepath (str): Source file path.
            dest_folder (Optional[Union[str, Dict[str, Any]]]): Destination folder path or UndoToken dict.
            undo_token (Optional[Dict[str, Any]]): Optional UndoToken containing provenance metadata.
        """
        logger.debug(f"Adding task: {task_type} {filepath}")
        task = FileTaskRunnable(
            task_type=task_type,
            filepath=filepath,
            dest_folder=dest_folder,
            settings=self.settings,
            ai_tagger=self.ai_tagger,
            signals=self.signals,
            undo_token=undo_token
        )
        self.thread_pool.start(task)

    def stop(self) -> None:
        """Waits for current tasks to finish and stops accepting new ones."""
        logger.info("Stopping QueueWorker, waiting for tasks to finish...")
        self.thread_pool.waitForDone()
        logger.info("QueueWorker stopped.")
