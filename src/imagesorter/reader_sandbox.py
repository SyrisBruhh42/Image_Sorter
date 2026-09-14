"""Unprivileged Linux x86_64 leaf-reader write and IPC restrictions.

Apply only in a disposable single-threaded leaf, after snapshots and leases are
prepared and before importing parsers. The GUI, broker and mutator never enter
this domain. Kernel/driver bugs and malicious same-user host processes are not
within this containment claim. Other platforms retain base smoke coverage only.

https://docs.kernel.org/userspace-api/landlock.html
https://docs.kernel.org/userspace-api/seccomp_filter.html
"""
from __future__ import annotations

import ctypes
import errno
import os
import platform
import re
import stat
import sys
import tempfile
from pathlib import Path


class ReaderIsolationError(RuntimeError):
    pass


def landlock_abi() -> int:
    """Read-only compatibility probe; never restrict the calling process."""
    if not sys.platform.startswith("linux") or platform.machine().lower() not in {"x86_64", "amd64"}:
        return 0
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return max(0, libc.syscall(444, 0, 0, 1))


def validate_reader_isolation(response: dict, *, policy_version: int, minimum_abi: int) -> None:
    policy = response.get("reader_isolation")
    if (not isinstance(policy, dict) or policy.get("enforced") is not True or
            type(policy.get("policy_version")) is not int or policy["policy_version"] != policy_version or
            type(policy.get("landlock_abi")) is not int or policy["landlock_abi"] < minimum_abi or
            policy.get("network_and_mutation_ipc") != "denied" or
            policy.get("external_metadata_writes") != "denied"):
        raise ReaderIsolationError("Helper did not attest the required unprivileged reader policy")


def _seccomp(libc, *, gpu):
    class Instruction(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("instructions", ctypes.POINTER(Instruction))]
    allow, denied = 0x7fff0000, 0x00050000 | errno.EPERM
    code = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xc000003e), (0x06, 0, 0, 0x80000000),
            (0x20, 0, 0, 0), (0x35, 0, 1, 453), (0x06, 0, 0, 0x00050000 | errno.ENOSYS)]
    # prlimit64 may query/change this process, never another same-user owner.
    code += [(0x15, 0, 4, 302), (0x20, 0, 0, 16), (0x15, 2, 0, 0),
             (0x15, 1, 0, os.getpid()), (0x06, 0, 0, denied), (0x20, 0, 0, 0)]
    # Native thread pools are supported; process creation and cancellation-group
    # escapes are not. clone3 returns ENOSYS so libc falls back to checked clone.
    code += [(0x15, 0, 1, 435), (0x06, 0, 0, 0x00050000 | errno.ENOSYS)]
    code += [(0x15, 0, 4, 56), (0x20, 0, 0, 16), (0x45, 1, 0, 0x10000),
             (0x06, 0, 0, denied), (0x06, 0, 0, allow), (0x20, 0, 0, 0)]
    # FD flags/duplication are sufficient for the qualified readers. Async
    # signal owners, leases and locks can affect a different same-user process
    # even when the underlying file cannot be opened for writing.
    code += [(0x15, 0, 11, 72), (0x20, 0, 0, 24), (0x15, 0, 4, 4),
             (0x20, 0, 0, 32), (0x45, 0, 1, 0x2000), (0x06, 0, 0, denied),
             (0x06, 0, 0, allow), (0x35, 0, 2, 4), (0x15, 1, 0, 1030),
             (0x06, 0, 0, denied), (0x06, 0, 0, allow), (0x20, 0, 0, 0)]
    # Deny authority escapes and metadata writes not currently covered by
    # Landlock. New syscall numbers fail ENOSYS, including x32 ABI entrypoints.
    blocked = {29, 30, 31, 42, 43, 44, 45, 46, 47, 53, 57, 58, 62, 64, 65, 66, 67, 68, 69, 70, 71, 73,
               90, 91, 92, 93, 94, 101, 109, 112, 129, 132, 141, 142, 144, 155, 161, 165, 166,
               167, 168, 175, 176, 179, 188, 189, 190, 197, 198, 199, 200, 203, 220, 234, 235, 246,
               248, 249, 250, 251, 260, 261, 268, 272, 280, 288, 297, 298, 299, 304, 307, 308, 311, 313, 321,
               314, 323, 424, 425, 426, 427, 428, 429, 430, 431, 432, 433, 438, 442, 443, 452}
    if not gpu:
        blocked.add(16)  # No device or file ioctl is needed by CPU/codecs.
        blocked.add(41)
    for number in sorted(blocked):
        code += [(0x15, 0, 1, number), (0x06, 0, 0, denied)]
    if gpu:
        # CUDA creates a local sequence-packet listener. It does not need to
        # connect, accept or exchange messages for qualified single-GPU compute.
        # Keep those calls denied even against an unconfined connecting peer.
        code += [(0x15, 0, 11, 41), (0x20, 0, 0, 16), (0x15, 1, 0, 1),
                 (0x06, 0, 0, denied), (0x20, 0, 0, 24), (0x54, 0, 0, 0xfff7f7ff),
                 (0x15, 1, 0, 5), (0x06, 0, 0, denied), (0x20, 0, 0, 32),
                 (0x15, 1, 0, 0), (0x06, 0, 0, denied), (0x06, 0, 0, allow)]
        # GPU readers may issue NVIDIA RM/UVM requests only. All filesystem
        # mutation ioctls, terminal ioctls, and unknown device families fail.
        code += [(0x15, 0, 10, 16), (0x20, 0, 0, 24),
                 (0x35, 1, 0, 256), (0x06, 0, 0, allow),
                 (0x54, 0, 0, 0xff00), (0x15, 0, 1, 0x4600), (0x06, 0, 0, allow),
                 (0x20, 0, 0, 24), (0x54, 0, 0, 0xfffffff0),
                 (0x15, 1, 0, 0x30000000), (0x06, 0, 0, denied)]
    code.append((0x06, 0, 0, allow))
    instructions = (Instruction * len(code))(*(Instruction(*item) for item in code))
    program = Program(len(code), instructions)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:  # PR_SET_SECCOMP / FILTER
        raise ReaderIsolationError(f"Reader syscall restriction unavailable: errno {ctypes.get_errno()}")


def restrict_leaf_reader(scratch: Path, *, gpu: bool = False) -> dict:
    if not sys.platform.startswith("linux"):
        return {"enforced": False, "reason": "Platform has base smoke coverage, not Linux reader containment"}
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ReaderIsolationError("Reader containment is qualified only for Linux x86_64")
    import fcntl
    import resource
    scratch = Path(scratch)
    if not scratch.is_absolute() or scratch.is_symlink() or scratch.resolve() != scratch:
        raise ReaderIsolationError("Reader scratch must be an absolute non-symlink directory")
    info = scratch.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ReaderIsolationError("Reader scratch must be a private, user-owned directory")
    if set(os.listdir("/proc/self/task")) != {str(os.getpid())}:
        raise ReaderIsolationError("Reader containment must precede all native thread creation")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    file_limit = 600 * 1024 * 1024
    previous_soft, previous_hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    finite = [n for n in (file_limit, previous_soft, previous_hard) if n != resource.RLIM_INFINITY]
    resource.setrlimit(resource.RLIMIT_FSIZE, (min(finite), min(finite)))
    for name in ("HOME", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
        os.environ[name] = str(scratch)
    tempfile.tempdir = str(scratch)
    # Popen closes other inherited descriptors. Refuse unexpected writable or
    # socket/device authority nonetheless; Landlock cannot revoke already-open
    # write descriptors. Read-only regular inputs remain safe to inherit.
    for number in os.listdir("/proc/self/fd"):
        fd = int(number)
        if fd < 3:
            continue  # The broker owns and validates the three transport FDs.
        try:
            mode = os.fstat(fd).st_mode
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except OSError as exc:
            if exc.errno == errno.EBADF:  # The listing's own directory FD closed.
                continue
            raise
        if not stat.S_ISREG(mode) or flags & os.O_ACCMODE != os.O_RDONLY:
            raise ReaderIsolationError("Unexpected inherited reader descriptor authority")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 3:
        raise ReaderIsolationError("Linux Landlock ABI 3+ is required; no elevated-permission fallback is used")
    if gpu and abi < 6:
        raise ReaderIsolationError("Contained NVIDIA inference requires Landlock ABI 6+; CPU remains available")
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64),
                    ("scoped", ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    writes = (1 << 1) | sum(1 << bit for bit in range(4, 15))
    ioctl_device = (1 << 15) if gpu else 0
    attributes = Ruleset(writes | ioctl_device, 0, 1 if gpu else 0)
    ruleset = libc.syscall(444, ctypes.byref(attributes), ctypes.sizeof(attributes), 0)
    if ruleset < 0:
        raise ReaderIsolationError(f"Cannot create reader write policy: errno {ctypes.get_errno()}")
    def grant(path, access):
        fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
        try:
            rule = Beneath(access, fd)
            if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) != 0:
                raise ReaderIsolationError(f"Cannot restrict reader scratch: errno {ctypes.get_errno()}")
        finally:
            os.close(fd)
    try:
        grant(scratch, writes)
        grant(Path("/dev/null"), 1 << 1)
        devices = []
        if gpu:
            # CUDA names newly created own threads through procfs. No create,
            # delete or cross-directory references are granted, and proc magic
            # aliases still resolve to the external inode's denied hierarchy.
            grant(Path(f"/proc/{os.getpid()}/task"), (1 << 1) | (1 << 14))
            for path in Path("/dev").glob("nvidia*"):
                if re.fullmatch(r"nvidia(?:[0-9]+|ctl|-uvm|-uvm-tools)", path.name) and stat.S_ISCHR(path.lstat().st_mode):
                    grant(path, (1 << 1) | ioctl_device)
                    devices.append(str(path))
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, ruleset, 0) != 0:
            raise ReaderIsolationError(f"Cannot enforce reader write policy: errno {ctypes.get_errno()}")
    finally:
        os.close(ruleset)
    _seccomp(libc, gpu=gpu)
    return {"enforced": True, "policy_version": 1, "landlock_abi": abi,
            "scratch": str(scratch), "device_write_paths": devices,
            "network_and_mutation_ipc": "denied", "external_metadata_writes": "denied",
            "own_task_proc_writes": gpu}
