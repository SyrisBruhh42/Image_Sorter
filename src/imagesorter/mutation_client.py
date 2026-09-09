"""Qt event-loop client; journal I/O and mutation draining live in another process."""
from __future__ import annotations

import atexit
import os
import socket
import subprocess
import time
import weakref

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .paths import get_data_dir, get_logs_dir
from .worker_protocol import (
    MAX_MESSAGE_BYTES,
    decode,
    encode,
    service_address,
    verify_peer,
    worker_command,
    worker_environment,
)

_clients = weakref.WeakSet()

@atexit.register
def _disconnect_clients():
    for client in list(_clients):
        client.disconnect()

class MutationClient(QObject):
    MAX_PENDING = 1024
    result = pyqtSignal(dict)
    accepted = pyqtSignal(str)
    status = pyqtSignal(str)
    recovery = pyqtSignal(dict)

    def __init__(self, parent=None, *, journal_path=None):
        super().__init__(parent)
        self.journal_path = str(journal_path or get_data_dir() / "operation_journal.db")
        self.connection = None
        self.process = None
        self.pending = {}
        self._received = b""
        self._outgoing = b""
        self._ready = False
        self._closed = False
        self._last_spawn = 0.0
        self._startup_write = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(50)
        _clients.add(self)

    def submit(self, request):
        if self._closed:
            raise RuntimeError("Application is closing")
        if len(self.pending) >= self.MAX_PENDING and request["operation_id"] not in self.pending:
            raise OverflowError("File-operation backlog is full; wait for accepted operations to finish")
        self.pending[request["operation_id"]] = request
        if self._ready:
            self._outgoing += encode({"type": "submit", "request": request})

    def _connect(self):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.connect(service_address(self.journal_path))
            verify_peer(connection)
        except OSError:
            connection.close()
            if time.monotonic() - self._last_spawn > 3 and (self.process is None or self.process.poll() is not None):
                log = open(get_logs_dir() / "mutation-service.log", "ab")
                startup_read, startup_write = os.pipe()
                try:
                    self.process = subprocess.Popen(worker_command("mutation-service") + ["--journal", self.journal_path, "--startup-fd", str(startup_read)],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                        pass_fds=(startup_read,), env=worker_environment())
                    if self._startup_write is not None:
                        os.close(self._startup_write)
                    self._startup_write = startup_write
                except Exception:
                    os.close(startup_write)
                    raise
                finally:
                    os.close(startup_read)
                    log.close()
                self._last_spawn = time.monotonic()
            return
        connection.setblocking(False)
        self.connection = connection
        self._received = b""
        self._outgoing = encode({"type": "hello"})

    def poll(self):
        if self._closed:
            return
        if self.connection is None:
            self._connect()
            if self.connection is None:
                return
        try:
            if self._outgoing:
                try:
                    count = self.connection.send(self._outgoing)
                    self._outgoing = self._outgoing[count:]
                except BlockingIOError:
                    pass
            try:
                block = self.connection.recv(65536)
            except BlockingIOError:
                return
            if not block:
                raise ConnectionError("Mutation helper disconnected; pending IDs will be rechecked")
            self._received += block
            while b"\n" in self._received:
                line, self._received = self._received.split(b"\n", 1)
                message = decode(line)
                kind = message.get("type")
                if kind == "ready":
                    from .diagnostics import record
                    record("mutation_service_ready", pid=message.get("pid"), draining=message.get("draining", False))
                    self._ready = not message.get("draining", False)
                    self.recovery.emit(message)
                    if self._ready:
                        for request in self.pending.values():
                            self._outgoing += encode({"type": "submit", "request": request})
                elif kind == "accepted":
                    self.accepted.emit(message["operation_id"])
                elif kind in ("recovery", "recovery_reset", "recovery_complete"):
                    self.recovery.emit(message)
                elif kind in ("result", "history"):
                    result = message["result"]
                    if result["operation_id"] in self.pending:
                        self.pending.pop(result["operation_id"], None)
                        self.result.emit(result)
                        if result.get("action") == "recover" or result.get("state") == "recovery_required":
                            self.refresh_recovery()
                    elif kind == "history":
                        self.recovery.emit(message)
                elif kind == "error":
                    self.status.emit(message["error"])
                    operation_id = message.get("operation_id")
                    request = self.pending.pop(operation_id, None)
                    if request:
                        self.result.emit({"schema_version": 1, "operation_id": operation_id,
                                          "action": request["action"], "source_path": request["source_path"],
                                          "destination_path": None, "state": "failed", "undo_token": None,
                                          "error": message["error"], "warning": None,
                                          "view_generation": request.get("task_options", {}).get("view_generation")})
            if len(self._received) > MAX_MESSAGE_BYTES:
                raise ValueError("Oversized service reply")
        except (OSError, ValueError, KeyError) as exc:
            self.status.emit(str(exc))
            self.connection.close()
            self.connection = None
            self._ready = False

    def refresh_recovery(self):
        if self._ready and not self._closed:
            self._outgoing += encode({"type": "recovery_list"})

    def disconnect(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._timer.stop()
        except RuntimeError:
            pass  # QApplication may already have destroyed the Qt timer.
        if self._startup_write is not None:
            os.close(self._startup_write)
            self._startup_write = None
        if self.connection:
            self.connection.close()
            self.connection = None
        # The service owns durable IDs and drains on the last client disconnect.
        # Do not wait for or kill it here.
