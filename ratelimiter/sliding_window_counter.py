"""Sliding Window Counter.

A cheap approximation of the sliding window log. Instead of storing every
timestamp, keep just two integers per client -- the count in the current fixed
window and the count in the previous one -- and estimate the rolling count as:

    estimate = previous_count * (fraction of the previous window still inside the
               trailing window) + current_count

This is O(1) memory per client (the win over the log) and far smoother than the
fixed window across boundaries, at the cost of a small approximation error when
traffic is very unevenly distributed inside a window.
"""

from __future__ import annotations

import math

from .base import RateLimiter


class SlidingWindowCounter(RateLimiter):
    name = "sliding_window_counter"

    # KEYS[1] = client key ; ARGV = now, limit, window
    # Stored hash fields: w=current window index, c=current count, p=previous count
    LUA = """
    local key    = KEYS[1]
    local now    = tonumber(ARGV[1])
    local limit  = tonumber(ARGV[2])
    local window = tonumber(ARGV[3])

    local current = math.floor(now / window)
    local elapsed = (now - current * window) / window   -- [0,1) into current window
    local weight_prev = 1 - elapsed

    local data = redis.call('HMGET', key, 'w', 'c', 'p')
    local cw = tonumber(data[1])
    local cc = tonumber(data[2])
    local pc = tonumber(data[3])

    if cw == nil then
        cw = current; cc = 0; pc = 0
    elseif cw == current then
        -- same window, keep counts
    elseif cw == current - 1 then
        pc = cc; cc = 0; cw = current
    else
        pc = 0; cc = 0; cw = current
    end

    local estimated = pc * weight_prev + cc
    local allowed = 0
    if estimated < limit then
        cc = cc + 1
        allowed = 1
    end
    redis.call('HMSET', key, 'w', cw, 'c', cc, 'p', pc)
    redis.call('EXPIRE', key, math.ceil(window * 2))
    return {allowed, math.floor(estimated)}
    """

    def _build_args(self, now: float) -> list:
        return [now, self.limit, self.window]

    @staticmethod
    def _py_op(store: dict, keys: list, args: list) -> list:
        key = keys[0]
        now, limit, window = args
        current = math.floor(now / window)
        elapsed = (now - current * window) / window
        weight_prev = 1 - elapsed

        entry = store.get(key)
        if entry is None:
            cw, cc, pc = current, 0, 0
        else:
            cw, cc, pc = entry
            if cw == current:
                pass
            elif cw == current - 1:
                pc, cc, cw = cc, 0, current
            else:
                pc, cc, cw = 0, 0, current

        estimated = pc * weight_prev + cc
        allowed = 0
        if estimated < limit:
            cc += 1
            allowed = 1
        store[key] = (cw, cc, pc)
        return [allowed, estimated]
