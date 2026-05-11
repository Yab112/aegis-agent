"""Simple in-memory rate limiting middleware for API abuse protection."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from config.settings import Settings


class _RateWindow:
    def __init__(self) -> None:
        self._events: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            q = self._events[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= max_requests:
                retry_after = int(max(1, q[0] + window_seconds - now))
                return False, retry_after
            q.append(now)
            return True, 0


class AegisRateLimitMiddleware:
    """Best-effort in-memory rate limiter keyed by IP and route group."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.window = _RateWindow()

    def _route_policy(self, path: str) -> tuple[int, int]:
        if path.startswith("/chat"):
            return self.settings.rate_limit_chat_per_minute, 60
        if path.startswith("/blog"):
            return self.settings.rate_limit_blog_per_minute, 60
        return self.settings.rate_limit_default_per_minute, 60

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in {"/health", "/docs", "/openapi.json", "/chat-ui"} or path.startswith(
            "/public"
        ):
            await self.app(scope, receive, send)
            return
        if "/telegram" in path:
            await self.app(scope, receive, send)
            return

        client = scope.get("client") or ("unknown", 0)
        ip = str(client[0])
        limit, window_seconds = self._route_policy(path)
        key = f"{ip}:{path.split('/', 2)[1] if '/' in path else path}"

        allowed, retry_after = self.window.allow(key=key, max_requests=limit, window_seconds=window_seconds)
        if allowed:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=429,
            content={
                "detail": "Too many requests. Please retry shortly.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)
