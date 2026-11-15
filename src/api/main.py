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
from src.api.admin_email_routes import router as admin_email_router
from src.api.admin_people_routes import router as admin_people_router
from src.api.admin_routes import public_router as projects_public_router
from src.api.admin_tools_routes import router as admin_tools_router
from src.api.admin_routes import router as admin_router
from src.api.blog_categories import router as blog_categories_router
from src.api.blog_internal import router as blog_internal_router
from src.api.blog_routes import router as blog_public_router
from src.api.health_routes import router as health_router
from src.api.keyword_routes import router as keyword_router
from src.api.newsroom_routes import router as newsroom_router
from src.api.routes import router
from src.api.scheduler_routes import internal_router as scheduler_internal_router
from src.api.scheduler_routes import public_router as automation_public_router
from src.api.rate_limit import AegisRateLimitMiddleware
from src.api.telegram_webhook import router as telegram_router
from src.scheduler.service import get_scheduler

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
    try:  # show the owner's command menu in Telegram (best effort)
        from src.telegram_bot.commands import BOT_COMMANDS
        from src.tools.handoff_tool import telegram_call

        chat = str(get_settings().telegram_chat_id or "").strip()
        if chat:
            telegram_call("setMyCommands", {"commands": BOT_COMMANDS, "scope": {"type": "chat", "chat_id": chat}})
    except Exception:
        logging.getLogger("aegis").warning("[aegis] could not register Telegram commands")
    scheduler = None
    if get_settings().scheduler_enabled:
        try:
            scheduler = get_scheduler()
            scheduler.start()
        except Exception:
            logging.getLogger("aegis").exception("[aegis] scheduler failed to start")
            scheduler = None
    else:
        logging.getLogger("aegis").info("[aegis] scheduler disabled (SCHEDULER_ENABLED=false)")
    yield
    if scheduler is not None:
        await scheduler.stop()


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
app.add_middleware(AegisRateLimitMiddleware, settings=settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(blog_categories_router)  # before /blog/{slug}
app.include_router(blog_public_router)
app.include_router(blog_internal_router)
app.include_router(health_router)
app.include_router(telegram_router)
app.include_router(scheduler_internal_router)
app.include_router(automation_public_router)
app.include_router(admin_email_router)
app.include_router(admin_people_router)
app.include_router(admin_tools_router)
app.include_router(admin_router)
app.include_router(projects_public_router)
app.include_router(keyword_router)
app.include_router(newsroom_router)

# Versioned aliases for forward-compatible clients.
app.include_router(router, prefix="/v1")
app.include_router(blog_categories_router, prefix="/v1")
app.include_router(blog_public_router, prefix="/v1")
app.include_router(blog_internal_router, prefix="/v1")
app.include_router(health_router, prefix="/v1")
app.include_router(telegram_router, prefix="/v1")
app.include_router(scheduler_internal_router, prefix="/v1")
app.include_router(automation_public_router, prefix="/v1")
app.include_router(admin_email_router, prefix="/v1")
app.include_router(admin_people_router, prefix="/v1")
app.include_router(admin_tools_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")
app.include_router(projects_public_router, prefix="/v1")
app.include_router(keyword_router, prefix="/v1")
app.include_router(newsroom_router, prefix="/v1")

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
        "health": "GET /health, GET /health/livez, GET /health/readyz",
        "chat_api": "POST /chat",
        "blog_api": "GET /blog, GET /blog/topics, GET /blog/search, GET /blog/{slug}",
        "blog_internal": "POST /internal/blog/generate-draft (auth required)",
        "automation": "GET /automation/status (public pipeline status)",
        "scheduler_internal": (
            "GET /internal/scheduler/status, POST /internal/scheduler/jobs/{name}/run|pause|resume, "
            "POST /internal/scheduler/tick (auth required)"
        ),
        "admin": "GET /admin/overview, /admin/inbox, /admin/posts, /admin/ideas, /admin/projects (auth required)",
        "keywords": "GET /keywords/best, GET /keywords/analyze?q=, POST /keywords/{id}/write (auth required)",
        "newsroom": "GET /newsroom/sources, GET /newsroom/items, POST /newsroom/collect (auth required)",
        "projects": "GET /projects (public: admin-managed project overrides)",
        "telegram_webhook": "POST /v1/telegram/webhook (Telegram setWebhook → owner reply → visitor email)",
        "v1_aliases": "GET /v1/health, POST /v1/chat, GET /v1/blog, POST /v1/telegram/webhook",
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

