from .base import Backend
from .memory import MemoryBackend

__all__ = ["Backend", "MemoryBackend"]

# RedisBackend is imported lazily so the library is usable without redis running.
try:  # pragma: no cover - trivial import guard
    from .redis_backend import RedisBackend  # noqa: F401

    __all__.append("RedisBackend")
except Exception:  # redis client or server unavailable
    pass
