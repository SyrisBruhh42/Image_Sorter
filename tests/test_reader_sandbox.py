"""Real child-process checks; never restrict the test runner or its writers."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux leaf-reader containment")


@pytest.mark.parametrize("gpu", [False, True])
def test_kernel_barrier_preserves_collection_bytes_attributes_and_journal_ipc(tmp_path, gpu):
    from imagesorter.reader_sandbox import landlock_abi
    actual_abi = landlock_abi()
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    collection = tmp_path / "image.jpg"
    collection.write_bytes(b"unrelated preserved bytes")
    collection.chmod(0o640)
    before = collection.stat()
    if actual_abi < (6 if gpu else 3):
        result = subprocess.run([sys.executable, "-c",
            ("import sys;from pathlib import Path;from imagesorter.reader_sandbox import restrict_leaf_reader;"
             "restrict_leaf_reader(Path(sys.argv[1]),gpu=sys.argv[2]=='True')"), str(scratch), str(gpu)],
            capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and "Landlock ABI" in result.stderr
        assert collection.read_bytes() == b"unrelated preserved bytes"
        # This proves fail-closed compatibility only, not GPU qualification.
        # The CPU parameter exercises the real CPU policy on ABI3–5 runners.
        return
    program = r'''
import ctypes, errno, fcntl, json, os, resource, socket, sys
from pathlib import Path
from imagesorter.reader_sandbox import restrict_leaf_reader
scratch, image = map(Path, sys.argv[1:3])
readonly = os.open(image, os.O_RDONLY)
policy = restrict_leaf_reader(scratch, gpu=sys.argv[3] == "True")
assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
assert resource.getrlimit(resource.RLIMIT_FSIZE)[1] <= 600 * 1024 * 1024
for command in (fcntl.F_SETOWN, fcntl.F_SETSIG, fcntl.F_SETLEASE, fcntl.F_SETLK, fcntl.F_SETLKW):
 try: fcntl.fcntl(0, command, 0)
 except OSError as exc: assert exc.errno == errno.EPERM
 else: raise AssertionError("Cross-process descriptor authority allowed")
try: fcntl.fcntl(0, fcntl.F_SETFL, os.O_ASYNC)
except OSError as exc: assert exc.errno == errno.EPERM
else: raise AssertionError("Async signal delivery allowed")
assert fcntl.fcntl(0, fcntl.F_GETFL) >= 0
denied = []
actions = {
 "write": lambda: image.write_bytes(b"destroyed"),
 "truncate": lambda: os.truncate(image, 0),
 "unlink": lambda: image.unlink(),
 "rename": lambda: image.rename(scratch / "stolen"),
 "link": lambda: os.link(image, scratch / "linked"),
 "chmod": lambda: image.chmod(0o777),
 "utime": lambda: os.utime(image, ns=(1, 1)),
 "xattr": lambda: os.setxattr(image, b"user.imagesorter-test", b"changed"),
 "unix-ipc": lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM),
 "network": lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM),
 "proc-root-alias": lambda: Path(f"/proc/self/task/{os.getpid()}/root{image}").write_bytes(b"destroyed"),
 "proc-fd-alias": lambda: os.open(f"/proc/self/task/{os.getpid()}/fd/{readonly}", os.O_WRONLY),
 "parent-proc-alias": lambda: Path(f"/proc/{os.getppid()}/task/{os.getppid()}/comm").write_text("interfered"),
}
for name, action in actions.items():
 try: action()
 except OSError as exc:
  if exc.errno not in {errno.EPERM, errno.EACCES} and not (name == "link" and exc.errno == errno.EXDEV):
   raise
  denied.append(name)
libc = ctypes.CDLL(None, use_errno=True)
for number in (31, 56, 57, 58, 66, 69, 71, 73, 109, 112, 141, 251):
 flags = 0 if number == 56 else -1
 assert libc.syscall(number, flags, 0, 0, 0) == -1 and ctypes.get_errno() == errno.EPERM
if sys.argv[3] == "True":
 Path(f"/proc/self/task/{os.getpid()}/comm").write_text("isolated-reader")
 endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
 endpoint.bind(chr(0) + "imagesorter-policy-" + str(os.getpid()))
 endpoint.listen(1)
 for number in (42, 43, 44, 45, 46, 47, 53, 288, 299, 307):
  assert libc.syscall(number, endpoint.fileno(), 0, 0, 0) == -1 and ctypes.get_errno() == errno.EPERM
 endpoint.close()
import threading
thread = threading.Thread(target=lambda: (scratch / "thread-result").write_text("inherited policy"))
thread.start()
thread.join()
(scratch / "output").write_bytes(b"bounded result")
assert image.read_bytes() == b"unrelated preserved bytes"
print(json.dumps({"denied": denied, "policy": policy}))
'''
    result = subprocess.run([sys.executable, "-c", program, str(scratch), str(collection), str(gpu)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["policy"]["enforced"] is True
    assert set(receipt["denied"]) == {"write", "truncate", "unlink", "rename", "link", "chmod", "utime", "xattr",
        "unix-ipc", "network", "proc-root-alias", "proc-fd-alias", "parent-proc-alias"}
    after = collection.stat()
    assert (before.st_ino, before.st_size, before.st_mode, before.st_mtime_ns, before.st_ctime_ns) == (
            after.st_ino, after.st_size, after.st_mode, after.st_mtime_ns, after.st_ctime_ns)
    assert (scratch / "output").read_bytes() == b"bounded result"


def test_non_private_or_symbolic_scratch_is_rejected_before_restriction(tmp_path):
    from imagesorter.reader_sandbox import ReaderIsolationError, restrict_leaf_reader
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o755)
    with pytest.raises(ReaderIsolationError, match="private"):
        restrict_leaf_reader(scratch)
    link = tmp_path / "alias"
    link.symlink_to(scratch, target_is_directory=True)
    with pytest.raises(ReaderIsolationError, match="non-symlink"):
        restrict_leaf_reader(link)
    # Invalid input must not accidentally confine the caller.
    (tmp_path / "still-ordinary-test-runner").write_text("unchanged authority")


@pytest.mark.parametrize("authority", ["thread", "writable-fd", "socket-fd"])
def test_existing_authority_is_rejected_in_disposable_child(tmp_path, authority):
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    program = r'''
import json, os, socket, sys, threading
from pathlib import Path
from imagesorter.reader_sandbox import ReaderIsolationError, restrict_leaf_reader
scratch, authority = Path(sys.argv[1]), sys.argv[2]
stop = threading.Event()
if authority == "thread":
 thread = threading.Thread(target=stop.wait)
 thread.start()
elif authority == "writable-fd":
 fd = os.open(scratch / "open-file", os.O_WRONLY | os.O_CREAT, 0o600)
else:
 sock = socket.socket(socket.AF_UNIX)
try:
 try: restrict_leaf_reader(scratch)
 except ReaderIsolationError as exc: print(json.dumps({"rejected": str(exc)}))
 else: raise AssertionError("unexpected pre-existing authority accepted")
finally:
 stop.set()
 if authority == "thread": thread.join()
'''
    result = subprocess.run([sys.executable, "-c", program, str(scratch), authority],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["rejected"]
