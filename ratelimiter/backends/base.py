"""The backend contract: execute an algorithm's atomic step and store its state.

A backend knows nothing about *which* algorithm it is running. It is handed two
equivalent implementations of one atomic step -- a Python callable and a Lua
script -- and runs whichever one it understands, atomically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class Backend(ABC):
    @abstractmethod
    def execute(
        self,
        py_op: Callable[[dict, list, list], list],
        lua: str,
        keys: list,
        args: list,
    ) -> list:
        """Run one algorithm step atomically and return its result list.

        ``result[0]`` is 1 (admit) or 0 (reject). The in-memory backend calls
        ``py_op`` under a lock; the Redis backend evaluates ``lua`` server-side.
        """

    @abstractmethod
    def reset(self) -> None:
        """Wipe all stored state (used between benchmark/test runs)."""
