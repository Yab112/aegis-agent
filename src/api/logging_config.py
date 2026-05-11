"""
HTTP middleware + logging.

FastAPI is ASGI-only — use Hypercorn, Uvicorn, etc. One stderr handler on ``aegis``
(flushed each emit). Request lines are logged **once** via that logger (plus an
optional append to ``aegis-access.log``); do not also enable Hypercorn/Uvicorn
``--access-logfile -`` or you will duplicate every request in the console.

We use **pure ASGI** middleware (not BaseHTTPMiddleware) so every request is logged
reliably with Hypercorn/Uvicorn; BaseHTTPMiddleware uses extra anyio plumbing that
can make request logs disappear in some setups.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from config.settings import Settings

# Project root: src/api/logging_config.py → parents[2]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REQUEST_LOG_FILE = _PROJECT_ROOT / "aegis-access.log"


def aegis_access_log_path() -> Path:
    """Absolute path to ``aegis-access.log`` (project root). Use for startup hints."""
    return _REQUEST_LOG_FILE

_LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
_LOG_DATEFMT = "%H:%M:%S"


def write_aegis_access_line(message: str) -> None:
    """
    Append one line to ``aegis-access.log`` under the project root (``*.log`` is gitignored).

    Use for boot markers and every HTTP request — survives IDE terminals that hide
    child-process or buffered stdout.
    """
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    try:
        with _REQUEST_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except OSError:
        pass


class _FlushingStderrHandler(logging.StreamHandler):
    """Always flush — Windows + ASGI workers often buffer stderr until newline/exit."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def _attach_aegis_stderr_handler(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger("aegis")
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = _FlushingStderrHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    root.addHandler(handler)
    root.propagate = False


def configure_logging(settings: Settings) -> None:
    """Attach stderr logging for `aegis` and tune third-party loggers."""
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    _attach_aegis_stderr_handler(settings)

    logging.getLogger("src").setLevel(level)

    if level <= logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
        logging.getLogger("googleapiclient.discovery").setLevel(logging.DEBUG)
    else:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)

    logging.getLogger("google.auth").setLevel(logging.WARNING)
    logging.getLogger("google_auth_httplib2").setLevel(logging.WARNING)


class AegisASGILogMiddleware:
    """
    Pure ASGI middleware — logs every HTTP request on the `aegis` logger.
    Register with ``app.add_middleware(AegisASGILogMiddleware)`` **before** CORS
    so stack order is: CORS (outer) → this → routes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        log = logging.getLogger("aegis")
        method = scope.get("method", "?")
        path = scope.get("path", "")
        start = time.perf_counter()
        line_in = f"[req] -> {method} {path}"
        write_aegis_access_line(line_in)
        log.info("%s", line_in)

        status: dict[str, int | None] = {"code": None}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message.get("status")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("[req] x %s %s (unhandled)", method, path)
            raise
        ms = (time.perf_counter() - start) * 1000
        line_out = f"[req] <- {method} {path} {status['code']} {ms:.0f}ms"
        write_aegis_access_line(line_out)
        log.info("%s", line_out)
