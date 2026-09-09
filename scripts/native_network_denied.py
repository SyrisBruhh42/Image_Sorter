"""Run native readiness inside an independently verified, empty network namespace.

This does not change host networking or firewall rules. The separate namespace
receipt proves denial; the usual readiness receipt still requires a clean source
identity and cannot become complete merely because networking was unavailable.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--parent-namespace", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    args, acceptance = parser.parse_known_args(argv)
    output = args.output.resolve()
    if not args.inside:
        output.mkdir(parents=True, mode=0o700)
        parent = os.readlink("/proc/self/ns/net")
        command = ["unshare", "--user", "--map-root-user", "--net", "--",
                   sys.executable, str(Path(__file__).resolve()), "--inside", "--parent-namespace", parent,
                   "--output", str(output), *acceptance]
        return subprocess.run(command, check=False).returncode
    current = os.readlink("/proc/self/ns/net")
    routes = Path("/proc/net/route").read_text()
    if current == args.parent_namespace or len(routes.splitlines()) > 1:
        raise RuntimeError("A separate namespace with no external routes was not established")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as canary:
        canary.settimeout(1)
        try:
            canary.connect(("192.0.2.1", 9))  # RFC 5737 documentation-only address.
        except OSError as exc:
            failure = exc.errno
        else:
            raise RuntimeError("Network-denial canary unexpectedly connected")
    if failure not in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
        raise RuntimeError(f"Denial was not established by the network namespace: errno {failure}")
    (output / "network-denial.json").write_text(json.dumps({"schema_version": 1,
        "parent_namespace": args.parent_namespace, "application_namespace": current,
        "routes": routes, "canary_errno": failure, "network_denied": True,
        "host_network_modified": False, "scope": "application and descendant network namespace"}, indent=2) + "\n")
    from native_acceptance import main as observe
    return observe(["--output", str(output / "observation"), *acceptance])


if __name__ == "__main__":
    raise SystemExit(main())
