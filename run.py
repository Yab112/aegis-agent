"""
Run the API with Hypercorn in one process.

  python run.py

The ASGI app is wrapped in ``_OutermostHttpLog`` so every HTTP request is logged
**before** FastAPI — if the log file still stays empty, the browser is not
reaching this process (wrong host/port/firewall).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from starlette.types import ASGIApp, Receive, Scope, Send

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.logging_config import aegis_access_log_path, write_aegis_access_line


class _OutermostHttpLog:
    """First code that runs for each HTTP request (inside Hypercorn, outside FastAPI)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            m = scope.get("method", "?")
            p = scope.get("path", "")
            msg = f"[outer-asgi] -> {m} {p}"
            write_aegis_access_line(msg)
            print(msg, flush=True)
        await self.app(scope, receive, send)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aegis API (Hypercorn, in-process)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    args = parser.parse_args()

    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    from src.api.main import app as fastapi_app

    app: ASGIApp = _OutermostHttpLog(fastapi_app)

    log_path = aegis_access_log_path().resolve()
    print(f"[aegis] Per-request lines also go to: {log_path}", flush=True)
    base = f"http://{args.host}:{args.port}"
    print(f"[aegis] Chat test UI: {base}/chat-ui  (or {base}/public/chat.html)", flush=True)

    config = Config()
    config.bind = [f"{args.host}:{args.port}"]
    config.accesslog = None  # AegisASGILogMiddleware logs each request once
    config.errorlog = "-"
    asyncio.run(serve(app, config))


if __name__ == "__main__":
    main()
