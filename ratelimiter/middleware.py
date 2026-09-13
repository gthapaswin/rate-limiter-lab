"""FastAPI middleware wrapping any algorithm + backend combo.

The middleware is deliberately generic: it takes a fully-built ``RateLimiter``
and a function that extracts a client identifier from the request. It hardcodes
nothing about which algorithm, backend, limit, or endpoint is in play -- so an
importing app can mount several with different configs (see the URL-shortener
use case in the spec).
"""

from __future__ import annotations

from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .base import RateLimiter


def default_key_func(request: Request) -> str:
    """Identify a client by source IP, falling back to a constant.

    Real deployments usually key on an API key or authenticated user id; pass a
    custom ``key_func`` for that.
    """
    if request.client and request.client.host:
        return request.client.host
    return "anonymous"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        limiter: RateLimiter,
        key_func: Callable[[Request], str] = default_key_func,
    ) -> None:
        super().__init__(app)
        self.limiter = limiter
        self.key_func = key_func

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        client_id = self.key_func(request)
        if not self.limiter.allow_request(client_id):
            return JSONResponse(
                {
                    "detail": "Rate limit exceeded",
                    "algorithm": self.limiter.name,
                    "limit": self.limiter.limit,
                    "window_seconds": self.limiter.window,
                },
                status_code=429,
                headers={"Retry-After": str(int(self.limiter.window))},
            )
        return await call_next(request)
