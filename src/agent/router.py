"""
Router node: classifies the user query into one of three intents
and extracts any metadata filters for RAG.
"""
import json
import logging
import re
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai
from config.settings import get_settings
from src.agent.state import AgentState

logger = logging.getLogger("aegis.router")
settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel(settings.gemini_model)

ROUTER_PROMPT = """You route messages for the portfolio chat. The on-screen assistant is named {assistant_name}; you only classify intent, you do not chat.

Classify the user message into exactly one intent:
- "general_qa"   — greetings (hi, hello, what's up), small talk, questions about Yabibal,
                   skills, stack, projects, background, education, hobbies, how to hire,
                   links (GitHub, Upwork), or anything answerable from a portfolio site
- "book_meeting" — wants to schedule a call, meeting, demo, video chat, or consultation
- "handoff"      — explicit negotiation: rates, salary, contract terms, quotes, invoices,
                   or sensitive commercial terms that need Yabibal directly

Casual greetings and "how are you" style messages MUST be "general_qa", never "handoff".

When intent is "book_meeting", you MUST also set "book_stage":
- "availability" — user is asking when Yabibal is free, what times work, or is exploring booking
  without committing (e.g. "when is he available?", "any slots this week?", "can we book a call?").
- "schedule" — user is ready to lock in a meeting: they gave an email, picked a specific time/slot,
  or explicitly says to book/confirm/schedule/send the invite now (e.g. "book the first slot",
  "Tuesday 9am works", "my email is x@y.com — schedule it").

If intent is not "book_meeting", set "book_stage" to null.

Known project filters (optional): car_rental_app, cli_tool, portfolio_site, general

Recent conversation (newest user line is the main signal):
{conversation}

Respond ONLY with valid JSON, no markdown, no explanation:
{{
  "intent": "<general_qa|book_meeting|handoff>",
  "project_filter": "<project_name or null>",
  "book_stage": "<availability|schedule|null>",
  "reason": "<one sentence>"
}}

Latest user message: {query}
"""

# Trigger phrases that force a handoff regardless of LLM classification
# Substring match on lowered query — keep short tokens precise to avoid false positives (e.g. "strategy").
HANDOFF_TRIGGERS = [
    "rate", "rates", "price", "pricing", "cost", "contract",
    "hire", "salary", "budget", "quote", "how much", "invoice",
    "retainer", "full-time", "part-time",
    "payment", "payments", "milestone", "deposit", "paid upfront",
    "paypal", "stripe", "wire transfer", "escrow",
]

# Extra tokens — only used with _thread_touched_commercial_topic (full thread scan)
_HANDOFF_TOPIC_EXTRA = [
    "fullstack",
    "full stack",
    "full-time",
    "monthly",
    "remote",
    "developer",
]


def _email_present(text: str) -> bool:
    return bool(re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", text))


def _force_schedule_if_commit_message(state: AgentState) -> bool:
    """If user gave an email + booking intent, treat as schedule even if the LLM wavers."""
    q = state["user_query"].lower()
    if not _email_present(state["user_query"]):
        return False
    commit = (
        "book", "schedule", "confirm", "invite", "lock", "option", "slot",
        "first", "second", "third", "yes", "that time", "this slot",
    )
    return any(c in q for c in commit)


def _should_route_calendar_availability(user_message: str) -> bool:
    """
    If the LLM labels this as general_qa, we would hit RAG and Gemini may *invent*
    calendar slots. Real availability must go through the Calendar API.
    """
    ql = user_message.lower()
    days = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    if not any(d in ql for d in days):
        return False
    hints = (
        "available",
        "availability",
        "free",
        "slot",
        "slots",
        "meet",
        "meeting",
        "call",
        "book",
        "schedule",
        "calendar",
        "when",
        "time",
        "open",
        "next ",
    )
    return any(h in ql for h in hints)


def _thread_touched_commercial_topic(state: AgentState) -> bool:
    """True if salary/rates/etc. appeared earlier in this session (loaded messages + current)."""
    blob_parts: list[str] = []
    for m in state.get("messages", [])[-24:]:
        if hasattr(m, "content"):
            blob_parts.append(str(m.content).lower())
    blob = " ".join(blob_parts)
    markers = [t.lower() for t in HANDOFF_TRIGGERS] + _HANDOFF_TOPIC_EXTRA
    return any(m in blob for m in markers)


def _is_short_handoff_followup(user_message: str) -> bool:
    """
    Follow-ups like 'will he respond?' don't repeat salary keywords — the router LLM
    often classifies them as general_qa and RAG *hallucinates* the old handoff blurb.
    """
    ql = user_message.lower().strip()
    if len(ql) > 240:
        return False
    needles = (
        "will he",
        "will she",
        "will they",
        "are you sure",
        "really",
        "respond",
        "reply",
        "get back",
        "follow up",
        "follow-up",
        "is that true",
        "for real",
        "serious",
        "confirm",
    )
    return any(n in ql for n in needles)


def _conversation_snippet(state: AgentState, max_chars: int = 1200) -> str:
    msgs = state.get("messages") or []
    lines: list[str] = []
    for m in msgs[-8:]:
        if not hasattr(m, "content"):
            continue
        role = "User" if getattr(m, "type", "") == "human" else "Assistant"
        text = str(m.content)[:500]
        lines.append(f"{role}: {text}")
    out = "\n".join(lines)
    return out[:max_chars] if out else "(no prior messages)"


def router_node(state: AgentState) -> dict:
    query = state["user_query"].lower()

    # Hard-coded trigger check first (faster + more reliable than LLM)
    if any(trigger in query for trigger in HANDOFF_TRIGGERS):
        logger.info(
            "router: handoff (keyword trigger) model=%s",
            settings.gemini_model,
        )
        return {
            "intent": "handoff",
            "book_stage": None,
            "metadata_filter": None,
            "should_handoff": True,
            "iterations": state.get("iterations", 0) + 1,
        }

    if _thread_touched_commercial_topic(state) and _is_short_handoff_followup(
        state["user_query"]
    ):
        logger.info(
            "router: handoff (follow-up after commercial thread) — skip router LLM",
        )
        return {
            "intent": "handoff",
            "book_stage": None,
            "metadata_filter": None,
            "should_handoff": True,
            "iterations": state.get("iterations", 0) + 1,
        }

    if _should_route_calendar_availability(state["user_query"]):
        logger.info(
            "router: book_meeting (weekday + calendar cue) — skip router LLM, use Calendar API",
        )
        return {
            "intent": "book_meeting",
            "book_stage": "availability",
            "metadata_filter": None,
            "should_handoff": False,
            "iterations": state.get("iterations", 0) + 1,
        }

    prompt = ROUTER_PROMPT.format(
        assistant_name=settings.assistant_name,
        conversation=_conversation_snippet(state),
        query=state["user_query"],
    )
    logger.info("router: calling Gemini (generate_content) model=%s", settings.gemini_model)
    response = model.generate_content(prompt)

    try:
        result = json.loads(response.text.strip())
    except json.JSONDecodeError:
        logger.warning("router: Gemini returned non-JSON; defaulting to general_qa")
        result = {"intent": "general_qa", "project_filter": None, "book_stage": None}

    intent = result.get("intent", "general_qa")
    # LLM often mislabels "is he free Monday?" as general_qa → RAG hallucinates slots.
    if intent == "general_qa" and _should_route_calendar_availability(state["user_query"]):
        logger.info("router: override general_qa → book_meeting (weekday + calendar cue)")
        intent = "book_meeting"
    # Follow-ups ("will he respond?") lack salary keywords → general_qa → RAG parrots an old handoff blurb.
    if (
        intent == "general_qa"
        and _thread_touched_commercial_topic(state)
        and _is_short_handoff_followup(state["user_query"])
    ):
        logger.info("router: override general_qa → handoff (commercial thread follow-up)")
        intent = "handoff"

    raw_stage = result.get("book_stage")
    if intent == "book_meeting":
        book_stage = (raw_stage or "availability").lower()
        if book_stage not in ("availability", "schedule"):
            book_stage = "availability"
        if _force_schedule_if_commit_message(state):
            book_stage = "schedule"
    else:
        book_stage = None

    logger.info(
        "router: intent=%s book_stage=%s project_filter=%s should_handoff=%s",
        intent,
        book_stage,
        result.get("project_filter"),
        intent == "handoff",
    )

    metadata_filter = {}
    if result.get("project_filter"):
        metadata_filter["project_name"] = result["project_filter"]

    return {
        "intent": intent,
        "book_stage": book_stage,
        "metadata_filter": metadata_filter or None,
        "should_handoff": intent == "handoff",
        "iterations": state.get("iterations", 0) + 1,
    }
