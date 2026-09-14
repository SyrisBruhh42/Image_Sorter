from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

OPERATION_RESULT_VERSION: int = 1
UNDO_TOKEN_VERSION: int = 1
# Result, Undo, journal and IPC versions evolve independently.
SCHEMA_VERSION: int = OPERATION_RESULT_VERSION


class OperationState:
    """Constants for operation terminal states."""
    COMPLETED: str = "completed"
    COMPLETED_WITH_WARNING: str = "completed_with_warning"
    FAILED: str = "failed"
    RECOVERY_REQUIRED: str = "recovery_required"
    CANCELLED: str = "cancelled"


@dataclass(frozen=True)
class TaskOptions:
    """Options attached to a task at dispatch time."""
    view_generation: int | None = None
    settings_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Converts task options to a plain dictionary."""
        return {
            "view_generation": self.view_generation,
            "settings_snapshot": self.settings_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskOptions:
        """Constructs TaskOptions from a dictionary."""
        if not data:
            return cls()
        return cls(
            view_generation=data.get("view_generation"),
            settings_snapshot=data.get("settings_snapshot"),
        )


@dataclass(frozen=True)
class OperationResult:
    """
    Immutable terminal operation result conforming to SHARED OPERATION CONTRACT v1.
    """
    operation_id: str
    action: str
    source_path: str
    destination_path: str | None = None
    state: str = OperationState.COMPLETED
    undo_token: dict[str, Any] | None = None
    error: str | None = None
    warning: str | None = None
    view_generation: int | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Converts result to dictionary matching the schema contract."""
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "action": self.action,
            "source_path": self.source_path,
            "destination_path": self.destination_path,
            "state": self.state,
            "undo_token": self.undo_token,
            "error": self.error,
            "warning": self.warning,
            "view_generation": self.view_generation,
        }


def create_undo_token(
    token_id: str,
    action: str,
    original_path: str,
    current_path: str,
    timestamp: float,
    provenance: dict[str, Any],
    companions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Creates a versioned UndoToken dictionary."""
    token = {
        "version": UNDO_TOKEN_VERSION,
        "token_id": token_id,
        "action": action,
        "original": original_path,
        "current": current_path,
        "timestamp": timestamp,
        "provenance": provenance,
    }
    if companions:
        token["companions"] = companions
    return token


def validate_undo_token(
    token: dict[str, Any] | None,
    *,
    undo_action: str,
    current_path: str,
) -> dict[str, Any]:
    """Validate a destructive Undo request before any filesystem mutation."""
    if not isinstance(token, dict):
        raise ValueError("A complete Undo token is required")
    if token.get("version") != UNDO_TOKEN_VERSION:
        raise ValueError("Unsupported or missing Undo token version")
    if not isinstance(token.get("token_id"), str) or not token["token_id"]:
        raise ValueError("Undo token identifier is missing")
    expected_original_actions = {
        "undo_move": {"move", "trash"},
        "undo_trash": {"trash"},
        "undo_copy": {"copy"},
    }
    if token.get("action") not in expected_original_actions.get(undo_action, set()):
        raise ValueError("Undo token action does not match the requested operation")
    for key in ("original", "current"):
        if not isinstance(token.get(key), str) or not token[key]:
            raise ValueError(f"Undo token {key} path is missing")
    if os.path.realpath(token["current"]) != os.path.realpath(current_path):
        raise ValueError("Undo token current path does not match the requested file")
    provenance = token.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Undo token provenance is missing")
    if not isinstance(provenance.get("size"), int) or not isinstance(
        provenance.get("sha256"), str
    ):
        raise ValueError("Undo token provenance is incomplete")
    return token
