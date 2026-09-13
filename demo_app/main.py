"""Minimal FastAPI app exercising the rate-limiter middleware.

Everything is configured through environment variables so the very same image
can be run twice against one Redis (see docker-compose.yml) to demonstrate that
the Redis backend shares a single limit across instances.

    RL_ALGORITHM  fixed_window | sliding_window_log | sliding_window_counter | token_bucket
    RL_BACKEND    memory | redis
    RL_LIMIT      integer, requests per window          (default 10)
    RL_WINDOW     window length in seconds              (default 60)
    RL_REDIS_URL  redis://host:port/db                  (default redis://localhost:6379/0)

Run locally:
    uvicorn demo_app.main:app --reload
Then hammer it:
    for i in $(seq 1 15); do curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/ping; done
"""

from __future__ import annotations

import os
import socket

from fastapi import FastAPI
from starlette.requests import Request

from ratelimiter import build_limiter
from ratelimiter.middleware import RateLimitMiddleware, default_key_func

ALGORITHM = os.getenv("RL_ALGORITHM", "fixed_window")
BACKEND = os.getenv("RL_BACKEND", "memory")
LIMIT = int(os.getenv("RL_LIMIT", "10"))
WINDOW = float(os.getenv("RL_WINDOW", "60"))
REDIS_URL = os.getenv("RL_REDIS_URL", "redis://localhost:6379/0")

limiter = build_limiter(
    ALGORITHM,
    BACKEND,
    limit=LIMIT,
    window=WINDOW,
    redis_url=REDIS_URL,
)

def key_func(request: Request) -> str:
    """Prefer an explicit ``X-Client-Id`` header, else fall back to source IP.

    The header makes the multi-instance demo deterministic: curl both instances
    with the same ``X-Client-Id`` and they must share one aggregate limit,
    regardless of the (Docker-assigned) source IP each container observes.
    """
    return request.headers.get("x-client-id") or default_key_func(request)


app = FastAPI(title="Rate Limiter Demo")
app.add_middleware(RateLimitMiddleware, limiter=limiter, key_func=key_func)

# Identifies which process answered -- handy when two instances share one Redis.
INSTANCE = socket.gethostname()


@app.get("/")
def root():
    return {
        "message": "ok",
        "instance": INSTANCE,
        "algorithm": ALGORITHM,
        "backend": BACKEND,
        "limit": LIMIT,
        "window_seconds": WINDOW,
    }


@app.get("/ping")
def ping():
    return {"pong": True, "instance": INSTANCE}
