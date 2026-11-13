"""Who is on the other end of the chat: a person, an AI agent acting for a person, or a crawler.

Deterministic on purpose (no model call): it runs on every request, and the
result changes how the agent answers and how a handoff is described to the owner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# (pattern in the User-Agent, display name, kind)
_UA_RULES: tuple[tuple[str, str, str], ...] = (
    ("chatgpt-user", "ChatGPT", "ai_agent"),
    ("oai-searchbot", "ChatGPT Search", "ai_agent"),
    ("gptbot", "OpenAI GPTBot", "crawler"),
    ("claude-user", "Claude", "ai_agent"),
    ("claude-searchbot", "Claude Search", "ai_agent"),
    ("claudebot", "Anthropic ClaudeBot", "crawler"),
    ("anthropic-ai", "Anthropic", "crawler"),
    ("perplexity-user", "Perplexity", "ai_agent"),
    ("perplexitybot", "PerplexityBot", "crawler"),
    ("google-extended", "Google AI", "crawler"),
    ("gemini", "Gemini", "ai_agent"),
    ("duckassistbot", "DuckAssist", "ai_agent"),
    ("mistralai-user", "Mistral Le Chat", "ai_agent"),
    ("meta-externalagent", "Meta AI", "crawler"),
    ("cohere-ai", "Cohere", "crawler"),
    ("bytespider", "Bytespider", "crawler"),
    ("amazonbot", "Amazonbot", "crawler"),
    ("applebot", "Applebot", "crawler"),
    ("ccbot", "Common Crawl", "crawler"),
    ("googlebot", "Googlebot", "crawler"),
    ("bingbot", "Bingbot", "crawler"),
    ("langchain", "LangChain agent", "ai_agent"),
    ("llamaindex", "LlamaIndex agent", "ai_agent"),
    ("browser-use", "browser-use agent", "ai_agent"),
    ("playwright", "automated browser", "ai_agent"),
    ("headlesschrome", "headless browser", "ai_agent"),
    ("python-requests", "script (python-requests)", "ai_agent"),
    ("python-httpx", "script (httpx)", "ai_agent"),
    ("aiohttp", "script (aiohttp)", "ai_agent"),
    ("node-fetch", "script (node-fetch)", "ai_agent"),
    ("axios/", "script (axios)", "ai_agent"),
    ("go-http-client", "script (Go)", "ai_agent"),
    ("curl/", "script (curl)", "ai_agent"),
)

# Phrases an assistant uses when it is relaying for someone.
_SELF_DECLARED = re.compile(
    r"\b("
    r"i am an? (ai|llm|language model|automated|virtual) ?(assistant|agent|model)?"
    r"|i'?m an? (ai|llm|automated|virtual) (assistant|agent)"
    r"|as an ai (assistant|agent)"
    r"|on behalf of (my|the|a) (user|client|principal|employer)"
    r"|my user (is|wants|asked|would like)"
    r"|i am (acting|researching|contacting you) (for|on behalf of)"
    r"|(respond|reply|answer|return) (only )?(in|with|as) (valid )?json"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VisitorProfile:
    kind: str = "human"  # "human" | "ai_agent" | "crawler"
    agent_name: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_automated(self) -> bool:
        return self.kind != "human"

    def describe(self) -> str:
        if self.kind == "ai_agent":
            return f"AI agent ({self.agent_name or 'unidentified'}), likely acting for a person"
        if self.kind == "crawler":
            return f"Crawler ({self.agent_name or 'unidentified'})"
        return "Human visitor"


def classify_visitor(user_agent: str | None, message: str | None = None) -> VisitorProfile:
    ua = (user_agent or "").lower()
    reasons: list[str] = []
    kind = "human"
    name: str | None = None

    for needle, label, rule_kind in _UA_RULES:
        if needle in ua:
            kind, name = rule_kind, label
            reasons.append(f"user-agent contains {needle!r}")
            break

    if message and _SELF_DECLARED.search(message):
        reasons.append("message says it is an AI or is acting for someone")
        if kind == "human":
            kind, name = "ai_agent", "self-declared assistant"
        elif kind == "crawler":
            kind = "ai_agent"  # a crawler does not hold a conversation

    if not ua and kind == "human" and message is not None:
        reasons.append("no user-agent header")

    return VisitorProfile(kind=kind, agent_name=name, reasons=tuple(reasons))


AI_AGENT_STYLE = (
    "\n\nAUDIENCE: The reader is an AI agent gathering facts for a person, not a person browsing. "
    "Answer in compact, literal, self-contained statements: no greetings, no rhetorical questions, "
    "no emoji. Give exact names, numbers, dates and full URLs from the context. "
    "If the context does not contain a fact, say it is not available rather than guessing. "
    "End with one line naming the best next step for the agent's user "
    "(for example the contact page or booking a call)."
)
