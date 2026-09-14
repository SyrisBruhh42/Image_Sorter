"""Explicit platform boundary for the durable mutation service."""
from __future__ import annotations

import socket
import sys


def mutation_unavailable_reason() -> str | None:
    # Windows needs a separately qualified locking, IPC and directory-durability
    # implementation. A socket shim or omitted directory sync would not supply it.
    if sys.platform.startswith('win'):
        return ('Durable sorting, Undo and metadata writes are unavailable on Windows. '
                'Image review and settings remain available; use a supported Linux environment for file operations.')
    if not hasattr(socket, 'AF_UNIX'):
        return 'Durable file operations require local Unix sockets, which are unavailable on this platform.'
    return None


def require_mutation_support() -> None:
    if reason := mutation_unavailable_reason():
        raise RuntimeError(reason)
