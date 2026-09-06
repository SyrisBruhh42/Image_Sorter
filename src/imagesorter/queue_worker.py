from __future__ import annotations

import copy
import hashlib
import os
import shutil
import time
import uuid
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from send2trash import TrashPermissionError, send2trash

from .ai_tagger import AITagger
from .logger import logger
from .metadata_io import write_metadata
from .operation_contracts import (
    OperationResult,
    OperationState,
    TaskOptions,
    create_undo_token,
)
from .operation_journal import JournalState, OperationJournal
from .settings_manager import SettingsManager


def _compute_provenance(filepath: str) -> dict[str, Any]:
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


def _verify_provenance(filepath: str, provenance: dict[str, Any]) -> None:
    """Verifies that the target file matches recorded provenance attributes."""
    if os.path.islink(filepath):
        raise ValueError(f"File is a symbolic link: {filepath}")
    if not os.path.isfile(filepath):
        raise ValueError(f"File is not a regular file: {filepath}")

    st = os.stat(filepath)
    if "size" in provenance and st.st_size != provenance["size"]:
        raise ValueError(
            f"File size mismatch for {filepath}: expected {provenance['size']}, got {st.st_size}"
        )

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


def _atomic_copy_stream(
    src_path: str, candidate_path: str, is_move: bool
) -> str:
    """
    Streams content to a temporary file in destination directory, flushes and fsyncs,
    verifies stream integrity, atomically replaces candidate path, and conditionally unlinks source.
    """
    dest_dir = os.path.dirname(candidate_path)
    temp_path = os.path.join(dest_dir, f".tmp_{uuid.uuid4().hex}")
    try:
        src_size = os.path.getsize(src_path)
        with open(src_path, "rb") as f_src, open(temp_path, "wb") as f_dst:
            shutil.copyfileobj(f_src, f_dst, length=65536)
            f_dst.flush()
            os.fsync(f_dst.fileno())

        # Post-copy streaming verification before atomic replace
        st_temp = os.stat(temp_path)
        if st_temp.st_size != src_size:
            raise OSError(
                f"Staged file size mismatch: expected {src_size}, got {st_temp.st_size}"
            )

        try:
            shutil.copystat(src_path, temp_path)
        except OSError:
            pass

        os.replace(temp_path, candidate_path)

        if is_move:
            os.remove(src_path)

        return candidate_path
    except Exception:
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
        raise


class WorkerSignals(QObject):
    """Signals to communicate back to the main UI thread."""
    finished = pyqtSignal(str)          # Emits filepath upon successful completion
    error = pyqtSignal(str, str)        # Emits (filepath, error_message)
    progress = pyqtSignal(str)          # Emits human-readable progress updates
    undo_record = pyqtSignal(dict)      # Emits standard UndoToken dictionary
    operation_result = pyqtSignal(dict)  # Emits unified OperationResult dictionary


class FileTaskRunnable(QRunnable):
    """
    A single file operation task designed for concurrent execution in QThreadPool.
    Implements antifragile retry logic, POSIX safe moves, trash fallback, and transactional UndoTokens.
    """

    def __init__(
        self,
        task_type: str,
        filepath: str,
        dest_folder: str | dict[str, Any] | None,
        settings: SettingsManager,
        ai_tagger: AITagger | None,
        signals: WorkerSignals,
        journal: OperationJournal,
        source_lock: Any,
        undo_token: dict[str, Any] | None = None,
        operation_id: str | None = None,
        task_options: TaskOptions | None = None,
    ) -> None:
        super().__init__()
        self.task_type = task_type
        if isinstance(dest_folder, dict):
            self.undo_token: dict[str, Any] | None = dest_folder
            self.dest_folder: str | None = dest_folder.get("original")
        else:
            self.undo_token = undo_token
            self.dest_folder = dest_folder

        if self.dest_folder is None and self.undo_token and isinstance(self.undo_token, dict):
            self.dest_folder = self.undo_token.get("original")

        self.filepath = filepath
        self.settings = settings
        self.ai_tagger = ai_tagger
        self.signals = signals
        self.journal = journal
        self.source_lock = source_lock
        self.operation_id = operation_id or uuid.uuid4().hex
        self.task_options = task_options or TaskOptions()
        self.max_retries = 3

    @pyqtSlot()
    def run(self) -> None:
        """Executes the file task with retries under per-source lock."""
        with self.source_lock:
            for attempt in range(self.max_retries):
                try:
                    self._execute()
                    return  # Success, exit loop
                except PermissionError as e:
                    if attempt < self.max_retries - 1:
                        logger.warning(
                            f"Permission error on {self.filepath}, retrying ({attempt+1}/{self.max_retries})..."
                        )
                        time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                    else:
                        err_msg = f"Permission Denied: {e}"
                        logger.error(
                            f"Failed to process {self.filepath} after {self.max_retries} attempts: {e}"
                        )
                        self._handle_error(err_msg)
                        return
                except Exception as e:
                    logger.error(f"Unexpected error processing {self.filepath}: {e}", exc_info=True)
                    self._handle_error(str(e))
                    return

    def _handle_error(self, err_msg: str) -> None:
        """Handles task failure by logging in journal and emitting failure signals."""
        self.journal.record_terminal(
            operation_id=self.operation_id,
            state=JournalState.FAILED,
            error=err_msg,
        )
        self.signals.error.emit(self.filepath, err_msg)

        result = OperationResult(
            operation_id=self.operation_id,
            action=self.task_type,
            source_path=self.filepath,
            destination_path=None,
            state=OperationState.FAILED,
            undo_token=None,
            error=err_msg,
            warning=None,
            view_generation=self.task_options.view_generation,
        )
        self.signals.operation_result.emit(result.to_dict())

    def _get_setting(self, section: str, key: str, default: Any = None) -> Any:
        """Helper to read setting value from options snapshot or SettingsManager."""
        snapshot = self.task_options.settings_snapshot
        if snapshot and section in snapshot and isinstance(snapshot[section], dict) and key in snapshot[section]:
            return snapshot[section][key]
        val = self.settings.get(section, key)
        return val if val is not None else default

    def _execute(self) -> None:
        """Core transactional logic for moving, copying, trashing, or undoing."""
        filepath = os.path.normpath(self.filepath)

        self.journal.record_intent(
            operation_id=self.operation_id,
            action=self.task_type,
            source_path=filepath,
            destination_path=self.dest_folder if isinstance(self.dest_folder, str) else None,
            task_options=self.task_options.to_dict(),
        )

        final_path = filepath
        undo_token: dict[str, Any] | None = None
        state = OperationState.COMPLETED
        warning_msg: str | None = None
        action_verb = "Processed"

        if self.task_type in ["move", "copy"]:
            if not os.path.lexists(filepath):
                raise FileNotFoundError(f"Source file missing: {filepath}")
            if os.path.islink(filepath):
                raise ValueError(f"Source file is a symbolic link: {filepath}")
            if not os.path.isfile(filepath):
                raise ValueError(f"Source path is not a regular file: {filepath}")

            if not self.dest_folder:
                raise ValueError("Destination folder required for move/copy operation.")

            dest_folder = os.path.normpath(self.dest_folder)
            if not os.path.exists(dest_folder) or not os.path.isdir(dest_folder) or os.path.islink(dest_folder):
                raise FileNotFoundError(f"Destination folder missing or invalid: {dest_folder}")

            filename = os.path.basename(filepath)
            intended_dest = os.path.join(dest_folder, filename)

            if os.path.realpath(filepath) == os.path.realpath(intended_dest):
                raise ValueError(f"Source and destination resolve to the same effective location: {filepath}")
            if os.path.exists(intended_dest) and os.path.samefile(filepath, intended_dest):
                raise ValueError(f"Source and destination resolve to the same file: {filepath}")

            candidate_path = _reserve_candidate_path(dest_folder, filename)
            final_path = candidate_path

            self.journal.record_staged(self.operation_id, destination_path=candidate_path)

            _atomic_copy_stream(filepath, candidate_path, is_move=(self.task_type == "move"))
            self.journal.record_committed(self.operation_id, destination_path=candidate_path)

            action_verb = f"{'Moved' if self.task_type == 'move' else 'Copied'} {os.path.basename(candidate_path)} to {dest_folder}"

        elif self.task_type == "trash":
            if not os.path.lexists(filepath):
                raise FileNotFoundError(f"Source file missing: {filepath}")
            if os.path.islink(filepath):
                raise ValueError(f"Source file is a symbolic link: {filepath}")
            if not os.path.isfile(filepath):
                raise ValueError(f"Source path is not a regular file: {filepath}")

            trash_folder = self._get_setting("directories", "trash")
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

                self.journal.record_staged(self.operation_id, destination_path=candidate_path)
                _atomic_copy_stream(filepath, candidate_path, is_move=True)
                self.journal.record_committed(self.operation_id, destination_path=candidate_path)

                action_verb = f"Moved {os.path.basename(candidate_path)} to Trash folder"
            else:
                try:
                    send2trash(filepath)
                    self.journal.record_committed(self.operation_id, destination_path=filepath)
                    action_verb = f"Trashed {os.path.basename(filepath)}"
                except TrashPermissionError as e:
                    raise PermissionError(f"Trash permission denied: {e}")

        elif self.task_type in ["undo_move", "undo_trash"]:
            if self.undo_token and isinstance(self.undo_token, dict) and "provenance" in self.undo_token:
                _verify_provenance(filepath, self.undo_token["provenance"])

            if not self.dest_folder:
                raise ValueError("Original path (dest_folder) required to undo operation.")

            original_path = os.path.normpath(self.dest_folder)

            if not os.path.lexists(filepath):
                raise FileNotFoundError(f"Source file missing for undo: {filepath}")

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

            self.journal.record_staged(self.operation_id, destination_path=original_path)
            _atomic_copy_stream(filepath, original_path, is_move=True)
            final_path = original_path
            self.journal.record_committed(self.operation_id, destination_path=original_path)

            action_verb = f"Undid action: restored {os.path.basename(original_path)}"

        elif self.task_type == "undo_copy":
            if not os.path.lexists(filepath):
                raise FileNotFoundError(f"Source file missing for undo_copy: {filepath}")

            if self.undo_token and isinstance(self.undo_token, dict) and "provenance" in self.undo_token:
                _verify_provenance(filepath, self.undo_token["provenance"])

            os.remove(filepath)
            final_path = filepath
            self.journal.record_committed(self.operation_id, destination_path=filepath)

            action_verb = f"Undid copy: {os.path.basename(filepath)}"

        # Handle AI Tagging & Metadata for non-undo operations
        if self.task_type not in ["undo_move", "undo_trash", "undo_copy"] and os.path.exists(final_path):
            ai_enabled = self._get_setting("ai_tagger", "enabled")
            if ai_enabled and self.ai_tagger:
                try:
                    threshold = self._get_setting("ai_tagger", "threshold", 0.5)
                    # Support threshold kwarg if AITagger get_tags accepts it
                    try:
                        tags = self.ai_tagger.get_tags(final_path, threshold=threshold)
                    except TypeError:
                        tags = self.ai_tagger.get_tags(final_path)

                    if tags:
                        write_exif = self._get_setting("metadata", "write_exif", True)
                        write_sidecar = self._get_setting("metadata", "write_sidecar", False)
                        write_metadata(final_path, tags, write_exif, write_sidecar)
                        action_verb += f" and tagged with: {', '.join(tags)}"
                except Exception as meta_err:
                    logger.warning(f"Metadata write warning for {final_path}: {meta_err}")
                    state = OperationState.COMPLETED_WITH_WARNING
                    warning_msg = f"Metadata tagging failed: {meta_err}"

        # Calculate FINAL provenance on committed destination file AFTER AI metadata changes
        if self.task_type in ["move", "copy", "trash"] and os.path.exists(final_path):
            final_provenance = _compute_provenance(final_path)
            orig_src = filepath
            undo_token = create_undo_token(
                token_id=str(uuid.uuid4()),
                action=self.task_type,
                original_path=orig_src,
                current_path=final_path,
                timestamp=time.time(),
                provenance=final_provenance,
            )

        # Journal update for terminal completion
        self.journal.record_terminal(
            operation_id=self.operation_id,
            state=state,
            destination_path=final_path,
            undo_token=undo_token,
            warning=warning_msg,
        )

        # Signal emissions strictly in ordered sequence
        self.signals.progress.emit(action_verb)

        if undo_token:
            self.signals.undo_record.emit(undo_token)

        self.signals.finished.emit(final_path)

        op_result = OperationResult(
            operation_id=self.operation_id,
            action=self.task_type,
            source_path=filepath,
            destination_path=final_path,
            state=state,
            undo_token=undo_token,
            error=None,
            warning=warning_msg,
            view_generation=self.task_options.view_generation,
        )
        self.signals.operation_result.emit(op_result.to_dict())


class QueueWorker(QObject):
    """
    Manages background file operations using a QThreadPool.
    Provides synergistic thread management based on hardware scan recommendations.
    """

    def __init__(self, settings_manager: SettingsManager) -> None:
        super().__init__()
        self.settings = settings_manager
        self.signals = WorkerSignals()
        self.journal = OperationJournal()

        # Reconcile any crashed/interrupted operations from previous run
        try:
            self.journal.reconcile_interrupted_operations()
        except Exception as e:
            logger.warning(f"Error reconciling interrupted journal operations: {e}")

        max_threads = self.settings.get("advanced", "worker_threads") or 2
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(int(max_threads))

        self._source_locks: dict[str, Any] = {}
        import threading
        self._source_locks_guard_obj = threading.Lock()

        self.ai_tagger: AITagger | None = None
        self._init_ai()
        logger.info(f"QueueWorker initialized with max {self.thread_pool.maxThreadCount()} threads.")

    def _get_source_lock(self, canonical_path: str) -> Any:
        """Retrieves or creates a thread lock for a given canonical source path."""
        if not hasattr(self, "_source_locks_guard_obj"):
            import threading
            self._source_locks_guard_obj = threading.Lock()

        with self._source_locks_guard_obj:
            if canonical_path not in self._source_locks:
                import threading
                self._source_locks[canonical_path] = threading.Lock()
            return self._source_locks[canonical_path]

    def _init_ai(self) -> None:
        """Initializes or disables the AI tagger based on settings."""
        is_enabled = self.settings.get("ai_tagger", "enabled")
        if is_enabled and self.ai_tagger is None:
            logger.info("Initializing AI Tagger in QueueWorker.")
            # Check for hardware_acceleration parameter if supported by AITagger
            hw_accel = self.settings.get("advanced", "hardware_acceleration")
            if hw_accel is None:
                hw_accel = True
            try:
                self.ai_tagger = AITagger(hardware_acceleration=hw_accel)
            except TypeError:
                self.ai_tagger = AITagger()
        elif not is_enabled and self.ai_tagger is not None:
            logger.info("Disabling AI Tagger in QueueWorker.")
            self.ai_tagger = None

    def refresh_settings(self) -> None:
        """Called when settings are updated to apply new configurations."""
        self._init_ai()
        new_threads = self.settings.get("advanced", "worker_threads")
        if new_threads and int(new_threads) != self.thread_pool.maxThreadCount():
            self.thread_pool.setMaxThreadCount(int(new_threads))
            logger.info(f"Updated QueueWorker max threads to {new_threads}.")

    def add_task(
        self,
        task_type: str,
        filepath: str,
        dest_folder: str | dict[str, Any] | None = None,
        undo_token: dict[str, Any] | None = None,
        *,
        operation_id: str | None = None,
        task_options: dict[str, Any] | TaskOptions | None = None,
    ) -> str:
        """
        Submits a new task to the thread pool for execution and returns its operation ID.

        Args:
            task_type (str): 'move', 'copy', 'trash', 'undo_move', 'undo_copy', 'undo_trash', etc.
            filepath (str): Source file path.
            dest_folder (Optional[Union[str, Dict[str, Any]]]): Destination folder path or UndoToken dict.
            undo_token (Optional[Dict[str, Any]]): Optional UndoToken containing provenance metadata.
            operation_id (Optional[str]): Explicit unique operation identifier.
            task_options (Optional[Union[Dict[str, Any], TaskOptions]]): Snapshot options (view generation, settings).

        Returns:
            str: Operation ID for tracking task completion.
        """
        if operation_id is None:
            operation_id = uuid.uuid4().hex

        # Resolve options & settings snapshot at dispatch time
        def _get_snapshot() -> dict[str, Any]:
            if hasattr(self.settings, "snapshot"):
                return self.settings.snapshot()
            if hasattr(self.settings, "get_all"):
                return copy.deepcopy(self.settings.get_all())
            return copy.deepcopy(getattr(self.settings, "settings", {}))

        if task_options is None:
            parsed_options = TaskOptions(settings_snapshot=_get_snapshot())
        elif isinstance(task_options, dict):
            if "settings_snapshot" not in task_options:
                task_options["settings_snapshot"] = _get_snapshot()
            parsed_options = TaskOptions.from_dict(task_options)
        else:
            parsed_options = task_options

        canonical_path = os.path.realpath(filepath)
        source_lock = self._get_source_lock(canonical_path)

        logger.debug(f"Adding task {operation_id}: {task_type} {filepath}")
        task = FileTaskRunnable(
            task_type=task_type,
            filepath=filepath,
            dest_folder=dest_folder,
            settings=self.settings,
            ai_tagger=self.ai_tagger,
            signals=self.signals,
            journal=self.journal,
            source_lock=source_lock,
            undo_token=undo_token,
            operation_id=operation_id,
            task_options=parsed_options,
        )
        self.thread_pool.start(task)
        return operation_id

    def stop(self) -> None:
        """Waits for current tasks to finish and stops accepting new ones."""
        logger.info("Stopping QueueWorker, waiting for tasks to finish...")
        self.thread_pool.waitForDone()
        logger.info("QueueWorker stopped.")
