"""Cross-platform advisory lock; never remove a lock file another process may use."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if path.stat().st_size == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError(
                    "Another installer is running. Close its window first."
                ) from None
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError(
                    "Another installer is running. Close its window first."
                ) from None
            try:
                yield
            finally:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
