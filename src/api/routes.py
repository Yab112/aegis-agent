import logging
import uuid

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from tenacity import RetryError

logger = logging.getLogger("aegis.chat")

from src.agent.graph import agent
from src.agent.state import AgentState
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


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    session_id = _resolve_session_id(request.session_id)
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
        "chat ok session_id=%s intent=%s confidence=%s sources=%s",
        session_id,
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
    )
