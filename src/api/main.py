import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config.settings import get_settings
from src.api.logging_config import (
    AegisASGILogMiddleware,
    configure_logging,
    write_aegis_access_line,
)
from src.api.blog_internal import router as blog_internal_router
from src.api.blog_routes import router as blog_public_router
from src.api.routes import router

settings = get_settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(get_settings())
    # Proof the log file path works (check aegis-access.log after startup).
    write_aegis_access_line("BOOT lifespan started — if you see this, logging path is writable")
    logging.getLogger("aegis").info(
        "[aegis] Ready. Chat test UI: GET /chat-ui  (file: public/chat.html). "
        "Access log: aegis-access.log in project root."
    )
    yield


app = FastAPI(
    title="Aegis-Agent API",
    description=(
        "Portfolio concierge API. Browser chat tester: **GET /chat-ui** "
        "(static file: `public/chat.html`)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# Pure ASGI (not BaseHTTPMiddleware) so [req] lines show under Hypercorn/Uvicorn.
# Order: next line runs inner → CORS is outer (add CORS after this).
app.add_middleware(AegisASGILogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(blog_public_router)
app.include_router(blog_internal_router)

_PUBLIC = Path(__file__).resolve().parents[2] / "public"
_CHAT_HTML = _PUBLIC / "chat.html"
if _PUBLIC.is_dir():
    app.mount(
        "/public",
        StaticFiles(directory=str(_PUBLIC)),
        name="public",
    )


@app.get("/")
def root():
    """Quick links so `/` is discoverable from logs or the browser."""
    return {
        "service": "aegis-agent",
        "chat_ui": "/chat-ui",
        "chat_static": "/public/chat.html",
        "docs": "/docs",
        "health": "/health",
        "chat_api": "POST /chat",
        "blog_api": "GET /blog, GET /blog/topics, GET /blog/{slug}",
        "blog_internal": "POST /internal/blog/generate-draft (auth required)",
    }


@app.get("/chat-ui")
def chat_ui():
    """Browser tester for POST /chat — same file as ``public/chat.html``."""
    if not _CHAT_HTML.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Missing {_CHAT_HTML} — add public/chat.html under the project root.",
        )
    return FileResponse(_CHAT_HTML, media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok", "agent": "aegis-agent", "version": "1.0.0"}
