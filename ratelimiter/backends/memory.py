"""In-process, dict-backed store.

Correct for a single process only. Atomicity comes from a re-entrant lock held
across the whole read-decide-write step, mirroring what Redis gets from running
a Lua script uninterrupted.
"""

from __future__ import annotations

import threading
from typing import Callable

from .base import Backend


class MemoryBackend(Backend):
    def __init__(self) -> None:
        self.store: dict = {}
        self._lock = threading.Lock()

    def execute(
        self,
        py_op: Callable[[dict, list, list], list],
        lua: str,  # unused here; the Redis backend runs it instead
        keys: list,
        args: list,
    ) -> list:
        with self._lock:
            return py_op(self.store, keys, args)

    def reset(self) -> None:
        with self._lock:
            self.store.clear()

    # -- helpers used by the benchmark's memory-footprint measurement -----------
    def raw(self, key: str):
        """Return the stored value for ``key`` (or ``None``)."""
        return self.store.get(key)
