"""Tests specific to the AdaptiveRateLimiter (a composite, not one of the four)."""

from __future__ import annotations

from ratelimiter import MemoryBackend
from ratelimiter.adaptive import AdaptiveRateLimiter

LIMIT = 100
WINDOW = 1.0
T0 = 1_000_000 * WINDOW


def _make(min_dwell=1):
    return AdaptiveRateLimiter(
        MemoryBackend(), limit=LIMIT, window=WINDOW, namespace="adapt",
        min_dwell_windows=min_dwell,
    )


def test_contract_admit_up_to_limit_then_reject():
    """At a single instant it behaves like any limiter: LIMIT admits then rejects."""
    rl = _make()
    verdicts = [rl.allow_request("c", now=T0) for _ in range(LIMIT + 5)]
    assert verdicts[:LIMIT] == [True] * LIMIT
    assert verdicts[LIMIT:] == [False] * 5


def test_switch_cannot_reset_counter_within_a_window():
    """The anti-exploit invariant: a shape change must not grant a second
    allowance inside the same window."""
    rl = _make()
    # Exhaust the limit early in one window with steady-looking arrivals.
    admitted = 0
    for i in range(LIMIT):
        if rl.allow_request("c", now=T0 + i * (WINDOW / (LIMIT * 4))):
            admitted += 1
    assert admitted == LIMIT
    # Now hammer with a tight burst *still inside the same window*. Even though
    # this looks bursty, the active algorithm cannot change mid-window, so the
    # client stays capped -- no reset, no 2x.
    extra = sum(
        1 for j in range(LIMIT * 3)
        if rl.allow_request("c", now=T0 + 0.5 + j * 1e-6)
    )
    assert extra == 0


def test_adapts_from_steady_to_bursty():
    """Steady traffic -> sliding counter; then bursty traffic -> token bucket."""
    rl = _make(min_dwell=1)
    client = "c"

    # Phase 1: 4 windows of smooth, evenly spaced arrivals (under the limit).
    for w in range(4):
        for i in range(80):
            rl.allow_request(client, now=T0 + w * WINDOW + i * (WINDOW / 80))
    steady_stats = rl.stats(client)
    assert steady_stats["active"] == "sliding_window_counter"
    assert steady_stats["last_shape"] in {"steady", "warmup"}

    # Phase 2: 5 windows of boundary-straddling bursts.
    edge = 0.1
    for w in range(4, 9):
        boundary = T0 + w * WINDOW
        for j in range(LIMIT):
            rl.allow_request(client, now=boundary - edge + edge * (j / LIMIT))
        for j in range(LIMIT):
            rl.allow_request(client, now=boundary + 1e-4 + edge * (j / LIMIT))

    bursty_stats = rl.stats(client)
    assert bursty_stats["active"] == "token_bucket", bursty_stats
    assert bursty_stats["switches"] >= 1


def test_clients_are_independent():
    rl = _make()
    for _ in range(LIMIT):
        assert rl.allow_request("busy", now=T0) is True
    assert rl.allow_request("busy", now=T0) is False
    assert rl.allow_request("fresh", now=T0) is True


def test_strict_policy_uses_log_when_bursty():
    from ratelimiter.adaptive import STRICT_POLICY

    rl = AdaptiveRateLimiter(
        MemoryBackend(), limit=LIMIT, window=WINDOW, namespace="strict",
        policy=STRICT_POLICY, min_dwell_windows=1,
    )
    client = "c"
    edge = 0.1
    for w in range(6):
        boundary = T0 + w * WINDOW
        for j in range(LIMIT):
            rl.allow_request(client, now=boundary - edge + edge * (j / LIMIT))
        for j in range(LIMIT):
            rl.allow_request(client, now=boundary + 1e-4 + edge * (j / LIMIT))
    assert rl.stats(client)["active"] == "sliding_window_log"
