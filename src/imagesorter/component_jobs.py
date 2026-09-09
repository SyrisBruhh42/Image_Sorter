"""Cancellable component-management subprocess entry point."""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "helper":
        from .component_worker import main as helper_main
        return helper_main(arguments[1:])
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "install", "import-model", "verify", "enable", "disable", "rollback", "remove", "recover"))
    parser.add_argument("component_id", nargs="?")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(arguments)
    from .component_manager import ComponentBusy, ComponentManager
    cancellation = [False]
    signal.signal(signal.SIGTERM, lambda *_: cancellation.__setitem__(0, True))
    try:
        manager = ComponentManager()
        if args.action == "install":
            manager.install(args.component_id, args.archive,
                progress=lambda value: print(json.dumps(value), flush=True), cancelled=lambda: cancellation[0])
        elif args.action == "import-model":
            if args.archive is None:
                raise ValueError("Select the existing model directory")
            manager.import_legacy_model(args.archive,
                progress=lambda value: print(json.dumps(value), flush=True), cancelled=lambda: cancellation[0])
        elif args.action in {"enable", "disable"}:
            manager.enable(args.component_id, args.action == "enable")
        elif args.action == "remove":
            while True:
                if cancellation[0]:
                    raise InterruptedError("Removal cancelled while waiting for active readers")
                try:
                    manager.remove(args.component_id)
                    break
                except ComponentBusy:
                    print(json.dumps({"state": "waiting_for_readers"}), flush=True)
                    time.sleep(.1)
        elif args.action in {"verify", "rollback"}:
            getattr(manager, args.action)(args.component_id)
        elif args.action == "recover":
            records = manager.recover_jobs()
            unresolved = [record for record in records if record["state"] == "recovery_required"]
            print(json.dumps({"state": "recovery_required" if unresolved else "completed", "recovery": records,
                              "error": (f"{len(unresolved)} installation(s) need review; retained material: " +
                                        "; ".join(str(record.get("staging", "see receipt")) for record in unresolved))
                                       if unresolved else None}), flush=True)
            if unresolved:
                return 3
        print(json.dumps({"state": "completed", "components": manager.status()}), flush=True)
        return 0
    except InterruptedError as exc:
        print(json.dumps({"state": "cancelled", "error": str(exc)}), flush=True)
        return 2
    except Exception as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
