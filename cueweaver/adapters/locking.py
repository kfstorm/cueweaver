"""Shared process and filesystem locking for durable Work-root stores."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported runtime is POSIX
    fcntl = None  # type: ignore[assignment]


class DurableFileLock:
    """Serialize threads and processes using one lock file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    @contextmanager
    def locked(self, directory: Path) -> Iterator[None]:
        with self._thread_lock:
            depth = getattr(self._local, "depth", 0)
            if depth:
                self._local.depth = depth + 1
                try:
                    yield
                finally:
                    self._local.depth = depth
                return
            directory.mkdir(parents=True, exist_ok=True)
            lock_file = self._path.open("a+")
            self._local.depth = 1
            try:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                self._local.depth = 0
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()


__all__ = ["DurableFileLock"]
