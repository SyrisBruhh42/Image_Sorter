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
    validate_undo_token,
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


def _reserve_candidate_paths(
    dest_folder: str, filename: str, *, include_sidecar: bool
) -> tuple[str, str | None]:
    """
    Reserve an image and, when needed, its exact sidecar name without overwriting.
    """
    base, ext = os.path.splitext(filename)
    counter = 0
    while True:
        candidate_name = filename if counter == 0 else f"{base}_{counter}{ext}"
        candidate_path = os.path.join(dest_folder, candidate_name)
        paths = [candidate_path]
        if include_sidecar:
            paths.append(candidate_path + ".txt")
        reserved: list[str] = []
        try:
            for path in paths:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
                os.close(fd)
                reserved.append(path)
            return candidate_path, paths[1] if include_sidecar else None
        except FileExistsError:
            for path in reserved:
                try:
                    os.remove(path)
                except OSError:
                    pass
            counter += 1


def _reserve_candidate_path(dest_folder: str, filename: str) -> str:
    """Backward-compatible single-file destination reservation."""
    candidate, _sidecar = _reserve_candidate_paths(
        dest_folder, filename, include_sidecar=False
    )
    return candidate


def _atomic_copy_stream(
    src_path: str,
    candidate_path: str,
    is_move: bool,
    *,
    temp_path: str | None = None,
) -> str:
    """
    Streams content to a temporary file in destination directory, flushes and fsyncs,
    verifies stream integrity, atomically replaces candidate path, and conditionally unlinks source.
    """
    dest_dir = os.path.dirname(candidate_path)
    temp_path = temp_path or os.path.join(
        dest_dir, f".imagesorter-copy-{uuid.uuid4().hex}.tmp"
    )
    replaced = False
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
        replaced = True

        if is_move:
            os.remove(src_path)

        return candidate_path
    except Exception:
        if os.path.lexists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        # If a move removed the source, the destination is now the only copy and
        # must be preserved for truthful recovery rather than cleaned as staging.
        source_was_removed = is_move and not os.path.lexists(src_path)
        if os.path.lexists(candidate_path) and not (replaced and source_was_removed):
            try:
                os.remove(candidate_path)
            except OSError:
                pass
        raise


class WorkerSignals(QObject):
    """Signals to communicate back to the main UI thread."""
    progress = pyqtSignal(str)
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
        try:
            self.journal.record_terminal(
                operation_id=self.operation_id,
                state=JournalState.FAILED,
                error=err_msg,
            )
        except Exception:
            logger.error("Could not record failed operation in journal", exc_info=True)
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

    def _temp_path(self, parent: str, label: str) -> str:
        return os.path.join(
            parent, f".imagesorter-{self.operation_id}-{label}-{uuid.uuid4().hex}.tmp"
        )

    def _artifact(self, path: str, kind: str) -> dict[str, Any]:
        record: dict[str, Any] = {
            "path": path,
            "kind": kind,
            "owner_token": self.journal.owner_token,
        }
        if os.path.lexists(path) and not os.path.islink(path):
            stat = os.stat(path)
            record.update({"dev": stat.st_dev, "ino": stat.st_ino})
        return record

    @staticmethod
    def _validate_regular_path(path: str, *, label: str) -> None:
        if not os.path.lexists(path):
            raise FileNotFoundError(f"{label} file missing: {path}")
        if os.path.islink(path):
            raise ValueError(f"{label} file is a symbolic link: {path}")
        if not os.path.isfile(path):
            raise ValueError(f"{label} path is not a regular file: {path}")

    def _stage_transfer(
        self, source: str, destination_folder: str, *, is_move: bool
    ) -> tuple[str, str | None]:
        """Copy an image/sidecar set, then remove sources only after both commit."""
        source_sidecar = source + ".txt"
        include_sidecar = os.path.lexists(source_sidecar)
        if include_sidecar:
            self._validate_regular_path(source_sidecar, label="Sidecar")

        candidate, candidate_sidecar = _reserve_candidate_paths(
            destination_folder,
            os.path.basename(source),
            include_sidecar=include_sidecar,
        )
        main_temp = self._temp_path(destination_folder, "image")
        artifacts = [
            self._artifact(candidate, "reservation"),
            self._artifact(main_temp, "temp"),
        ]
        side_temp: str | None = None
        if candidate_sidecar:
            side_temp = self._temp_path(destination_folder, "sidecar")
            artifacts.extend(
                [
                    self._artifact(candidate_sidecar, "reservation"),
                    self._artifact(side_temp, "temp"),
                ]
            )
        try:
            self.journal.record_staged(
                self.operation_id, destination_path=candidate, artifacts=artifacts
            )
        except Exception:
            for target in (candidate_sidecar, candidate):
                if target and os.path.lexists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
            raise

        try:
            _atomic_copy_stream(
                source, candidate, is_move=False, temp_path=main_temp
            )
            if candidate_sidecar and side_temp:
                _atomic_copy_stream(
                    source_sidecar,
                    candidate_sidecar,
                    is_move=False,
                    temp_path=side_temp,
                )
        except Exception:
            for target in (candidate_sidecar, candidate):
                if target and os.path.lexists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
            raise

        warning: str | None = None
        if is_move:
            try:
                os.remove(source)
            except OSError:
                for target in (candidate_sidecar, candidate):
                    if target and os.path.lexists(target):
                        try:
                            os.remove(target)
                        except OSError:
                            pass
                raise
            if include_sidecar:
                try:
                    os.remove(source_sidecar)
                except OSError as exc:
                    warning = (
                        "Image moved, but the original sidecar could not be removed: "
                        f"{exc}"
                    )
        return candidate, warning

    @staticmethod
    def _validate_companions(token: dict[str, Any]) -> list[dict[str, Any]]:
        companions = token.get("companions") or []
        if not isinstance(companions, list):
            raise ValueError("Undo token companion records are invalid")
        for companion in companions:
            if not isinstance(companion, dict):
                raise ValueError("Undo token companion record is invalid")
            if not all(
                isinstance(companion.get(key), expected)
                for key, expected in (
                    ("original", str),
                    ("current", str),
                    ("provenance", dict),
                )
            ):
                raise ValueError("Undo token companion record is incomplete")
        return companions

    def _restore_move_set(
        self, current: str, token: dict[str, Any]
    ) -> tuple[str, str | None]:
        """Restore a validated move and every recorded companion as one file set."""
        original = os.path.normpath(token["original"])
        original_dir = os.path.dirname(original)
        if not os.path.isdir(original_dir) or os.path.islink(original_dir):
            raise FileNotFoundError(
                f"Original directory missing or invalid: {original_dir}"
            )
        if os.path.lexists(original):
            raise FileExistsError(
                f"Cannot restore file: destination path already exists: {original}"
            )

        companions = self._validate_companions(token)
        for companion in companions:
            if os.path.realpath(companion["current"]) != os.path.realpath(current + ".txt"):
                raise ValueError("Undo token companion current path is invalid")
            if os.path.realpath(companion["original"]) != os.path.realpath(original + ".txt"):
                raise ValueError("Undo token companion original path is invalid")
            self._validate_regular_path(companion["current"], label="Companion")
            _verify_provenance(companion["current"], companion["provenance"])
            if os.path.lexists(companion["original"]):
                raise FileExistsError(
                    "Cannot restore sidecar: destination path already exists: "
                    f"{companion['original']}"
                )

        restore_paths = [original] + [item["original"] for item in companions]
        reserved: list[str] = []
        try:
            for path in restore_paths:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
                os.close(fd)
                reserved.append(path)
        except Exception:
            for path in reserved:
                try:
                    os.remove(path)
                except OSError:
                    pass
            raise

        source_paths = [current] + [item["current"] for item in companions]
        temps = [
            self._temp_path(os.path.dirname(path), f"restore-{index}")
            for index, path in enumerate(restore_paths)
        ]
        artifacts = [self._artifact(path, "reservation") for path in restore_paths]
        artifacts.extend(self._artifact(path, "temp") for path in temps)
        try:
            self.journal.record_staged(
                self.operation_id, destination_path=original, artifacts=artifacts
            )
        except Exception:
            for path in restore_paths:
                if os.path.lexists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            raise

        try:
            for source_path, restore_path, temp_path in zip(
                source_paths, restore_paths, temps, strict=True
            ):
                _atomic_copy_stream(
                    source_path, restore_path, is_move=False, temp_path=temp_path
                )
        except Exception:
            for path in restore_paths:
                if os.path.lexists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            raise

        try:
            os.remove(current)
        except OSError:
            for path in restore_paths:
                if os.path.lexists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            raise

        warning: str | None = None
        for companion in companions:
            try:
                os.remove(companion["current"])
            except OSError as exc:
                warning = f"Image restored, but a source sidecar remains: {exc}"
        return original, warning

    def _remove_copy_set(self, current: str, token: dict[str, Any]) -> str | None:
        """Delete a validated copy while rolling back a companion rename on failure."""
        companions = self._validate_companions(token)
        backups: list[tuple[str, str]] = []
        for index, companion in enumerate(companions):
            if os.path.realpath(companion["current"]) != os.path.realpath(current + ".txt"):
                raise ValueError("Undo token companion current path is invalid")
            self._validate_regular_path(companion["current"], label="Companion")
            _verify_provenance(companion["current"], companion["provenance"])
            backup = self._temp_path(os.path.dirname(current), f"undo-sidecar-{index}")
            backups.append((companion["current"], backup))

        self.journal.record_staged(
            self.operation_id,
            destination_path=current,
            artifacts=[self._artifact(path, "temp") for _source, path in backups],
        )
        for source, backup in backups:
            os.replace(source, backup)
        try:
            os.remove(current)
        except Exception:
            for source, backup in reversed(backups):
                if os.path.lexists(backup):
                    os.replace(backup, source)
            raise

        warnings: list[str] = []
        for _source, backup in backups:
            try:
                os.remove(backup)
            except OSError as exc:
                warnings.append(str(exc))
        if warnings:
            return "Copy removed, but a temporary sidecar artifact remains: " + "; ".join(warnings)
        return None

    def _execute(self) -> None:
        """Execute one operation and emit a single truthful terminal result."""
        filepath = os.path.normpath(self.filepath)
        self.journal.record_intent(
            operation_id=self.operation_id,
            action=self.task_type,
            source_path=filepath,
            destination_path=self.dest_folder if isinstance(self.dest_folder, str) else None,
            task_options=self.task_options.to_dict(),
        )

        final_path: str | None = filepath
        undo_token: dict[str, Any] | None = None
        warnings: list[str] = []
        action_verb = "Processed"

        if self.task_type in ("move", "copy"):
            self._validate_regular_path(filepath, label="Source")
            if not self.dest_folder:
                raise ValueError("Destination folder required for move/copy operation")
            destination_folder = os.path.normpath(self.dest_folder)
            if (
                not os.path.isdir(destination_folder)
                or os.path.islink(destination_folder)
            ):
                raise FileNotFoundError(
                    f"Destination folder missing or invalid: {destination_folder}"
                )
            intended = os.path.join(destination_folder, os.path.basename(filepath))
            if os.path.realpath(filepath) == os.path.realpath(intended):
                raise ValueError(
                    f"Source and destination resolve to the same effective location: {filepath}"
                )

            final_path, transfer_warning = self._stage_transfer(
                filepath,
                destination_folder,
                is_move=self.task_type == "move",
            )
            if transfer_warning:
                warnings.append(transfer_warning)
            verb = "Moved" if self.task_type == "move" else "Copied"
            action_verb = (
                f"{verb} {os.path.basename(final_path)} to {destination_folder}"
            )

        elif self.task_type == "trash":
            self._validate_regular_path(filepath, label="Source")
            trash_folder = self._get_setting("directories", "trash")
            if (
                trash_folder
                and os.path.isdir(trash_folder)
                and not os.path.islink(trash_folder)
            ):
                trash_folder = os.path.normpath(trash_folder)
                intended = os.path.join(trash_folder, os.path.basename(filepath))
                if os.path.realpath(filepath) == os.path.realpath(intended):
                    raise ValueError(f"Source file is already in the trash folder: {filepath}")
                final_path, transfer_warning = self._stage_transfer(
                    filepath, trash_folder, is_move=True
                )
                if transfer_warning:
                    warnings.append(transfer_warning)
                action_verb = (
                    f"Moved {os.path.basename(final_path)} to Trash folder"
                )
            else:
                try:
                    send2trash(filepath)
                except TrashPermissionError as exc:
                    raise PermissionError(f"Trash permission denied: {exc}") from exc
                final_path = None
                action_verb = f"Trashed {os.path.basename(filepath)}"

        elif self.task_type in ("undo_move", "undo_trash"):
            token = validate_undo_token(
                self.undo_token, undo_action=self.task_type, current_path=filepath
            )
            self._validate_regular_path(filepath, label="Undo source")
            _verify_provenance(filepath, token["provenance"])
            if self.dest_folder and os.path.realpath(self.dest_folder) != os.path.realpath(
                token["original"]
            ):
                raise ValueError("Undo destination does not match the Undo token")
            final_path, restore_warning = self._restore_move_set(filepath, token)
            if restore_warning:
                warnings.append(restore_warning)
            action_verb = f"Restored {os.path.basename(final_path)}"

        elif self.task_type == "undo_copy":
            token = validate_undo_token(
                self.undo_token, undo_action=self.task_type, current_path=filepath
            )
            self._validate_regular_path(filepath, label="Undo source")
            _verify_provenance(filepath, token["provenance"])
            removal_warning = self._remove_copy_set(filepath, token)
            if removal_warning:
                warnings.append(removal_warning)
            final_path = filepath
            action_verb = f"Removed copied file {os.path.basename(filepath)}"

        else:
            raise ValueError(f"Unsupported file operation: {self.task_type}")

        if (
            self.task_type not in ("undo_move", "undo_trash", "undo_copy")
            and final_path
            and os.path.exists(final_path)
        ):
            ai_enabled = self._get_setting("ai_tagger", "enabled")
            if ai_enabled and self.ai_tagger:
                try:
                    threshold = float(
                        self._get_setting("ai_tagger", "threshold", 0.5)
                    )
                    tags = self.ai_tagger.get_tags(final_path, threshold=threshold)
                    if tags:
                        write_metadata(
                            final_path,
                            tags,
                            bool(self._get_setting("metadata", "write_exif", True)),
                            bool(self._get_setting("metadata", "write_sidecar", False)),
                        )
                        action_verb += f" and tagged with: {', '.join(tags)}"
                except Exception as exc:
                    logger.warning(f"Metadata write warning for {final_path}: {exc}")
                    warnings.append(f"Metadata tagging failed: {exc}")

        if self.task_type in ("move", "copy", "trash") and final_path:
            try:
                companion_tokens: list[dict[str, Any]] = []
                current_sidecar = final_path + ".txt"
                if os.path.exists(current_sidecar):
                    companion_tokens.append(
                        {
                            "original": filepath + ".txt",
                            "current": current_sidecar,
                            "provenance": _compute_provenance(current_sidecar),
                        }
                    )
                undo_token = create_undo_token(
                    token_id=str(uuid.uuid4()),
                    action=self.task_type,
                    original_path=filepath,
                    current_path=final_path,
                    timestamp=time.time(),
                    provenance=_compute_provenance(final_path),
                    companions=companion_tokens,
                )
            except Exception as exc:
                warnings.append(f"Operation completed, but Undo could not be created: {exc}")

        try:
            self.journal.record_committed(
                self.operation_id, destination_path=final_path, undo_token=undo_token
            )
        except Exception as exc:
            logger.error("Filesystem operation committed but journal commit failed", exc_info=True)
            warnings.append(f"Operation committed, but journal update failed: {exc}")

        state = (
            OperationState.COMPLETED_WITH_WARNING
            if warnings
            else OperationState.COMPLETED
        )
        warning_message = "; ".join(warnings) if warnings else None
        try:
            self.journal.record_terminal(
                operation_id=self.operation_id,
                state=state,
                destination_path=final_path,
                undo_token=undo_token,
                warning=warning_message,
            )
        except Exception as exc:
            logger.error("Terminal journal update failed", exc_info=True)
            warnings.append(f"Terminal journal update failed: {exc}")
            state = OperationState.COMPLETED_WITH_WARNING
            warning_message = "; ".join(warnings)

        self.signals.progress.emit(action_verb)
        self.signals.operation_result.emit(
            OperationResult(
                operation_id=self.operation_id,
                action=self.task_type,
                source_path=filepath,
                destination_path=final_path,
                state=state,
                undo_token=undo_token,
                error=None,
                warning=warning_message,
                view_generation=self.task_options.view_generation,
            ).to_dict()
        )

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
            self.journal.prune_journal()
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
        """Wait for current tasks with a bounded desktop-shutdown delay."""
        logger.info("Stopping QueueWorker, waiting for tasks to finish...")
        if self.thread_pool.waitForDone(5_000):
            logger.info("QueueWorker stopped.")
        else:
            logger.warning("QueueWorker still has tasks after five seconds")
