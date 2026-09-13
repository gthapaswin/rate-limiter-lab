"""Sliding Window Log.

Keep a timestamp for every admitted request. On each new request, drop the
timestamps that have aged out of the trailing ``window`` and admit only if fewer
than ``limit`` remain. This is the most accurate algorithm -- there is no window
boundary to game -- but its cost is the point of the benchmark: storage grows
O(n) with the number of in-window requests per client, unlike the O(1) others.

Backends:
  * memory -- a Python list of timestamps per client.
  * redis  -- a sorted set scored by timestamp; ``ZREMRANGEBYSCORE`` trims the
    tail and ``ZCARD`` counts the survivors, all inside one Lua script. An
    atomic ``INCR`` supplies a unique member so simultaneous requests can't
    collide on the same set member (which would silently under-count).
"""

from __future__ import annotations

from .base import RateLimiter


class SlidingWindowLog(RateLimiter):
    name = "sliding_window_log"

    # KEYS[1] = client key
    # ARGV = now, limit, window
    LUA = """
    local key    = KEYS[1]
    local now    = tonumber(ARGV[1])
    local limit  = tonumber(ARGV[2])
    local window = tonumber(ARGV[3])
    local cutoff = now - window

    -- Drop everything at or before the cutoff (keeps score > cutoff).
    redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
    local count = redis.call('ZCARD', key)

    local allowed = 0
    if count < limit then
        local seq = redis.call('INCR', key .. ':seq')
        redis.call('ZADD', key, now, now .. ':' .. seq)
        count = count + 1
        allowed = 1
    end
    redis.call('EXPIRE', key, math.ceil(window))
    redis.call('EXPIRE', key .. ':seq', math.ceil(window))
    return {allowed, count}
    """

    def _build_args(self, now: float) -> list:
        return [now, self.limit, self.window]

    @staticmethod
    def _py_op(store: dict, keys: list, args: list) -> list:
        key = keys[0]
        now, limit, window = args
        cutoff = now - window

        log = store.get(key, [])
        # Keep only timestamps strictly inside the trailing window.
        log = [t for t in log if t > cutoff]

        allowed = 0
        if len(log) < limit:
            log.append(now)
            allowed = 1
        store[key] = log
        return [allowed, len(log)]
