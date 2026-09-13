"""Traffic-shape generators for the benchmark.

Each generator returns a sorted list of request arrival times (unix-style
seconds, starting at 0) for a single client. The runner replays them through
``limiter.allow_request(client_id, now=t)`` so every algorithm and backend sees
the *identical* offered load -- the only variable is the limiter itself.

Three shapes, matching Section 6 of the spec:

  * constant -- steady offered rate a bit above the limit
  * bursty   -- long idle gaps, then spikes that straddle window boundaries
                (where Fixed Window and Token Bucket diverge most)
  * ramping  -- offered rate climbs linearly from well below to well above limit
"""

from __future__ import annotations


def constant_traffic(
    limit: int,
    window: float,
    duration: float,
    rate_factor: float = 1.5,
) -> list[float]:
    """Evenly spaced arrivals at ``rate_factor`` x the sustainable rate.

    Offering above the limit is deliberate: a correct limiter should admit ~the
    sustainable count and reject the excess, so accuracy is measurable.
    """
    rate = (limit / window) * rate_factor  # requests / second
    n = int(rate * duration)
    return [i / rate for i in range(n)]


def bursty_traffic(
    limit: int,
    window: float,
    duration: float,
    edge: float = 0.12,
) -> list[float]:
    """Isolated boundary-straddling bursts separated by a full idle window.

    Each burst event puts ``limit`` requests in the ``edge`` seconds just *before*
    a window boundary and another ``limit`` just *after* it -- so 2x the limit
    arrives inside a single trailing window. Events are spaced two windows apart
    so the intervening window is idle and each event stands alone.

    A Fixed Window counter resets exactly at the boundary, so both halves fall in
    different buckets and *both* are admitted (~2x the limit per event). A true
    sliding window (log) sees one 2x burst and admits only ~the limit. This is
    the divergence the headline chart is built around.
    """
    ts: list[float] = []
    # Boundaries at 2*window, 4*window, ... so alternate windows stay idle.
    k = 2
    while k * window <= duration:
        boundary = k * window
        for j in range(limit):  # pre-boundary half (ends just before boundary)
            ts.append(boundary - edge + edge * (j / limit))
        for j in range(limit):  # post-boundary half (starts just after boundary)
            ts.append(boundary + 0.0005 + edge * (j / limit))
        k += 2
    return sorted(t for t in ts if 0 <= t <= duration)


def ramping_traffic(
    limit: int,
    window: float,
    duration: float,
    start_factor: float = 0.2,
    end_factor: float = 3.0,
) -> list[float]:
    """Arrival rate climbs linearly from ``start_factor`` to ``end_factor`` x the
    sustainable rate over the run."""
    base = limit / window
    rate0 = base * start_factor
    rate1 = base * end_factor
    ts: list[float] = []
    t = 0.0
    while t < duration:
        frac = t / duration
        rate = rate0 + (rate1 - rate0) * frac
        ts.append(t)
        t += 1.0 / rate
    return ts


#: Registry so the runner can iterate shapes by name.
SHAPES = {
    "constant": constant_traffic,
    "bursty": bursty_traffic,
    "ramping": ramping_traffic,
}
