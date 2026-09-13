"""ratelimiter -- four interchangeable rate-limiting algorithms.

Public API::

    from ratelimiter import build_limiter
    limiter = build_limiter("fixed_window", "memory", limit=10, window=60)
    limiter.allow_request("client-42")  # -> True / False

Algorithms and backends can also be imported directly and composed by hand.

More algorithms are registered in ``ALGORITHMS`` as they are implemented across
the build phases (fixed window, sliding window log, sliding window counter,
token bucket).
"""

from __future__ import annotations

from .backends import Backend, MemoryBackend
from .base import RateLimiter
from .fixed_window import FixedWindowCounter
from .sliding_window_log import SlidingWindowLog
from .sliding_window_counter import SlidingWindowCounter
from .token_bucket import TokenBucket
from .adaptive import AdaptiveRateLimiter

#: Name -> algorithm class. The four base algorithms benchmarked head-to-head.
#: (``adaptive`` is a higher-level composite of these, handled separately by
#: ``build_limiter`` and kept out of the four-way comparison.)
ALGORITHMS: dict[str, type[RateLimiter]] = {
    FixedWindowCounter.name: FixedWindowCounter,
    SlidingWindowLog.name: SlidingWindowLog,
    SlidingWindowCounter.name: SlidingWindowCounter,
    TokenBucket.name: TokenBucket,
}

__all__ = [
    "RateLimiter",
    "Backend",
    "MemoryBackend",
    "FixedWindowCounter",
    "SlidingWindowLog",
    "SlidingWindowCounter",
    "TokenBucket",
    "AdaptiveRateLimiter",
    "ALGORITHMS",
    "build_limiter",
]


def build_limiter(
    algorithm: str,
    backend: str = "memory",
    *,
    limit: int = 10,
    window: float = 60.0,
    namespace: str = "rl",
    redis_url: str = "redis://localhost:6379/0",
    **kwargs,
) -> RateLimiter:
    """Construct an algorithm + backend pair from plain configuration.

    This is the single entry point the demo middleware and any importing project
    (e.g. the URL shortener) use, so nothing application-specific leaks into the
    library.

    ``algorithm`` is one of ``ALGORITHMS`` (the four base algorithms) or
    ``"adaptive"`` to switch algorithm per client based on observed traffic shape.
    Extra ``kwargs`` pass through to the algorithm (e.g. token bucket's
    ``capacity`` / ``refill_rate``, or adaptive's ``policy`` / ``min_dwell_windows``).
    """
    valid = set(ALGORITHMS) | {"adaptive"}
    if algorithm not in valid:
        raise ValueError(
            f"unknown algorithm {algorithm!r}; choose from {sorted(valid)}"
        )

    if backend == "memory":
        backend_obj: Backend = MemoryBackend()
    elif backend == "redis":
        from .backends.redis_backend import RedisBackend

        backend_obj = RedisBackend(redis_url)
    else:
        raise ValueError(f"unknown backend {backend!r}; choose 'memory' or 'redis'")

    if algorithm == "adaptive":
        return AdaptiveRateLimiter(
            backend_obj, limit=limit, window=window, namespace=namespace, **kwargs
        )

    algo_cls = ALGORITHMS[algorithm]
    return algo_cls(backend_obj, limit=limit, window=window, namespace=namespace, **kwargs)
