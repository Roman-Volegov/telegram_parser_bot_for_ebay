from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        limit = _limit_for_path(request.url.path)
        if limit is not None:
            client = request.client.host if request.client else "unknown"
            bucket = _bucket_for_path(request.url.path)
            now = time.monotonic()
            key = (client, bucket)
            async with self._lock:
                timestamps = self._requests[key]
                while timestamps and timestamps[0] <= now - 60:
                    timestamps.popleft()
                if len(timestamps) >= limit:
                    return JSONResponse(
                        {"detail": "Too many requests"},
                        status_code=429,
                        headers={"Retry-After": "60"},
                    )
                timestamps.append(now)
                if len(self._requests) > 5000:
                    self._requests = defaultdict(
                        deque,
                        {
                            item_key: values
                            for item_key, values in self._requests.items()
                            if values and values[-1] > now - 60
                        },
                    )
        return await call_next(request)


def _bucket_for_path(path: str) -> str:
    if path.startswith("/api/"):
        return "api"
    if path.startswith("/ebay/deletion/"):
        return "deletion"
    return "etsy-access"


def _limit_for_path(path: str) -> int | None:
    if path.startswith("/api/"):
        return 120
    if path.startswith("/ebay/deletion/"):
        return 30
    if path == "/etsy-captcha/access":
        return 20
    return None
