"""Adaptive rate limiter -- switches algorithm per client based on observed shape.

Each of the four base algorithms makes a fixed trade-off (see the benchmark
findings): a Sliding Window Counter is cheap and precise on smooth traffic; a
Token Bucket gracefully absorbs bursts. This limiter watches each client's
traffic with a :class:`~ratelimiter.traffic_detector.TrafficDetector` and routes
the client to whichever algorithm best fits its *current* behaviour, so you get
sliding-counter precision while a client is steady and token-bucket burst
tolerance while it is spiky -- without deciding in advance which one a client is.

Correctness note (the subtle part)
-----------------------------------
Switching algorithms naively is exploitable: a fresh algorithm starts with an
empty counter, so a mid-window switch would hand the client a *second* full
allowance in the same window (2x the limit). To prevent that, the active
algorithm is re-selected **only at window boundaries**. Within any single window
exactly one algorithm governs a client, so the "<= limit per window" invariant
each algorithm enforces is never reset mid-window. Hysteresis (``min_dwell_windows``)
further requires a new shape to persist before the limiter commits to a switch,
avoiding flapping.

Scope note
----------
The detector state lives in-process. When the underlying algorithms use the Redis
backend, *enforcement* remains atomic and correct across instances (each chosen
algorithm still runs its Lua script), but the *selection* is per-instance
advisory -- two instances observing different local slices of a client's traffic
may briefly pick different algorithms. Sharing the selection through Redis is a
natural extension; the sustained ``limit`` bounds long-run rate regardless.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .base import RateLimiter
from .fixed_window import FixedWindowCounter
from .sliding_window_counter import SlidingWindowCounter
from .sliding_window_log import SlidingWindowLog
from .token_bucket import TokenBucket
from .traffic_detector import TrafficDetector

_CANDIDATE_CLASSES = {
    "fixed_window": FixedWindowCounter,
    "sliding_window_log": SlidingWindowLog,
    "sliding_window_counter": SlidingWindowCounter,
    "token_bucket": TokenBucket,
}

#: Default "tolerant" policy: precise when steady, burst-tolerant when spiky.
#: Every key a detector can emit must map to a candidate algorithm.
DEFAULT_POLICY = {
    "warmup": "sliding_window_counter",
    "steady": "sliding_window_counter",
    "moderate": "sliding_window_counter",
    "ramping": "sliding_window_counter",
    "bursty": "token_bucket",
}

#: Alternative "strict" policy: clamp bursts to exactly the limit (pays the
#: log's O(limit) memory only while a client is actually bursty).
STRICT_POLICY = {**DEFAULT_POLICY, "bursty": "sliding_window_log"}


@dataclass
class _ClientState:
    detector: TrafficDetector
    active: str
    last_check_window: int
    pending: str | None = None
    pending_count: int = 0
    switches: int = 0
    last_shape: str = "warmup"


class AdaptiveRateLimiter(RateLimiter):
    name = "adaptive"

    def __init__(
        self,
        backend,
        limit: int,
        window: float,
        namespace: str = "rl",
        *,
        policy: dict | None = None,
        min_dwell_windows: int = 2,
        detector_kwargs: dict | None = None,
    ) -> None:
        super().__init__(backend, limit, window, namespace)
        self.policy = dict(DEFAULT_POLICY)
        if policy:
            self.policy.update(policy)
        self.min_dwell_windows = max(1, int(min_dwell_windows))
        self.detector_kwargs = dict(detector_kwargs or {})

        # One limiter instance per algorithm the policy can select, each with its
        # own storage namespace so their counters never collide.
        needed = set(self.policy.values())
        self._algos = {
            name: _CANDIDATE_CLASSES[name](
                backend, limit=limit, window=window,
                namespace=f"{namespace}:adaptive:{name}",
            )
            for name in needed
        }
        self._state: dict[str, _ClientState] = {}

    # ------------------------------------------------------------------ core
    def allow_request(self, client_id: str, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        w = math.floor(now / self.window)

        st = self._state.get(client_id)
        if st is None:
            st = _ClientState(
                detector=TrafficDetector(self.window, **self.detector_kwargs),
                active=self.policy["warmup"],
                last_check_window=w,
            )
            self._state[client_id] = st

        st.detector.observe(now)

        # Re-select only when a new window starts -> one algorithm per window.
        if w != st.last_check_window:
            st.last_check_window = w
            self._reselect(st, client_id, now)

        return self._algos[st.active].allow_request(client_id, now=now)

    def _reselect(self, st: _ClientState, client_id: str, now: float) -> None:
        shape = st.detector.classify()
        st.last_shape = shape
        desired = self.policy.get(shape, st.active)

        if desired == st.active:
            st.pending, st.pending_count = None, 0
            return

        # Hysteresis: the new shape must persist across min_dwell_windows checks.
        if st.pending == desired:
            st.pending_count += 1
        else:
            st.pending, st.pending_count = desired, 1

        if st.pending_count >= self.min_dwell_windows:
            st.active = desired
            st.switches += 1
            st.pending, st.pending_count = None, 0
            # Conservative hand-off: don't let the freshly activated algorithm
            # grant a burst on top of what the previous one already allowed in
            # the adjacent window (prevents ~2x in a boundary-straddling window).
            self._algos[desired].prime(client_id, now)

    # --------------------------------------------------------- observability
    def stats(self, client_id: str) -> dict | None:
        """Current selection for a client (or ``None`` if never seen)."""
        st = self._state.get(client_id)
        if st is None:
            return None
        return {
            "active": st.active,
            "last_shape": st.last_shape,
            "switches": st.switches,
        }

    def snapshot(self) -> dict:
        """Per-client selection state, for a status endpoint / debugging."""
        return {cid: self.stats(cid) for cid in self._state}

    # ------------------------------------------------ base ABC requirements
    # Adaptive overrides allow_request and delegates, so it never uses the
    # single-algorithm py_op/Lua machinery. These satisfy the ABC.
    def _build_args(self, now: float) -> list:  # pragma: no cover
        raise NotImplementedError("AdaptiveRateLimiter delegates to sub-algorithms")

    @staticmethod
    def _py_op(store: dict, keys: list, args: list) -> list:  # pragma: no cover
        raise NotImplementedError("AdaptiveRateLimiter delegates to sub-algorithms")
