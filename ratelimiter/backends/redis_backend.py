"""Redis-backed store with Lua-scripted atomic operations.

This is what makes the limiter correct across multiple app instances. The whole
read-decide-write step for each algorithm is shipped to Redis as a Lua script;
Redis runs a script to completion without interleaving other commands, so two
requests from the same client -- even from different processes -- can never both
read "count = limit - 1" and both be admitted.

``register_script`` uses ``EVALSHA`` (falling back to ``EVAL`` once to cache the
script), so after the first call only the 40-char SHA travels over the wire.
"""

from __future__ import annotations

from typing import Callable

import redis

from .base import Backend


class RedisBackend(Backend):
    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        self.url = url
        self.client = redis.Redis.from_url(url)
        # Cache one registered Script per unique Lua source.
        self._scripts: dict[str, "redis.client.Script"] = {}

    def execute(
        self,
        py_op: Callable[[dict, list, list], list],  # unused; memory backend's path
        lua: str,
        keys: list,
        args: list,
    ) -> list:
        script = self._scripts.get(lua)
        if script is None:
            script = self.client.register_script(lua)
            self._scripts[lua] = script
        # Returns e.g. [1, 4]; result[0] is the admit/reject flag.
        return script(keys=keys, args=args)

    def reset(self) -> None:
        self.client.flushdb()

    # -- helper for the benchmark's memory-footprint measurement ---------------
    def key_bytes(self, key: str) -> int:
        """Bytes Redis reports for one client's key (0 if absent)."""
        return int(self.client.memory_usage(key) or 0)
