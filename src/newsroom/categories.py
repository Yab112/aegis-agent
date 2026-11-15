"""Blog categories and the rules that assign one, plus the format a keyword should be written in."""
from __future__ import annotations

import re
from typing import Any

# Order matters: the first category whose pattern matches wins, most specific first.
CATEGORIES: list[tuple[str, str, str]] = [
    ("safety-alignment", "Safety and alignment", r"alignment|safety|jailbreak|red.?team|reward hack|interpretab|misuse|deceptive|sabotage|guardrail"),
    ("benchmarks-evals", "Benchmarks and evals", r"benchmark|leaderboard|eval(uation)?s?\b|swe-bench|arena|elo\b|score[sd]?\b"),
    ("policy", "Policy and regulation", r"regulat|lawsuit|copyright|export control|senate|congress|\beu\b|ai act|court|ban\b|executive order|antitrust"),
    ("industry-funding", "Industry and funding", r"funding|raises|valuation|acqui|ipo\b|revenue|layoff|\bceo\b|hires?\b|investor|\$\d"),
    ("infrastructure-chips", "Infrastructure and chips", r"\bgpu|chip|nvidia|data ?cent|datacenter|inference|cluster|tsmc|compute|cuda|accelerator"),
    ("open-source", "Open source", r"open.?source|open.?weight|hugging ?face|github|apache|\bmit licen|llama|qwen|deepseek|mistral"),
    ("tools-agents", "Tools and agents", r"agent|copilot|\bide\b|coding|mcp\b|workflow|assistant|plugin|sdk\b|api\b"),
    ("research-papers", "Research and papers", r"arxiv|paper|study|researchers|novel|architecture|transformer|diffusion|training method"),
    ("model-releases", "Model releases", r"\bgpt|claude|gemini|grok|\bo\d\b|opus|sonnet|haiku|release|launch|preview|\bv?\d+(\.\d+)+"),
]
DEFAULT_CATEGORY = ("ai-news", "AI news")
EXTRA = [("learn", "Learn"), ("engineering", "Engineering")]


def category_labels() -> dict[str, str]:
    return {slug: label for slug, label, _ in CATEGORIES} | dict([DEFAULT_CATEGORY, *EXTRA])


def categorize(phrase: str, titles: list[str], source_keys: set[str]) -> str:
    text = " ".join([phrase, *titles[:12]]).lower()
    best, best_hits = DEFAULT_CATEGORY[0], 0
    for slug, _label, pattern in CATEGORIES:
        hits = len(re.findall(pattern, text))
        if hits > best_hits:
            best, best_hits = slug, hits
    if best_hits == 0 and any(k.startswith("arxiv") or k == "hf-papers" for k in source_keys):
        return "research-papers"
    return best


def suggest_format(signals: dict[str, Any]) -> tuple[str, str]:
    """news | analysis | explainer | course, with the reason in plain words."""
    fresh = signals.get("freshness", {}).get("score", 0)
    coverage = signals.get("coverage", {}).get("score", 0)
    search = signals.get("search", {})
    suggestions = len(search.get("suggestions") or [])
    if fresh >= 0.6 and coverage >= 0.35:
        return "news", "Fresh and covered by several trusted sources: write it as news while it is new."
    if suggestions >= 9 and search.get("score", 0) >= 0.7 and fresh < 0.25 and coverage >= 0.5:
        return "course", "People search many different questions around it: enough for a series of lessons."
    if search.get("score", 0) >= 0.45 and fresh < 0.6:
        return "explainer", "Steady search interest and no longer breaking: explain it properly."
    return "analysis", "Enough coverage to compare sources and take a position."
