import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from tenacity import RetryError

logger = logging.getLogger("aegis.chat")

from src.agent.graph import agent
from src.agent.state import AgentState
from src.agent.visitor import classify_visitor
from src.tools.calendar_tool import CalendarOAuthError
from src.tools.lead_tool import upsert_session, load_session

router = APIRouter()


def _resolve_session_id(raw: str | None) -> str:
    """
    Swagger/OpenAPI often sends the placeholder ``\"string\"`` — not a valid UUID.
    Supabase ``sessions.id`` is uuid; coerce invalid values to a new id.
    """
    if raw is None:
        return str(uuid.uuid4())
    s = raw.strip()
    if not s:
        return str(uuid.uuid4())
    try:
        uuid.UUID(s)
        return s
    except ValueError:
        return str(uuid.uuid4())


def _format_pipeline_error(exc: BaseException) -> str:
    """Surface the real failure instead of RetryError[<Future ...>]."""
    if isinstance(exc, RetryError):
        fut = exc.last_attempt
        try:
            fut.result()
        except Exception as inner:
            return f"Upstream request failed after retries: {inner!r}"
        return repr(exc)
    return str(exc)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = Field(
        default=None,
        description=(
            "Omit for a new chat, or send a UUID from a prior response. "
            "Invalid placeholders (e.g. Swagger's default) are replaced with a new id."
        ),
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class ChatResponse(BaseModel):
    response: str
    session_id: str
    sources: list[str]
    intent: str | None
    confidence: float | None
    # "human" | "ai_agent" | "crawler": how the agent read this caller
    visitor: str | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http: Request):
    session_id = _resolve_session_id(request.session_id)
    # The site proxies chat, so it forwards the browser's UA in X-Visitor-User-Agent.
    visitor = classify_visitor(
        http.headers.get("x-visitor-user-agent") or http.headers.get("user-agent"),
        request.message,
    )
    preview = (request.message[:120] + "…") if len(request.message) > 120 else request.message
    logger.info("chat start session_id=%s message=%r", session_id, preview)

    # Load prior messages for session continuity
    prior_messages_raw = load_session(session_id)
    prior_messages = [
        HumanMessage(content=m["content"]) if m["role"] == "human"
        else __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(content=m["content"])
        for m in prior_messages_raw
    ]

    initial_state: AgentState = {
        "messages": prior_messages + [HumanMessage(content=request.message)],
        "session_id": session_id,
        "user_query": request.message,
        "visitor_kind": visitor.kind,
        "visitor_agent": visitor.agent_name,
        "intent": None,
        "book_stage": None,
        "metadata_filter": None,
        "retrieved_chunks": None,
        "confidence_score": None,
        "tool_calls": [],
        "tool_outputs": [],
        "iterations": 0,
        "should_handoff": False,
        "response": None,
        "sources": None,
    }

    try:
        final_state = await agent.ainvoke(initial_state)
    except CalendarOAuthError as e:
        logger.exception("chat pipeline failed (Google OAuth) session_id=%s", session_id)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("chat pipeline failed session_id=%s", session_id)
        raise HTTPException(status_code=500, detail=_format_pipeline_error(e))

    # Persist updated session
    all_msgs = [
        {"role": "human" if m.type == "human" else "ai", "content": m.content}
        for m in final_state["messages"]
        if hasattr(m, "content")
    ]
    upsert_session(session_id, all_msgs)

    logger.info(
        "chat ok session_id=%s visitor=%s intent=%s confidence=%s sources=%s",
        session_id,
        visitor.kind,
        final_state.get("intent"),
        final_state.get("confidence_score"),
        final_state.get("sources") or [],
    )

    return ChatResponse(
        response=final_state["response"] or "I couldn't generate a response. Please try again.",
        session_id=session_id,
        sources=final_state.get("sources") or [],
        intent=final_state.get("intent"),
        confidence=final_state.get("confidence_score"),
        visitor=visitor.kind,
    )


@router.get("/agent/profile", summary="Machine-readable profile for AI agents")
def agent_profile() -> dict:
    """Structured facts about the owner, for AI agents that prefer JSON over a chat."""
    import json
    from pathlib import Path

    from config.settings import get_settings

    settings = get_settings()
    path = Path(__file__).resolve().parents[2] / "docs" / "knowledge_base" / "general" / "profile.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        profile = {}
    site = settings.portfolio_url.rstrip("/")
    return {
        "name": settings.owner_name,
        "role": settings.owner_role,
        "profile": profile,
        "links": {
            "site": site,
            "llms_txt": f"{site}/llms.txt",
            "work": f"{site}/work",
            "writing": f"{site}/blog",
            "contact": f"{site}/contact",
        },
        "how_to_use": {
            "ask": "POST /chat with JSON {\"message\": \"...\", \"session_id\": \"<uuid, optional>\"}",
            "book_a_call": "Ask in /chat for availability; give the person's email to confirm a slot.",
            "reach_the_owner": "Ask in /chat about hiring, pricing or contracts; include the person's email so he can reply.",
        },
    }
