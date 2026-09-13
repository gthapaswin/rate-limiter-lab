"""Fixed Window Counter.

Count requests in a fixed clock-aligned bucket (e.g. each 60s slot). The count
resets the instant the boundary is crossed.

Known weakness (the benchmark is designed to expose it): a client can fire up to
``limit`` requests in the last moment of one window and another ``limit`` in the
first moment of the next -- ~2x the intended rate across a boundary.
"""

from __future__ import annotations

import math

from .base import RateLimiter


class FixedWindowCounter(RateLimiter):
    name = "fixed_window"

    # KEYS[1] = client key
    # ARGV = now, limit, window
    LUA = """
    local key    = KEYS[1]
    local now    = tonumber(ARGV[1])
    local limit  = tonumber(ARGV[2])
    local window = tonumber(ARGV[3])

    local current = math.floor(now / window)
    local data  = redis.call('HMGET', key, 'w', 'c')
    local w     = tonumber(data[1])
    local count = tonumber(data[2])
    if w == nil or w ~= current then count = 0 end

    local allowed = 0
    if count < limit then
        count = count + 1
        allowed = 1
    end
    redis.call('HMSET', key, 'w', current, 'c', count)
    redis.call('EXPIRE', key, math.ceil(window * 2))
    return {allowed, count}
    """

    def _build_args(self, now: float) -> list:
        return [now, self.limit, self.window]

    @staticmethod
    def _py_op(store: dict, keys: list, args: list) -> list:
        key = keys[0]
        now, limit, window = args
        current = math.floor(now / window)

        entry = store.get(key)
        if entry is None or entry[0] != current:
            count = 0
        else:
            count = entry[1]

        allowed = 0
        if count < limit:
            count += 1
            allowed = 1
        store[key] = (current, count)
        return [allowed, count]
