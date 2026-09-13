"""The shared contract every algorithm implements.

The whole point of the project is that the four algorithms are interchangeable.
That works because:

  * Every algorithm subclasses :class:`RateLimiter` and exposes exactly one
    public method, ``allow_request``.
  * The algorithm-specific "read the counter, decide, write it back" step is
    expressed *twice* per algorithm: once as a pure-Python callable (run
    atomically by the in-memory backend under a lock) and once as a Lua script
    (run atomically by Redis). A backend only has to know how to execute one of
    those atomically -- it never needs to understand the algorithm.

This separation is what lets you swap an in-memory backend for a Redis one to get
multi-instance correctness without touching a single line of algorithm logic.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable

from .backends.base import Backend

# A per-algorithm atomic step for the in-memory backend.
#
# Signature: (store, keys, args) -> list[float]
#   store : the backend's mutable dict, persisted across calls
#   keys  : namespaced storage keys (always length 1 here)
#   args  : numeric parameters, args[0] is always ``now`` (unix seconds)
# Returns a list whose first element is 1.0 (admit) or 0.0 (reject); any further
# elements are diagnostic (current count / remaining tokens / estimate).
PyOp = Callable[[dict, list, list], list]


class RateLimiter(ABC):
    """Abstract base for all four algorithms.

    Parameters
    ----------
    backend:
        A :class:`~ratelimiter.backends.base.Backend` handling atomic storage.
    limit:
        Requests permitted per ``window`` seconds (the sustained rate).
    window:
        Length of the limiting window in seconds.
    namespace:
        Key prefix, so several limiters can share one backend without clashing.
    """

    #: Lua source implementing the same logic as ``_py_op`` for the Redis backend.
    LUA: str = ""
    #: Short identifier used in storage keys and reports.
    name: str = "base"

    def __init__(
        self,
        backend: Backend,
        limit: int,
        window: float,
        namespace: str = "rl",
    ) -> None:
        self.backend = backend
        self.limit = limit
        self.window = float(window)
        self.namespace = namespace

    # ------------------------------------------------------------------ public
    def allow_request(self, client_id: str, now: float | None = None) -> bool:
        """Return ``True`` if the request is admitted, ``False`` if it hits the
        limit (the caller should reply HTTP 429).

        ``now`` is injectable purely so tests and the benchmark harness can drive
        the algorithms over simulated time. Production callers omit it and get
        the wall clock.
        """
        if now is None:
            now = time.time()
        key = f"{self.namespace}:{self.name}:{client_id}"
        args = self._build_args(now)
        result = self.backend.execute(self._py_op, self.LUA, [key], args)
        return bool(result[0])

    # -------------------------------------------------------------- subclass API
    @abstractmethod
    def _build_args(self, now: float) -> list:
        """Build the numeric ARGV list for this algorithm. ``args[0]`` must be
        ``now`` so both backends share a clock."""

    @staticmethod
    @abstractmethod
    def _py_op(store: dict, keys: list, args: list) -> list:
        """The in-memory atomic step. Must mirror :attr:`LUA` exactly."""

    # Convenience so ``str(limiter)`` reads well in benchmark output.
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}(limit={self.limit}, window={self.window})"
