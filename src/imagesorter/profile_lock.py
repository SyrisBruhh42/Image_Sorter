"""Kernel-held profile ownership; clock/PID leases never authorize takeover."""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


class ProfileBusyError(RuntimeError):
    """Another service owns this profile's mutation and recovery."""


class ProfileLock:
    """Hold throughout service drain; never unlink the stable lock inode."""

    def __init__(self, path: str | Path) -> None:
        self.path = os.fspath(path)
        self._fd: int | None = None

    def acquire(self, blocking: bool = False) -> ProfileLock:
        if self._fd is not None:
            return self
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ValueError("Profile lock must be a private regular file")
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                raise PermissionError("Profile lock belongs to another user")
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            if os.name == "nt":
                import msvcrt
                if st.st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EDEADLK):
                raise ProfileBusyError("Image Sorter file work is still running for this profile") from exc
            raise
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)

    def __enter__(self) -> ProfileLock:  # noqa: PYI034 - Self is unavailable on Python 3.10.
        return self.acquire()

    def __exit__(self, *_args: object) -> None:
        self.release()
