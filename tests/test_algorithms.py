"""One parametrized suite run against every algorithm and both backends.

Because the tests parametrize over ``ratelimiter.ALGORITHMS``, each new algorithm
is automatically held to the same contract the moment it is registered. The
Redis parametrization is skipped automatically when no Redis is reachable, so the
suite passes out of the box without Docker.
"""

from __future__ import annotations

import pytest

from ratelimiter import ALGORITHMS, MemoryBackend

ALGO_NAMES = sorted(ALGORITHMS)

LIMIT = 5
WINDOW = 10.0
# A window-aligned start time so the fixed-window and sliding-counter boundary
# maths are clean and deterministic.
T0 = 1_000_000 * WINDOW


# --------------------------------------------------------------------------- backends
def _make_memory():
    return MemoryBackend()


def _make_redis():
    """Return a live Redis backend, or skip the test if Redis is unreachable."""
    redis = pytest.importorskip("redis")
    try:
        from ratelimiter.backends.redis_backend import RedisBackend
    except ImportError:
        pytest.skip("Redis backend not implemented yet (Phase 6)")

    try:
        client = redis.Redis.from_url("redis://localhost:6379/0")
        client.ping()
    except Exception:  # noqa: BLE001 - any connection failure -> skip, don't fail
        pytest.skip("Redis not reachable on localhost:6379")
    backend = RedisBackend("redis://localhost:6379/0")
    backend.reset()
    return backend


BACKEND_FACTORIES = {"memory": _make_memory, "redis": _make_redis}


@pytest.fixture(params=list(BACKEND_FACTORIES), ids=list(BACKEND_FACTORIES))
def backend(request):
    return BACKEND_FACTORIES[request.param]()


@pytest.fixture(params=ALGO_NAMES, ids=ALGO_NAMES)
def limiter(request, backend):
    algo_cls = ALGORITHMS[request.param]
    # Unique namespace per test param so Redis keys never collide across runs.
    return algo_cls(backend, limit=LIMIT, window=WINDOW, namespace=f"test:{request.param}")


# --------------------------------------------------------------------------- tests
def test_admits_up_to_limit_then_rejects(limiter):
    """Exactly ``LIMIT`` requests admitted in a fresh window; the rest rejected."""
    verdicts = [limiter.allow_request("client-a", now=T0) for _ in range(LIMIT + 3)]
    assert verdicts[:LIMIT] == [True] * LIMIT
    assert verdicts[LIMIT:] == [False] * 3


def test_recovers_after_window_passes(limiter):
    """After a full window (two, to clear every algorithm's trailing state) the
    client is admitted again."""
    for _ in range(LIMIT):
        assert limiter.allow_request("client-b", now=T0) is True
    assert limiter.allow_request("client-b", now=T0) is False
    # Two windows later, capacity is available again for all four algorithms.
    assert limiter.allow_request("client-b", now=T0 + 2 * WINDOW) is True


def test_clients_are_isolated(limiter):
    """One client exhausting its limit must not affect another."""
    for _ in range(LIMIT):
        assert limiter.allow_request("client-c", now=T0) is True
    assert limiter.allow_request("client-c", now=T0) is False
    # A different client still has a full allowance.
    assert limiter.allow_request("client-d", now=T0) is True


def test_admitted_count_never_exceeds_limit_under_pressure(limiter):
    """Hammering well past the limit within one window never over-admits."""
    admitted = sum(
        1 for _ in range(LIMIT * 10) if limiter.allow_request("client-e", now=T0)
    )
    assert admitted == LIMIT
