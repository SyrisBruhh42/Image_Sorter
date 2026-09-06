from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

from .logger import logger
from .paths import get_data_dir


class JournalState:
    """States for journaled operation tracking."""
    INTENT: str = "INTENT"
    STAGED: str = "STAGED"
    COMMITTED: str = "COMMITTED"
    COMPLETED: str = "completed"
    COMPLETED_WITH_WARNING: str = "completed_with_warning"
    FAILED: str = "failed"
    RECOVERY_REQUIRED: str = "recovery_required"


class OperationJournal:
    """
    Durable SQLite operation journal with explicit intent, staged, committed, and terminal states.
    Maintains crash recovery records in the app's XDG data directory.
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            data_dir = get_data_dir()
            os.makedirs(data_dir, exist_ok=True)
            db_path = str(data_dir / "operation_journal.db")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Opens a connection to the SQLite database with WAL enabled."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        """Initializes database schema and indexes."""
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
                    updated_at REAL NOT NULL
                );
                """
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
        """Records initial operation intent before executing disk mutations."""
        now = time.time()
        options_json = json.dumps(task_options) if task_options else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO operations (
                    operation_id, action, source_path, destination_path, state,
                    task_options_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    action=excluded.action,
                    source_path=excluded.source_path,
                    destination_path=excluded.destination_path,
                    state=excluded.state,
                    task_options_json=excluded.task_options_json,
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
                ),
            )
            conn.commit()

    def record_staged(
        self,
        operation_id: str,
        destination_path: str | None = None,
        artifacts: list[str] | None = None,
    ) -> None:
        """Records that temporary or candidate files have been staged."""
        now = time.time()
        artifacts_json = json.dumps(artifacts) if artifacts else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE operations
                SET state = ?, destination_path = COALESCE(?, destination_path),
                    artifacts_json = ?, updated_at = ?
                WHERE operation_id = ?;
                """,
                (
                    JournalState.STAGED,
                    destination_path,
                    artifacts_json,
                    now,
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
        """Records that the primary filesystem mutation has been atomically committed."""
        now = time.time()
        undo_json = json.dumps(undo_token) if undo_token else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE operations
                SET state = ?, destination_path = COALESCE(?, destination_path),
                    undo_token_json = ?, updated_at = ?
                WHERE operation_id = ?;
                """,
                (
                    JournalState.COMMITTED,
                    destination_path,
                    undo_json,
                    now,
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
        """Records terminal state (completed, completed_with_warning, failed, recovery_required)."""
        now = time.time()
        undo_json = json.dumps(undo_token) if undo_token else None
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE operations
                SET state = ?,
                    destination_path = COALESCE(?, destination_path),
                    undo_token_json = COALESCE(?, undo_token_json),
                    error_message = ?,
                    warning_message = ?,
                    updated_at = ?
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
        """Retrieves a single operation entry by ID."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?;", (operation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def get_all_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieves recent operation entries."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM operations ORDER BY updated_at DESC LIMIT ?;", (limit,)
            )
            return [self._row_to_dict(r) for r in cursor.fetchall()]

    def reconcile_interrupted_operations(self) -> list[dict[str, Any]]:
        """
        Reconciles operations left in non-terminal states (INTENT, STAGED, COMMITTED)
        upon restart without deleting ambiguous user files.
        """
        reconciled: list[dict[str, Any]] = []
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM operations
                WHERE state IN (?, ?, ?);
                """,
                (JournalState.INTENT, JournalState.STAGED, JournalState.COMMITTED),
            )
            interrupted = cursor.fetchall()

            for row in interrupted:
                op_dict = self._row_to_dict(row)
                op_id = op_dict["operation_id"]
                state = op_dict["state"]
                src = op_dict["source_path"]
                dst = op_dict["destination_path"]
                artifacts = op_dict.get("artifacts") or []

                now = time.time()

                if state == JournalState.INTENT:
                    # Intent only - if target does not exist and source exists, task never started. Mark failed.
                    if dst and os.path.exists(dst) and not os.path.exists(src):
                        new_state = JournalState.COMMITTED
                    else:
                        new_state = JournalState.FAILED
                    conn.execute(
                        "UPDATE operations SET state = ?, warning_message = ?, updated_at = ? WHERE operation_id = ?;",
                        (new_state, "Reconciled after process restart", now, op_id),
                    )

                elif state == JournalState.STAGED:
                    # Clean up temporary staged files safely without deleting user source or target
                    for art in artifacts:
                        if art and os.path.exists(art) and art != src and art != dst and ".tmp" in os.path.basename(art):
                            try:
                                os.remove(art)
                                logger.info(f"Reconciler removed orphaned temp artifact: {art}")
                            except OSError as e:
                                logger.warning(f"Failed to clean temp artifact {art}: {e}")

                    # If destination exists and source removed, file was committed
                    if dst and os.path.exists(dst) and not os.path.exists(src):
                        new_state = JournalState.COMPLETED
                    elif src and os.path.exists(src):
                        new_state = JournalState.FAILED
                    else:
                        new_state = JournalState.RECOVERY_REQUIRED

                    conn.execute(
                        "UPDATE operations SET state = ?, warning_message = ?, updated_at = ? WHERE operation_id = ?;",
                        (new_state, "Reconciled staged task after restart", now, op_id),
                    )

                elif state == JournalState.COMMITTED:
                    # Mutation succeeded on disk before crash. If destination exists, mark completed.
                    if dst and os.path.exists(dst):
                        new_state = JournalState.COMPLETED
                    else:
                        new_state = JournalState.RECOVERY_REQUIRED

                    conn.execute(
                        "UPDATE operations SET state = ?, warning_message = ?, updated_at = ? WHERE operation_id = ?;",
                        (new_state, "Reconciled committed task after restart", now, op_id),
                    )

                op_dict["reconciled_state"] = new_state
                reconciled.append(op_dict)

            conn.commit()

        return reconciled

    def prune_journal(
        self, max_age_seconds: float = 86400 * 30, max_entries: int = 10000
    ) -> int:
        """
        Prunes old terminal entries beyond retention bounds.
        """
        cutoff = time.time() - max_age_seconds
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM operations
                WHERE state IN (?, ?, ?) AND updated_at < ?;
                """,
                (
                    JournalState.COMPLETED,
                    JournalState.COMPLETED_WITH_WARNING,
                    JournalState.FAILED,
                    cutoff,
                ),
            )
            deleted_count = cursor.rowcount

            # Count total entries
            cursor = conn.execute("SELECT COUNT(*) FROM operations;")
            total = cursor.fetchone()[0]
            if total > max_entries:
                excess = total - max_entries
                conn.execute(
                    """
                    DELETE FROM operations WHERE operation_id IN (
                        SELECT operation_id FROM operations
                        WHERE state IN (?, ?, ?)
                        ORDER BY updated_at ASC LIMIT ?
                    );
                    """,
                    (
                        JournalState.COMPLETED,
                        JournalState.COMPLETED_WITH_WARNING,
                        JournalState.FAILED,
                        excess,
                    ),
                )
                deleted_count += excess

            conn.commit()
            return deleted_count

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Converts sqlite3.Row to a dictionary."""
        d = dict(row)
        if d.get("undo_token_json"):
            try:
                d["undo_token"] = json.loads(d["undo_token_json"])
            except Exception:
                d["undo_token"] = None
        else:
            d["undo_token"] = None

        if d.get("task_options_json"):
            try:
                d["task_options"] = json.loads(d["task_options_json"])
            except Exception:
                d["task_options"] = None
        else:
            d["task_options"] = None

        if d.get("artifacts_json"):
            try:
                d["artifacts"] = json.loads(d["artifacts_json"])
            except Exception:
                d["artifacts"] = []
        else:
            d["artifacts"] = []

        return d
