"""
Agent nodes: each node performs one action and returns state updates.
"""
import logging
import re
import warnings
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai
from langchain_core.messages import AIMessage

from config.settings import get_settings
from src.agent.state import AgentState
from src.rag.retriever import retrieve
from src.tools.calendar_tool import (
    check_availability,
    create_meet_link,
    diversify_slots,
    filter_slots_by_weekday,
)
from src.tools.handoff_tool import send_whatsapp_briefing
from src.tools.lead_tool import log_lead

logger = logging.getLogger("aegis.nodes")
settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel(settings.gemini_model)

CALENDAR_REPLY_PROMPT = """You are {assistant_name}, the concierge for {owner_name}'s portfolio (not {owner_name} himself).

Timezone: {tz_name}.

Latest user message:
{user_query}

Recent conversation:
{history}

--- Calendar facts (only use these times; do not invent slots) ---
{slot_facts}
---

Instructions:
- Use ONLY times from the facts block. Never invent slots. If the user asked for one weekday and the facts show no slots that day, do not list other days' times as if they were that weekday.
- Answer the user's actual question. If they ask whether times are only on one day, explain honestly using the facts.
- If there are no slots at all, be brief and point them to {portfolio_url}.
- Voice: direct, warm, slightly witty — no corporate filler.
- End by asking for a chosen slot + their email for the Meet invite if booking isn't locked in yet.

Plain text only, no JSON."""

RAG_SYSTEM_PROMPT = """You are **{assistant_name}**, the single concierge for {owner_name}'s portfolio at {portfolio_url}.

## Who you are (stay in character)
- You speak in first person as **{assistant_name}** only. You are not {owner_name}; you speak *about* him in third person ("he", "{owner_name}").
- Role: help visitors understand his work, stack, and projects — and nudge toward a call or contact when it fits, without being pushy.
- Voice: **direct, warm, slightly witty** — like a sharp engineer friend at a coffee chat. Confident, never corporate. No filler ("I'd be happy to assist", "as an AI").
- Rhythm: short paragraphs, one idea per beat. Use plain English; explain jargon only when it helps.
- Tone limits: never cheesy, never sycophantic, never preachy. Light humor is OK if it fits the user.

## What you do
- Use CONTEXT as the only source of facts about projects, employers, dates, and tech.
- Greetings / small talk: answer as {assistant_name}; one beat of warmth, then steer toward something useful (projects, stack, or booking).
- Technical questions: be precise; name real projects and tools from CONTEXT; use `backticks` for code or stack names when natural.

## Hard rules
- Do not invent employers, timelines, or projects not supported by CONTEXT.
- Do not mention RAG, embeddings, or "knowledge base".
- Do not pretend messages were forwarded or escalated unless the user is clearly in a commercial negotiation flow (rates, contracts) — and even then stay calm.

## If CONTEXT is empty or thin
Say you don't have that detail in the portfolio materials and suggest they check {portfolio_url} or leave a note — no fake urgency.

CONTEXT:
{context}

CONVERSATION HISTORY:
{history}
"""


# ──────────────────────────────────────────────────────────────────────────────
# RAG NODE
# ──────────────────────────────────────────────────────────────────────────────

def rag_node(state: AgentState) -> dict:
    logger.info("node rag: Supabase match_documents + HF embeddings")
    chunks, confidence = retrieve(
        query=state["user_query"],
        top_k=settings.rag_top_k,
        metadata_filter=state.get("metadata_filter"),
    )

    tool_calls = state.get("tool_calls", []) + ["rag"]
    tool_outputs = state.get("tool_outputs", []) + [
        {"tool": "rag", "chunks_found": len(chunks), "confidence": confidence}
    ]

    logger.info(
        "node rag: done chunks=%d confidence=%.4f",
        len(chunks),
        confidence,
    )

    return {
        "retrieved_chunks": chunks,
        "confidence_score": confidence,
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
        # Never set should_handoff here — low retrieval must not trigger WhatsApp; router alone decides handoff.
        "should_handoff": False,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CALENDAR NODE
# ──────────────────────────────────────────────────────────────────────────────

def calendar_node(state: AgentState) -> dict:
    logger.info("node calendar: Google Calendar API (availability + optional Meet)")
    book_stage = (state.get("book_stage") or "availability").lower()
    cal_tz = ZoneInfo(settings.calendar_timezone)
    raw = check_availability(days_ahead=14, max_collect=72)
    pref = _parse_weekday_preference(state)
    if pref is not None:
        slots = filter_slots_by_weekday(raw, cal_tz, pref)
    else:
        slots = diversify_slots(raw, cal_tz, 5)

    alternatives: list[dict] = []
    if pref is not None and not slots and raw:
        alternatives = diversify_slots(raw, cal_tz, 5)

    meet_link = None
    booking_note = None

    # Only create Meet + invite after explicit "schedule" stage + email (router decides stage).
    if book_stage == "schedule" and slots:
        email = _extract_email(state)
        slot = _match_slot_to_availability(state["user_query"], slots)
        if slot is None:
            slot = slots[0]
        if email:
            meet_link = create_meet_link(slot, attendee_email=email)
            if not meet_link:
                booking_note = "create_failed"
        else:
            booking_note = "need_email"
    elif book_stage == "schedule" and not slots:
        booking_note = "no_slots"

    logger.info(
        "node calendar: done slots=%d meet_link=%s book_stage=%s note=%s",
        len(slots),
        "yes" if meet_link else "no",
        book_stage,
        booking_note,
    )

    tool_calls = state.get("tool_calls", []) + ["calendar"]
    tool_outputs = state.get("tool_outputs", []) + [
        {
            "tool": "calendar",
            "slots_found": len(slots),
            "slots": slots,
            "pool_size": len(raw),
            "weekday_filter": pref,
            "alternatives": alternatives,
            "meet_link": meet_link,
            "book_stage": book_stage,
            "booking_note": booking_note,
        }
    ]

    return {
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
    }


# ──────────────────────────────────────────────────────────────────────────────
# HANDOFF NODE
# ──────────────────────────────────────────────────────────────────────────────

def handoff_node(state: AgentState) -> dict:
    logger.info("node handoff: WhatsApp Cloud API + optional Supabase lead")
    # Try to extract email from conversation
    user_email = _extract_email(state)

    if user_email:
        log_lead(
            session_id=state["session_id"],
            email=user_email,
            intent=state.get("intent", "handoff"),
            query=state["user_query"],
        )

    whatsapp_sent = send_whatsapp_briefing(
        query=state["user_query"],
        intent=state.get("intent", "sensitive"),
        session_id=state["session_id"],
        user_email=user_email,
    )

    tool_calls = state.get("tool_calls", []) + ["handoff"]
    tool_outputs = state.get("tool_outputs", []) + [
        {
            "tool": "handoff",
            "whatsapp_sent": whatsapp_sent,
            "lead_logged": bool(user_email),
            "user_email": user_email,
        }
    ]

    return {
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
        "should_handoff": False,  # Already handled
    }


# ──────────────────────────────────────────────────────────────────────────────
# OBSERVE NODE
# ──────────────────────────────────────────────────────────────────────────────

def observe_node(state: AgentState) -> dict:
    """
    Reviews what the last tool returned and decides if more action is needed.
    State updates handled by routing edges — this node just increments iterations.
    """
    return {"iterations": state.get("iterations", 0) + 1}


# ──────────────────────────────────────────────────────────────────────────────
# RESPOND NODE
# ──────────────────────────────────────────────────────────────────────────────

def respond_node(state: AgentState) -> dict:
    last_tool = state["tool_calls"][-1] if state.get("tool_calls") else "rag"

    if last_tool == "handoff":
        ho = [t for t in state.get("tool_outputs", []) if t.get("tool") == "handoff"]
        tool_out = ho[-1] if ho else {}
        email = tool_out.get("user_email")
        wa_ok = tool_out.get("whatsapp_sent")

        logger.info("node respond: template (handoff), no Gemini")
        if email:
            response_text = (
                f"I'm {settings.assistant_name} — salary and full-time terms are for "
                f"{settings.owner_name} to answer.\n\n"
                f"I still have **{email}** from earlier in this chat — he'll follow up there.\n\n"
                f"I pinged him on WhatsApp with this question (that alert goes to **his** phone, not yours — "
                f"so you won't see a new WhatsApp on your side)."
                f"{' Meta accepted the send.' if wa_ok else ' WhatsApp send failed on our side; he may still see the lead in the dashboard.'}"
            )
        else:
            response_text = (
                f"I'm {settings.assistant_name} — this one needs {settings.owner_name} directly "
                f"(rates, scope, contracts). "
                f"{'I sent him a WhatsApp alert.' if wa_ok else 'WhatsApp delivery failed — use the site or leave your email.'} "
                f"Drop your email in your next message if you haven’t shared one yet."
            )
        return {
            "response": response_text,
            "sources": [],
            "messages": [AIMessage(content=response_text)],
        }

    if last_tool == "calendar":
        tool_out = next(
            (t for t in state["tool_outputs"] if t["tool"] == "calendar"), {}
        )
        meet_link = tool_out.get("meet_link")
        slots = tool_out.get("slots") or []
        booking_note = tool_out.get("booking_note")
        book_stage = tool_out.get("book_stage") or "availability"

        if meet_link:
            logger.info("node respond: template (calendar booked)")
            response_text = (
                f"{settings.assistant_name} here — booked a slot for {settings.owner_name}. "
                f"Meet: {meet_link}\n\n"
                f"You should get a calendar invite at the email you gave. Anything else?"
            )
        elif booking_note == "create_failed":
            logger.info("node respond: template (calendar create_failed)")
            response_text = (
                f"Couldn't create the Meet link just now — try again in a bit, "
                f"or reach him via {settings.portfolio_url}."
            )
        elif booking_note == "no_slots" and book_stage == "schedule":
            logger.info("node respond: template (calendar schedule, no slots)")
            response_text = (
                f"Nothing open for that request in the window I checked — "
                f"{settings.owner_name} can follow up via {settings.portfolio_url}."
            )
        elif tool_out.get("weekday_filter") is not None and not slots:
            logger.info("node respond: deterministic (weekday filter, no slots on that day)")
            response_text = _deterministic_weekday_filtered_empty(tool_out)
        else:
            logger.info("node respond: Gemini (calendar Q&A + slots)")
            response_text = _gemini_calendar_reply(state, tool_out)

        return {
            "response": response_text,
            "sources": [],
            "messages": [AIMessage(content=response_text)],
        }

    # Default: RAG response
    chunks = state.get("retrieved_chunks", [])
    context = "\n\n---\n\n".join(
        f"[{c['metadata'].get('project_name', 'general')} · {c['metadata'].get('type', 'doc')}]\n{c['content']}"
        for c in chunks
    )

    history = "\n".join(
        f"{m.type.upper()}: {m.content}"
        for m in state["messages"][-6:]  # last 3 turns
        if hasattr(m, "content")
    )

    full_prompt = (
        RAG_SYSTEM_PROMPT.format(
            assistant_name=settings.assistant_name,
            owner_name=settings.owner_name,
            portfolio_url=settings.portfolio_url,
            context=context or "No relevant context found.",
            history=history,
        )
        + f"\n\nUSER QUESTION: {state['user_query']}"
    )

    logger.info(
        "node respond: Gemini generate_content model=%s context_chunks=%d",
        settings.gemini_model,
        len(chunks),
    )
    gemini_response = model.generate_content(full_prompt)
    response_text = gemini_response.text

    sources = list(
        dict.fromkeys(
            c["metadata"].get("project_name") or "general"
            for c in chunks
        )
    )

    return {
        "response": response_text,
        "sources": sources,
        "messages": [AIMessage(content=response_text)],
    }


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────


def _parse_weekday_from_text(text: str) -> int | None:
    """Return 0=Monday … 6=Sunday from free text (full names + common abbrev)."""
    ql = text.lower()
    # Longer names first (e.g. "tuesday" before "day" issues)
    for name, idx in (
        ("wednesday", 2),
        ("thursday", 3),
        ("tuesday", 1),
        ("saturday", 5),
        ("sunday", 6),
        ("monday", 0),
        ("friday", 4),
    ):
        if name in ql:
            return idx
    m = re.search(
        r"\b(mon|tue|wed|thu|fri|sat|sun)\b",
        ql,
    )
    if m:
        abb = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        return abb.get(m.group(1))
    return None


def _parse_weekday_preference(state: AgentState) -> int | None:
    """Use latest user message plus recent user lines so follow-ups keep weekday context."""
    parts: list[str] = [state["user_query"]]
    for m in state.get("messages", [])[-8:]:
        if getattr(m, "type", "") == "human" and hasattr(m, "content"):
            parts.append(str(m.content))
    return _parse_weekday_from_text(" ".join(parts))


def _conversation_history_snippet(state: AgentState, max_chars: int = 2800) -> str:
    msgs = state.get("messages") or []
    lines: list[str] = []
    for m in msgs[-10:]:
        if not hasattr(m, "content"):
            continue
        role = "User" if getattr(m, "type", "") == "human" else "Assistant"
        lines.append(f"{role}: {str(m.content)[:700]}")
    out = "\n".join(lines)
    return out[:max_chars] if out else "(no prior turns)"


def _slot_facts_block(tool_out: dict) -> str:
    pool = tool_out.get("pool_size", 0)
    wf = tool_out.get("weekday_filter")
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    lines: list[str] = [
        f"Free slots found in scan (14 weekdays, 9:00–18:00 local): {pool}.",
    ]
    if wf is not None:
        lines.append(f"User asked specifically for: {names[wf]}.")
    lines.append("Slots offered in this reply (numbered 1..n in the UI):")
    sl = tool_out.get("slots") or []
    if not sl:
        lines.append("(none — no availability matching this reply’s filter)")
    else:
        lines.append(_format_slot_lines(sl))
    alts = tool_out.get("alternatives") or []
    if alts:
        lines.append("Alternative openings on other days (if primary filter empty):")
        lines.append(_format_slot_lines(alts))
    return "\n".join(lines)


def _deterministic_weekday_filtered_empty(tool_out: dict) -> str:
    """No Gemini — avoids inventing Friday slots when the user asked for Monday."""
    wf = tool_out.get("weekday_filter")
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if wf is None:
        return (
            f"No openings matched — try {settings.portfolio_url} or another weekday."
        )
    name = names[wf]
    alts = tool_out.get("alternatives") or []
    if not alts:
        return (
            f"I pulled {settings.owner_name}'s real calendar ({settings.calendar_timezone}) — "
            f"there's nothing open on **{name}** in the next ~two weeks during working hours "
            f"(weekdays 9:00–18:00).\n\n"
            f"Want another weekday, or should I show the next openings on any day?"
        )
    lines = _format_slot_lines(alts)
    return (
        f"I checked specifically for **{name}** — no free 30-minute windows that day in the range I scanned.\n\n"
        f"Here are his next openings on **other** days:\n\n{lines}\n\n"
        f"Reply with an option number and your email if one works."
    )


def _gemini_calendar_reply(state: AgentState, tool_out: dict) -> str:
    slots = tool_out.get("slots") or []
    prompt = CALENDAR_REPLY_PROMPT.format(
        assistant_name=settings.assistant_name,
        owner_name=settings.owner_name,
        tz_name=settings.calendar_timezone,
        user_query=state["user_query"],
        history=_conversation_history_snippet(state),
        slot_facts=_slot_facts_block(tool_out),
        portfolio_url=settings.portfolio_url,
    )
    try:
        out = model.generate_content(prompt)
        text = (out.text or "").strip()
        if text:
            return text
    except Exception:
        logger.exception("node respond: Gemini calendar reply failed; using fallback")

    lines = _format_slot_lines(slots)
    if not slots:
        return (
            f"No openings in that window — try {settings.portfolio_url} "
            f"or leave a note for {settings.owner_name}."
        )
    return (
        f"I checked his calendar ({settings.calendar_timezone}).\n\n{lines}\n\n"
        f"Tell me which slot and your email, and I’ll send the Meet invite."
    )


def _format_slot_lines(slots: list[dict]) -> str:
    """Human-readable numbered lines for up to five 30m slots."""
    if not slots:
        return "(no open slots in this window)"
    try:
        tz = ZoneInfo(settings.calendar_timezone)
    except Exception:
        tz = timezone.utc
    lines: list[str] = []
    for i, s in enumerate(slots[:5], 1):
        raw_start = s.get("start", "")
        raw_end = s.get("end", "")
        try:
            start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            ls = start.astimezone(tz)
            le = end.astimezone(tz)
            lines.append(
                f"{i}. {ls.strftime('%a %b %d')}, {ls.strftime('%H:%M')}–{le.strftime('%H:%M')}"
            )
        except Exception:
            lines.append(f"{i}. {raw_start}")
    return "\n".join(lines)


def _match_slot_to_availability(user_query: str, slots: list[dict]) -> dict | None:
    """Map user wording to a slot; None lets calendar_node fall back to first slot."""
    if not slots:
        return None
    q = user_query.lower()
    if any(x in q for x in ("first", "earliest", "soonest", "first slot", "option 1", "#1", "1.", " slot 1")):
        return slots[0]
    if len(slots) > 1 and any(x in q for x in ("second", "option 2", "#2", "2.", " slot 2")):
        return slots[1]
    if len(slots) > 2 and any(x in q for x in ("third", "option 3", "#3", "3.", " slot 3")):
        return slots[2]
    return None


def _extract_email(state: AgentState) -> str | None:
    import re
    all_text = " ".join(
        str(m.content) for m in state.get("messages", []) if hasattr(m, "content")
    )
    matches = re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", all_text)
    return matches[-1] if matches else None
