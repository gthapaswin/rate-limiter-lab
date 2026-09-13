"""Token Bucket.

A bucket holds up to ``capacity`` tokens and refills continuously at
``refill_rate`` tokens/second. Each request spends one token; if the bucket is
empty the request is rejected. This admits short bursts (up to a full bucket)
while holding the long-run average to the refill rate -- the behaviour most APIs
actually want.

Defaults derive a bucket from the shared (limit, window) contract so it slots
into the same benchmark as the others:

    refill_rate = limit / window      # sustained rate
    capacity    = limit               # burst allowance == one window's worth

Pass ``capacity`` / ``refill_rate`` explicitly to tune burst tolerance
independently (Section 5's "known weakness": these must be set deliberately).
"""

from __future__ import annotations

import math

from .backends.base import Backend
from .base import RateLimiter


class TokenBucket(RateLimiter):
    name = "token_bucket"

    def __init__(
        self,
        backend: Backend,
        limit: int,
        window: float,
        namespace: str = "rl",
        *,
        capacity: float | None = None,
        refill_rate: float | None = None,
    ) -> None:
        super().__init__(backend, limit, window, namespace)
        self.capacity = float(capacity) if capacity is not None else float(limit)
        self.refill_rate = (
            float(refill_rate) if refill_rate is not None else limit / float(window)
        )

    # KEYS[1] = client key ; ARGV = now, capacity, refill_rate
    # Stored hash fields: t=tokens, ts=last refill timestamp
    LUA = """
    local key      = KEYS[1]
    local now      = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local rate     = tonumber(ARGV[3])

    local data = redis.call('HMGET', key, 't', 'ts')
    local tokens = tonumber(data[1])
    local last   = tonumber(data[2])
    if tokens == nil then
        tokens = capacity
        last = now
    end

    tokens = math.min(capacity, tokens + (now - last) * rate)
    last = now

    local allowed = 0
    if tokens >= 1 then
        tokens = tokens - 1
        allowed = 1
    end
    redis.call('HMSET', key, 't', tokens, 'ts', last)
    redis.call('EXPIRE', key, math.ceil(capacity / rate) + 1)
    return {allowed, math.floor(tokens)}
    """

    def _build_args(self, now: float) -> list:
        return [now, self.capacity, self.refill_rate]

    @staticmethod
    def _py_op(store: dict, keys: list, args: list) -> list:
        key = keys[0]
        now, capacity, rate = args

        entry = store.get(key)
        if entry is None:
            tokens, last = capacity, now
        else:
            tokens, last = entry
            tokens = min(capacity, tokens + (now - last) * rate)
            last = now

        allowed = 0
        if tokens >= 1.0:
            tokens -= 1.0
            allowed = 1
        store[key] = (tokens, last)
        return [allowed, tokens]
