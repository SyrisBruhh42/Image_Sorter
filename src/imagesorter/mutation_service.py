"""Independent journal owner: never killed to make a GUI shutdown timer pass."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import select
import selectors
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from .platform_capabilities import require_mutation_support
from .worker_protocol import (
    MAX_MESSAGE_BYTES,
    decode,
    encode,
    service_address,
    verify_peer,
)


def runtime_inventory(source: Path, executable_name: str, internal: Path) -> dict:
    """Hash only the packaged runtime, never a neighboring portable user profile."""
    relative_internal = internal.resolve().relative_to(source.resolve())
    if not relative_internal.parts:
        raise ValueError("Only an isolated onedir runtime can be pinned for safe drain")
    top_names = {executable_name, relative_internal.parts[0]}
    files = {}
    paths = []
    for name in sorted(top_names):
        path = source / name
        paths.append(path)
        if path.is_dir() and not path.is_symlink():
            paths.extend(path.rglob("*"))
    for path in sorted(paths):
        relative = path.relative_to(source)
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(source) or resolved.relative_to(source).parts[0] not in top_names:
                raise ValueError("Packaged runtime link escapes its immutable runtime")
            files[str(relative)] = {"link": os.readlink(path)}
        elif path.is_dir():
            continue
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            files[str(relative)] = {"sha256": digest.hexdigest()}
        else:
            raise ValueError("Packaged runtime contains a non-regular file")
    return {"files": files, "top_names": sorted(top_names)}


def _pin_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    from .paths import get_data_dir
    from .profile_lock import ProfileLock
    source = Path(sys.executable).resolve().parent
    root = get_data_dir() / "runtime"
    if root.is_symlink() or not root.resolve().is_relative_to(get_data_dir().resolve()):
        raise ValueError("Pinned runtime path escapes the application profile")
    root.mkdir(parents=True, exist_ok=True)
    if source.is_relative_to(root.resolve()):
        return
    inventory = runtime_inventory(source, Path(sys.executable).name, Path(sys._MEIPASS))
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    target = root / digest
    if target.is_symlink():
        raise ValueError("Pinned runtime must not be a symbolic link")
    with ProfileLock(str(root / "copy.lock")):
        if (target / ".complete").exists():
            expected = json.loads((target / ".complete").read_text())
            actual = runtime_inventory(target, Path(sys.executable).name,
                                       target / Path(sys._MEIPASS).relative_to(source))
            if expected != inventory or actual != inventory:
                raise ValueError("Pinned runtime changed; preserved it for inspection")
        else:
            staging = Path(tempfile.mkdtemp(prefix="runtime-", dir=root))
            try:
                shutil.copytree(source, staging / "app", symlinks=True,
                                ignore=lambda directory, names: [name for name in names if name not in inventory["top_names"]] if Path(directory) == source else [])
                copied = runtime_inventory(staging / "app", Path(sys.executable).name,
                                           staging / "app" / Path(sys._MEIPASS).relative_to(source))
                if copied != inventory:
                    raise ValueError("Packaged runtime changed during pinning")
                (staging / "app" / ".complete").write_text(json.dumps(inventory, sort_keys=True))
                os.rename(staging / "app", target)
            finally:
                shutil.rmtree(staging)
    executable = target / Path(sys.executable).name
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    # The persistent runtime must bootstrap its own bundled library directory.
    environment.pop("LD_LIBRARY_PATH", None)
    environment.pop("LD_LIBRARY_PATH_ORIG", None)
    os.execve(executable, [str(executable), "--mutation-service", *sys.argv[sys.argv.index("--mutation-service") + 1:]], environment)

def main(args=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", required=True)
    parser.add_argument("--startup-fd", type=int)
    options = parser.parse_args(args)
    require_mutation_support()
    _pin_runtime()
    from .operation_engine import OperationEngine
    from .operation_journal import OperationJournal
    from .profile_lock import ProfileBusyError, ProfileLock
    lock = ProfileLock(options.journal + ".lock")
    try:
        lock.acquire(blocking=False)
    except ProfileBusyError:
        return 73
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    selector = selectors.DefaultSelector()
    clients = {}
    tasks = queue.Queue(maxsize=1024)
    completed = queue.Queue()
    draining = threading.Event()
    active = threading.Event()
    started = time.monotonic()
    had_client = False
    journal = None
    worker = None
    try:
        journal = OperationJournal(options.journal)
        journal.reconcile_interrupted_operations(force=True)
        engine = OperationEngine(journal)
        address = service_address(options.journal)
        if not address.startswith("\0") and os.path.exists(address):
            os.unlink(address)
        listener.bind(address)
        listener.listen(8)
        listener.setblocking(False)
        selector.register(listener, selectors.EVENT_READ)

        def execute_tasks():
            while True:
                request = tasks.get()
                if request is None:
                    return
                active.set()
                try:
                    result = engine.cancel_pending(request["operation_id"]) if draining.is_set() else engine.execute(request, cancelled=draining.is_set)
                except Exception as exc:
                    result = {"operation_id": request["operation_id"], "action": request["action"],
                              "source_path": request["source_path"], "state": "recovery_required", "error": str(exc)}
                completed.put(result)
                active.clear()
                tasks.task_done()
        worker = threading.Thread(target=execute_tasks, name="durable-mutations")
        worker.start()

        def send(connection, message):
            state = clients.get(connection)
            if state is not None:
                state["out"] += encode(message)
                if len(state["out"]) > 4 * MAX_MESSAGE_BYTES:
                    drop(connection)

        def drop(connection):
            selector.unregister(connection)
            clients.pop(connection, None)
            connection.close()

        while True:
            for key, _events in selector.select(timeout=0.05):
                if key.fileobj is listener:
                    connection, _ = listener.accept()
                    verify_peer(connection)
                    connection.setblocking(False)
                    clients[connection] = {"in": b"", "out": b"", "replay_after": None,
                                           "recovery_after": None}
                    selector.register(connection, selectors.EVENT_READ)
                    had_client = True
                    continue
                connection = key.fileobj
                message = {}
                try:
                    block = connection.recv(65536)
                    if not block:
                        drop(connection)
                        continue
                    state = clients[connection]
                    state["in"] += block
                    if len(state["in"]) > MAX_MESSAGE_BYTES:
                        raise ValueError("Oversized client message")
                    while b"\n" in state["in"]:
                        body, state["in"] = state["in"].split(b"\n", 1)
                        message = decode(body)
                        if message["type"] == "hello":
                            send(connection, {"type": "ready", "pid": os.getpid(), "draining": draining.is_set()})
                            cursor = message.get("after_sequence", 0)
                            if type(cursor) is not int or cursor < 0:
                                raise ValueError("Invalid replay cursor")
                            state["replay_after"] = cursor
                            state["recovery_after"] = ""
                            send(connection, {"type": "recovery_reset"})
                        elif message["type"] == "recovery_list":
                            state["recovery_after"] = ""
                            send(connection, {"type": "recovery_reset"})
                        elif message["type"] == "submit":
                            request = message["request"]
                            if draining.is_set():
                                raise RuntimeError("Mutation service is draining; retry after its owner exits")
                            if tasks.full():
                                raise RuntimeError("Mutation backlog is full; retry after accepted work finishes")
                            entry, created = journal.accept(request)
                            send(connection, {"type": "accepted", "operation_id": request["operation_id"]})
                            if created:
                                tasks.put(request)
                            elif entry.get("result"):
                                send(connection, {"type": "result", "result": entry["result"]})
                        elif message["type"] == "close":
                            send(connection, {"type": "close_ack", "pending": tasks.unfinished_tasks, "pid": os.getpid()})
                        else:
                            raise ValueError("Unknown service request")
                except (OSError, ValueError, KeyError, RuntimeError) as exc:
                    if connection in clients:
                        send(connection, {"type": "error", "error": str(exc),
                                          "operation_id": message.get("request", {}).get("operation_id")})
            while not completed.empty():
                result = completed.get_nowait()
                for connection in list(clients):
                    send(connection, {"type": "result", "result": result})
            for connection, state in list(clients.items()):
                if state["recovery_after"] is not None and len(state["out"]) < 65536:
                    records = journal.recovery_records(state["recovery_after"], limit=128)
                    processed = 0
                    for entry in records:
                        if len(state["out"]) >= MAX_MESSAGE_BYTES:
                            break
                        send(connection, {"type": "recovery", "recovery": [entry]})
                        state["recovery_after"] = entry["operation_id"]
                        processed += 1
                    if processed == len(records) and len(records) < 128:
                        send(connection, {"type": "recovery_complete"})
                        state["recovery_after"] = None
                if state["replay_after"] is not None and len(state["out"]) < 65536:
                    rows = journal.replay_results(state["replay_after"], limit=128)
                    processed = 0
                    for receipt in rows:
                        if len(state["out"]) >= MAX_MESSAGE_BYTES:
                            break
                        result = dict(receipt["result"])
                        if token := result.get("undo_token"):
                            try:
                                _entry, result["undo_token"] = journal.authoritative_undo(token["token_id"])
                            except (ValueError, KeyError):
                                result["undo_token"] = None
                        send(connection, {"type": "history", **receipt, "result": result})
                        state["replay_after"] = receipt["sequence"]
                        processed += 1
                    if processed == len(rows) and len(rows) < 128:
                        send(connection, {"type": "history_complete", "sequence": state["replay_after"]})
                        state["replay_after"] = None
                if state["out"]:
                    try:
                        count = connection.send(state["out"])
                        state["out"] = state["out"][count:]
                    except BlockingIOError:
                        pass
                    except OSError:
                        drop(connection)
            abandoned = (options.startup_fd is not None and
                         bool(select.select([options.startup_fd], [], [], 0)[0]))
            if not clients and (had_client or abandoned or time.monotonic() - started > 15):
                draining.set()
                if not active.is_set() and not tasks.unfinished_tasks:
                    break
        tasks.put(None)
        worker.join()
        return 0
    finally:
        for connection in clients:
            connection.close()
        selector.close()
        listener.close()
        draining.set()
        if worker is not None and worker.is_alive():
            tasks.put(None)
            worker.join()
        if journal is not None:
            journal.close()
        if options.startup_fd is not None:
            os.close(options.startup_fd)
        lock.release()
