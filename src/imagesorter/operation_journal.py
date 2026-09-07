from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

from .logger import logger
from .paths import get_data_dir


class JournalState:
    """States for durable operation tracking."""

    INTENT: str = "INTENT"
    STAGED: str = "STAGED"
    COMMITTED: str = "COMMITTED"
    COMPLETED: str = "completed"
    COMPLETED_WITH_WARNING: str = "completed_with_warning"
    FAILED: str = "failed"
    RECOVERY_REQUIRED: str = "recovery_required"


class OperationJournal:
    """SQLite operation journal with owner leases and conservative recovery."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            data_dir = get_data_dir()
            os.makedirs(data_dir, exist_ok=True)
            db_path = str(data_dir / "operation_journal.db")
        self.db_path = db_path
        self.owner_token = uuid.uuid4().hex
        self.owner_pid = os.getpid()
        self.lease_seconds = 300.0
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        """Create the schema and migrate journals written by older releases."""
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    destination_path TEXT,
                    state TEXT NOT NULL,
                    undo_token_json TEXT,
                    task_options_json TEXT,
                    error_message TEXT,
                    warning_message TEXT,
                    artifacts_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    owner_token TEXT,
                    owner_pid INTEGER,
                    lease_expires_at REAL
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(operations);").fetchall()
            }
            for column, sql_type in {
                "owner_token": "TEXT",
                "owner_pid": "INTEGER",
                "lease_expires_at": "REAL",
            }.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE operations ADD COLUMN {column} {sql_type};"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_operations_state ON operations(state);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_operations_updated ON operations(updated_at);"
            )
            conn.commit()

    def record_intent(
        self,
        operation_id: str,
        action: str,
        source_path: str,
        destination_path: str | None = None,
        task_options: dict[str, Any] | None = None,
    ) -> None:
        """Record intent before any destination is reserved or mutated."""
        now = time.time()
        options_json = json.dumps(task_options) if task_options else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO operations (
                    operation_id, action, source_path, destination_path, state,
                    task_options_json, created_at, updated_at, owner_token,
                    owner_pid, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    action=excluded.action,
                    source_path=excluded.source_path,
                    destination_path=excluded.destination_path,
                    state=excluded.state,
                    task_options_json=excluded.task_options_json,
                    owner_token=excluded.owner_token,
                    owner_pid=excluded.owner_pid,
                    lease_expires_at=excluded.lease_expires_at,
                    updated_at=excluded.updated_at;
                """,
                (
                    operation_id,
                    action,
                    source_path,
                    destination_path,
                    JournalState.INTENT,
                    options_json,
                    now,
                    now,
                    self.owner_token,
                    self.owner_pid,
                    now + self.lease_seconds,
                ),
            )
            conn.commit()

    def record_staged(
        self,
        operation_id: str,
        destination_path: str | None = None,
        artifacts: list[dict[str, Any] | str] | None = None,
    ) -> None:
        """Record owned reservations and temporary artifacts before staging bytes."""
        now = time.time()
        artifacts_json = json.dumps(artifacts) if artifacts else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE operations
                SET state = ?, destination_path = COALESCE(?, destination_path),
                    artifacts_json = ?, updated_at = ?, lease_expires_at = ?
                WHERE operation_id = ?;
                """,
                (
                    JournalState.STAGED,
                    destination_path,
                    artifacts_json,
                    now,
                    now + self.lease_seconds,
                    operation_id,
                ),
            )
            conn.commit()

    def record_committed(
        self,
        operation_id: str,
        destination_path: str | None = None,
        undo_token: dict[str, Any] | None = None,
    ) -> None:
        """Record a completed primary disk mutation before UI notification."""
        now = time.time()
        undo_json = json.dumps(undo_token) if undo_token else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE operations
                SET state = ?, destination_path = COALESCE(?, destination_path),
                    undo_token_json = ?, updated_at = ?, lease_expires_at = ?
                WHERE operation_id = ?;
                """,
                (
                    JournalState.COMMITTED,
                    destination_path,
                    undo_json,
                    now,
                    now + self.lease_seconds,
                    operation_id,
                ),
            )
            conn.commit()

    def record_terminal(
        self,
        operation_id: str,
        state: str,
        destination_path: str | None = None,
        undo_token: dict[str, Any] | None = None,
        error: str | None = None,
        warning: str | None = None,
    ) -> None:
        """Record the public terminal state and release the operation lease."""
        now = time.time()
        undo_json = json.dumps(undo_token) if undo_token else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE operations
                SET state = ?,
                    destination_path = COALESCE(?, destination_path),
                    undo_token_json = COALESCE(?, undo_token_json),
                    error_message = ?, warning_message = ?, updated_at = ?,
                    lease_expires_at = 0
                WHERE operation_id = ?;
                """,
                (
                    state,
                    destination_path,
                    undo_json,
                    error,
                    warning,
                    now,
                    operation_id,
                ),
            )
            conn.commit()

    def get_entry(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?;", (operation_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_all_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM operations ORDER BY updated_at DESC LIMIT ?;", (limit,)
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def reconcile_interrupted_operations(
        self, *, force: bool = False
    ) -> list[dict[str, Any]]:
        """Settle abandoned records while preserving every ambiguous user file."""
        reconciled: list[dict[str, Any]] = []
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM operations
                WHERE state IN (?, ?, ?);
                """,
                (JournalState.INTENT, JournalState.STAGED, JournalState.COMMITTED),
            ).fetchall()

            for row in rows:
                entry = self._row_to_dict(row)
                now = time.time()
                if not force and self._lease_owner_is_live(entry, now):
                    continue

                state = entry["state"]
                source = entry["source_path"]
                destination = entry.get("destination_path")
                if state == JournalState.STAGED:
                    self._remove_owned_artifacts(entry)

                source_exists = os.path.lexists(source)
                destination_exists = bool(
                    destination and os.path.lexists(destination)
                )
                if state in (JournalState.INTENT, JournalState.STAGED):
                    if source_exists and not destination_exists:
                        new_state = JournalState.FAILED
                        warning = "Interrupted before commit; original source is intact"
                    else:
                        new_state = JournalState.RECOVERY_REQUIRED
                        warning = (
                            "Interrupted before a durable commit; ambiguous files were "
                            "preserved for manual review"
                        )
                else:
                    action = entry["action"]
                    deletion_committed = action == "undo_copy" and not source_exists
                    system_trash_committed = (
                        action == "trash"
                        and destination is None
                        and not source_exists
                    )
                    if destination_exists or deletion_committed or system_trash_committed:
                        new_state = JournalState.COMPLETED
                        warning = "Durable commit reconciled after restart"
                    else:
                        new_state = JournalState.RECOVERY_REQUIRED
                        warning = "Committed record does not match disk state; review required"

                conn.execute(
                    """
                    UPDATE operations
                    SET state = ?, warning_message = ?, updated_at = ?,
                        lease_expires_at = 0
                    WHERE operation_id = ?;
                    """,
                    (new_state, warning, now, entry["operation_id"]),
                )
                entry["reconciled_state"] = new_state
                reconciled.append(entry)

            conn.commit()
        return reconciled

    @staticmethod
    def _pid_is_running(pid: int | None) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _lease_owner_is_live(self, entry: dict[str, Any], now: float) -> bool:
        return bool(
            (entry.get("lease_expires_at") or 0) > now
            and self._pid_is_running(entry.get("owner_pid"))
        )

    @staticmethod
    def _remove_owned_artifacts(entry: dict[str, Any]) -> None:
        """Delete only exact artifacts whose journal record proves app ownership."""
        owner_token = entry.get("owner_token")
        operation_id = entry["operation_id"]
        for artifact in entry.get("artifacts") or []:
            if not isinstance(artifact, dict):
                continue  # A legacy path alone is not adequate ownership proof.
            path = artifact.get("path")
            kind = artifact.get("kind")
            if not path or artifact.get("owner_token") != owner_token:
                continue
            if not os.path.lexists(path) or os.path.islink(path):
                continue

            basename = os.path.basename(path)
            owned_temp = (
                kind == "temp"
                and basename.startswith(f".imagesorter-{operation_id}-")
                and basename.endswith(".tmp")
            )
            owned_reservation = (
                kind == "reservation"
                and os.path.isfile(path)
                and os.path.getsize(path) == 0
                and artifact.get("dev") == os.stat(path).st_dev
                and artifact.get("ino") == os.stat(path).st_ino
            )
            if not (owned_temp or owned_reservation):
                continue
            try:
                os.remove(path)
                logger.info(f"Reconciler removed owned {kind} artifact: {path}")
            except OSError as exc:
                logger.warning(f"Failed to clean owned artifact {path}: {exc}")

    def prune_journal(
        self, max_age_seconds: float = 86400 * 30, max_entries: int = 10000
    ) -> int:
        """Prune old terminal entries while retaining recovery-required records."""
        cutoff = time.time() - max_age_seconds
        terminal = (
            JournalState.COMPLETED,
            JournalState.COMPLETED_WITH_WARNING,
            JournalState.FAILED,
        )
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM operations
                WHERE state IN (?, ?, ?) AND updated_at < ?;
                """,
                (*terminal, cutoff),
            )
            deleted_count = cursor.rowcount
            total = conn.execute("SELECT COUNT(*) FROM operations;").fetchone()[0]
            if total > max_entries:
                excess = total - max_entries
                cursor = conn.execute(
                    """
                    DELETE FROM operations WHERE operation_id IN (
                        SELECT operation_id FROM operations
                        WHERE state IN (?, ?, ?)
                        ORDER BY updated_at ASC LIMIT ?
                    );
                    """,
                    (*terminal, excess),
                )
                deleted_count += cursor.rowcount
            conn.commit()
            return deleted_count

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for source_key, target_key, fallback in (
            ("undo_token_json", "undo_token", None),
            ("task_options_json", "task_options", None),
            ("artifacts_json", "artifacts", []),
        ):
            if data.get(source_key):
                try:
                    data[target_key] = json.loads(data[source_key])
                except (TypeError, ValueError):
                    data[target_key] = fallback
            else:
                data[target_key] = fallback
        return data
