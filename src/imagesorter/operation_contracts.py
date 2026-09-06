from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION: int = 1


class OperationState:
    """Constants for operation terminal states."""
    COMPLETED: str = "completed"
    COMPLETED_WITH_WARNING: str = "completed_with_warning"
    FAILED: str = "failed"
    RECOVERY_REQUIRED: str = "recovery_required"


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
) -> dict[str, Any]:
    """Creates a versioned UndoToken dictionary."""
    return {
        "version": 1,
        "token_id": token_id,
        "action": action,
        "original": original_path,
        "current": current_path,
        "timestamp": timestamp,
        "provenance": provenance,
    }
