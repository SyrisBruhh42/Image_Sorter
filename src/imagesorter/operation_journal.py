from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from typing import Any

from .paths import get_data_dir


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args: object) -> None:
        try:
            super().__exit__(*args)
        finally:
            self.close()


class JournalState:
    """States for durable operation tracking."""

    INTENT: str = "INTENT"
    STAGED: str = "STAGED"
    COMMITTED: str = "COMMITTED"
    COMPLETED: str = "completed"
    COMPLETED_WITH_WARNING: str = "completed_with_warning"
    FAILED: str = "failed"
    RECOVERY_REQUIRED: str = "recovery_required"
    CANCELLED: str = "cancelled"


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
        if os.path.islink(self.db_path):
            raise ValueError("Operation journal cannot be a symbolic link")
        fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            observed = os.fstat(fd)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise ValueError("Operation journal must be a private regular file")
            if hasattr(os, "getuid") and observed.st_uid != os.getuid():
                raise PermissionError("Operation journal belongs to another user")
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self._init_db()
        # Keep WAL alive for this service lifetime. Otherwise closing the last
        # connection after every durable phase checkpoints/removes WAL and the
        # next phase pays journal setup again. Writers remain separate, locked,
        # FULL-synchronous transactions; no durability setting is relaxed.
        self._anchor = self._get_connection()

    def close(self) -> None:
        """Called by the owner thread after the mutation service has drained."""
        with self._lock:
            anchor = getattr(self, "_anchor", None)
            if anchor is not None:
                anchor.close()
                self._anchor = None

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=FULL;")
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
                "request_json": "TEXT",
                "manifest_json": "TEXT",
                "result_json": "TEXT",
                "undo_token_id": "TEXT",
                "undo_consumed_by": "TEXT",
                "parent_operation_id": "TEXT",
                "resolved_by": "TEXT",
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_undo_token ON operations(undo_token_id);")
            conn.execute("""CREATE TABLE IF NOT EXISTS receipts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL, result_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );""")
            for row in conn.execute("SELECT operation_id, undo_token_json FROM operations WHERE undo_token_id IS NULL AND undo_token_json IS NOT NULL;").fetchall():
                try:
                    token = json.loads(row["undo_token_json"])
                    if isinstance(token, dict) and isinstance(token.get("token_id"), str):
                        conn.execute("UPDATE operations SET undo_token_id=? WHERE operation_id=?", (token["token_id"], row["operation_id"]))
                except (TypeError, ValueError):
                    pass
            conn.commit()

    @staticmethod
    def normalize_request(request: dict[str, Any]) -> dict[str, Any]:
        """Copy JSON input, preserving every option in idempotency comparisons."""
        data = json.loads(json.dumps(request, allow_nan=False))
        if not isinstance(data, dict):
            raise ValueError("Operation request must be an object")
        for key in ("operation_id", "action", "source_path"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f"Operation {key} is required")
        if len(data["operation_id"]) > 128:
            raise ValueError("Operation identifier is too long")
        data["source_path"] = os.path.abspath(data["source_path"])
        destination = data.get("destination_path")
        if destination is not None:
            if not isinstance(destination, str) or not destination:
                raise ValueError("Destination path must be a nonempty string")
            data["destination_path"] = os.path.abspath(destination)
        else:
            data["destination_path"] = None
        data.setdefault("task_options", {})
        if not isinstance(data["task_options"], dict):
            raise ValueError("Task options must be an object")
        return data

    def accept(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Durable acknowledgement. Reused IDs can never change the request."""
        data = self.normalize_request(request)
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        now = time.time()
        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute("SELECT * FROM operations WHERE operation_id=?", (data["operation_id"],)).fetchone()
            if row:
                if row["request_json"] != encoded:
                    raise ValueError("Operation ID already exists with a different or legacy request")
                return self._row_to_dict(row), False
            conn.execute("""INSERT INTO operations
                (operation_id, action, source_path, destination_path, state,
                 task_options_json, created_at, updated_at, owner_token, owner_pid,
                 lease_expires_at, request_json, parent_operation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    data["operation_id"], data["action"], data["source_path"], data["destination_path"],
                    JournalState.INTENT, json.dumps(data["task_options"]), now, now,
                    self.owner_token, self.owner_pid, now + self.lease_seconds, encoded,
                    data.get("parent_operation_id")))
            row = conn.execute("SELECT * FROM operations WHERE operation_id=?", (data["operation_id"],)).fetchone()
            conn.commit()
            return self._row_to_dict(row), True

    def save_manifest(self, operation_id: str, manifest: dict[str, Any]) -> None:
        """Persist role-aware paths before each mutation and its resulting phase."""
        encoded = json.dumps(manifest, allow_nan=False)
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("""UPDATE operations SET manifest_json=?, state=?, updated_at=?
                WHERE operation_id=? AND result_json IS NULL""", (encoded, JournalState.STAGED, time.time(), operation_id))
            if cursor.rowcount != 1:
                raise ValueError("Operation is missing or already terminal")

    def finish_result(self, result: dict[str, Any], *,
                      parent_token: tuple[str, dict[str, Any]] | None = None,
                      consume_parent: str | None = None,
                      resolve_operation: str | None = None,
                      manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        """Atomically persist a result/receipt and authoritative Undo changes."""
        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute("SELECT * FROM operations WHERE operation_id=?", (result["operation_id"],)).fetchone()
            if row is None:
                raise ValueError("Cannot finish an unaccepted operation")
            if row["result_json"]:
                return json.loads(row["result_json"])
            if manifest is not None:
                if manifest.get("operation_id") != result["operation_id"]:
                    raise ValueError("Final manifest belongs to a different operation")
                conn.execute("UPDATE operations SET manifest_json=? WHERE operation_id=?", (
                    json.dumps(manifest, allow_nan=False), result["operation_id"]))
            if parent_token:
                parent_id, token = parent_token
                parent = conn.execute("SELECT * FROM operations WHERE operation_id=?", (parent_id,)).fetchone()
                if parent is None or parent["undo_consumed_by"]:
                    raise ValueError("Metadata parent is missing or already undone")
                previous = json.loads(parent["result_json"]) if parent["result_json"] else None
                if previous:
                    previous["undo_token"] = token
                conn.execute("UPDATE operations SET undo_token_json=?, undo_token_id=?, result_json=? WHERE operation_id=?", (
                    json.dumps(token), token["token_id"], json.dumps(previous) if previous else None, parent_id))
            if consume_parent:
                changed = conn.execute("UPDATE operations SET undo_consumed_by=? WHERE operation_id=? AND undo_consumed_by IS NULL", (result["operation_id"], consume_parent))
                if changed.rowcount != 1:
                    raise ValueError("Undo was already consumed")
            if resolve_operation:
                changed = conn.execute("UPDATE operations SET resolved_by=? WHERE operation_id=? AND state=? AND resolved_by IS NULL", (
                    result["operation_id"], resolve_operation, JournalState.RECOVERY_REQUIRED))
                if changed.rowcount != 1:
                    raise ValueError("Recovery target was already resolved or changed")
            token = result.get("undo_token")
            encoded = json.dumps(result, allow_nan=False)
            conn.execute("""UPDATE operations SET state=?, destination_path=?, result_json=?,
                undo_token_json=?, undo_token_id=?, error_message=?, warning_message=?,
                lease_expires_at=0, updated_at=? WHERE operation_id=?""", (
                    result["state"], result.get("destination_path"), encoded,
                    json.dumps(token) if token else None, token.get("token_id") if token else None,
                    result.get("error"), result.get("warning"), time.time(), result["operation_id"]))
            conn.execute("INSERT INTO receipts(operation_id,result_json,created_at) VALUES(?,?,?)", (result["operation_id"], encoded, time.time()))
            conn.commit()
            return result

    def authoritative_undo(self, token_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM operations WHERE undo_token_id=? AND action IN ('move','copy','trash')", (token_id,)).fetchone()
            if row is None:
                raise ValueError("Undo token is not recorded in this profile journal")
            entry = self._row_to_dict(row)
            if entry["undo_consumed_by"] or entry["state"] not in (JournalState.COMPLETED, JournalState.COMPLETED_WITH_WARNING):
                raise ValueError("Undo operation is not eligible or was already consumed")
            return entry, entry["undo_token"]

    def replay_results(self, after_sequence: int = 0, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Read a bounded cursor page for IPC; None retains legacy local use."""
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("Receipt cursor must be a nonnegative integer")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000):
            raise ValueError("Receipt page size must be an integer between 1 and 10000")
        with self._lock, self._get_connection() as conn:
            return [{"sequence": row["sequence"], "result": json.loads(row["result_json"])}
                    for row in conn.execute("SELECT * FROM receipts WHERE sequence>? ORDER BY sequence LIMIT ?",
                        (after_sequence, -1 if limit is None else limit)).fetchall()]

    def recovery_records(self, after_operation_id: str = "", *, limit: int = 128) -> list[dict[str, Any]]:
        """Page unresolved records, including legacy work without receipts."""
        if not isinstance(after_operation_id, str):
            raise ValueError("Recovery cursor must be an operation identifier string")
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("Recovery page size must be an integer between 1 and 10000")
        with self._lock, self._get_connection() as conn:
            rows = conn.execute("""SELECT * FROM operations WHERE state=? AND resolved_by IS NULL
                AND operation_id>? ORDER BY operation_id LIMIT ?""",
                (JournalState.RECOVERY_REQUIRED, after_operation_id, limit)).fetchall()
            return [self._row_to_dict(row) for row in rows]

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
                if entry.get("request") is not None:
                    # The role-aware engine never discards originals/rollback
                    # copies during startup. A transition lacking its receipt
                    # needs review, including a crash during system trash.
                    from .operation_contracts import OperationResult, OperationState
                    manifest = entry.get("manifest")
                    new_state = OperationState.RECOVERY_REQUIRED if manifest else OperationState.CANCELLED
                    message = ("Interrupted file transaction; recorded source claims and staging were preserved for recovery"
                               if manifest else "Accepted work was interrupted before any file transaction started")
                    result = OperationResult(operation_id=entry["operation_id"], action=entry["action"],
                        source_path=source, destination_path=destination, state=new_state,
                        error=message if manifest else None, warning=message,
                        view_generation=(entry.get("task_options") or {}).get("view_generation")).to_dict()
                    encoded = json.dumps(result)
                    conn.execute("UPDATE operations SET state=?,result_json=?,warning_message=?,updated_at=?,lease_expires_at=0 WHERE operation_id=?", (new_state, encoded, message, now, entry["operation_id"]))
                    conn.execute("INSERT INTO receipts(operation_id,result_json,created_at) VALUES(?,?,?)", (entry["operation_id"], encoded, now))
                    entry["reconciled_state"] = new_state
                    reconciled.append(entry)
                    continue
                source_exists = os.path.lexists(source)
                destination_exists = bool(
                    destination and os.path.lexists(destination)
                )
                if state in (JournalState.INTENT, JournalState.STAGED):
                    preserved_artifacts = any(
                        os.path.lexists(item.get("path", "") if isinstance(item, dict) else item)
                        for item in entry.get("artifacts") or [] if isinstance(item, (dict, str))
                    )
                    if source_exists and not destination_exists and not preserved_artifacts:
                        new_state = JournalState.FAILED
                        warning = "Interrupted before commit; original source is intact"
                    else:
                        new_state = JournalState.RECOVERY_REQUIRED
                        warning = (
                            "Interrupted before a durable commit; ambiguous files were "
                            "preserved for manual review"
                        )
                else:
                    # Legacy commits lack a complete, durable image/sidecar
                    # manifest. Existence or absence is not content/ownership
                    # proof and must never manufacture a successful receipt.
                    new_state = JournalState.RECOVERY_REQUIRED
                    warning = "Legacy commit lacks a verified file-set receipt; files preserved for review"

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
        # Expired clocks never establish that a live filesystem writer died.
        # The service's kernel ProfileLock is the ownership/takeover authority.
        return self._pid_is_running(entry.get("owner_pid"))

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
                WHERE state IN (?, ?, ?) AND updated_at < ?
                  AND manifest_json IS NULL AND request_json IS NULL;
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
                          AND manifest_json IS NULL AND request_json IS NULL
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
            ("request_json", "request", None),
            ("manifest_json", "manifest", None),
            ("result_json", "result", None),
        ):
            if data.get(source_key):
                try:
                    data[target_key] = json.loads(data[source_key])
                except (TypeError, ValueError):
                    data[target_key] = fallback
            else:
                data[target_key] = fallback
        return data
