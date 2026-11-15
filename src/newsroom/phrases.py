"""
Turn collected headlines into keyword candidates.

No model call: a phrase is a candidate when several trusted sources use it in a
headline within the window. That is cheap, explainable, and runs every few hours.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.newsroom.registry import FIREHOSE

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+'-]*[A-Za-z0-9+]|[A-Za-z0-9]")
_SPLIT = re.compile(r"\s[-–—|:]\s|[:;,()\[\]?!“”\"]|\s[-–—]\s")

STOP = set(
    "a an the and or but of in on at to for from by with without into over under about as is are was were be been "
    "this that these those it its their his her our your you we they he she i not no new how why what when where who "
    "lets let puts brings adds gets goes takes gives shows wants plans starts opens "
    "which will can could should would may might has have had do does did than then now more most just also via vs "
    "up out off all any some one two three first after before during amid says said say per get gets".split()
)
# Words that appear in every AI headline and say nothing on their own.
GENERIC = set(
    "ai model models llm llms launch launches launched release releases released announces announced announce introduces "
    "introducing update updates research paper papers report reports company companies startup startups data tool tools "
    "open source using use build building built learning machine deep language large agent agents system systems tech "
    "news blog today week year million billion users user team app apps platform future world make makes help helps "
    "better best top big next gen generation intelligence artificial work works way ways need needs want wants coming "
    "available access here inside against between across through while still back enterprise search video code "
    "training control reasoning business customers developers engineers product products feature features service "
    "services software hardware cloud security performance scale growth market industry people human humans time "
    "day days month months years free pro plus mode preview beta version support plan plans deal deals".split()
)
# Headline verbs: a phrase never starts or ends with one ("Gemini 4 launches").
VERBS = set(
    "launch launches launched release releases released announces announced announce introduces introducing unveils "
    "unveiled ships shipped debuts rolls arrives arrived expands raises raised wins hits adds added says update updates".split()
)


# Names of companies and product families. On their own they are far too broad to be a keyword
# ("NVIDIA", "Claude"); with a version or a second word they are exactly what people search for.
BRANDS = set(
    "openai anthropic google deepmind meta microsoft amazon aws nvidia apple xai mistral deepseek alibaba qwen "
    "hugging face huggingface github scale surge snorkel claude chatgpt gpt gemini grok llama copilot bedrock "
    "sagemaker azure tesla samsung intel amd oracle ibm reddit youtube tiktok x".split()
)


@dataclass
class Candidate:
    phrase: str  # lower-case key
    display: str
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def sources(self) -> set[str]:
        return {i["source_key"] for i in self.items}

    @property
    def strong_sources(self) -> set[str]:
        return self.sources - FIREHOSE


def _is_anchor(token: str) -> bool:
    """A token that names something: has a digit, inner capitals, or is a capitalised non-generic word."""
    low = token.lower()
    if low in STOP or low in GENERIC or len(token) < 2:
        return False
    if any(c.isdigit() for c in token):
        return len(token) > 2 or token.isalpha()
    return token[0].isupper() or any(c.isupper() for c in token[1:])


def title_phrases(title: str, max_len: int = 4) -> set[str]:
    """Every 1-4 word phrase in a headline that could be a search term (original casing)."""
    out: set[str] = set()
    title = re.sub(r"['’]s\b", "", title or "")  # "Google's" → "Google"
    for segment in _SPLIT.split(title):
        tokens = _TOKEN.findall(segment)
        for n in range(1, max_len + 1):
            for i in range(len(tokens) - n + 1):
                gram = tokens[i : i + n]
                first, last = gram[0].lower(), gram[-1].lower()
                if first in STOP or last in STOP or first in VERBS or last in VERBS or not any(_is_anchor(t) for t in gram):
                    continue
                lows = [t.lower() for t in gram]
                if all(t in BRANDS or t in STOP for t in lows):
                    continue  # a bare brand name is not a topic
                if n == 1 and not (any(c.isdigit() for c in gram[0]) and any(c.isalpha() for c in gram[0]) and len(gram[0]) >= 3):
                    continue  # single words only when they carry a version: "GPT-5.5", "o3-pro"
                if any(t in GENERIC for t in lows) and not any(t in BRANDS or any(c.isdigit() for c in t) for t in lows):
                    continue  # "Enterprise Search" says nothing; "Gemini Enterprise" does
                out.add(" ".join(gram))
    return out


def extract_candidates(items: list[dict[str, Any]], *, min_sources: int = 2, limit: int = 60) -> list[Candidate]:
    """
    Group headlines by shared phrase and keep phrases with real, independent coverage:
    at least `min_sources` different non-firehose sources.
    """
    by_phrase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    casing: dict[str, Counter[str]] = defaultdict(Counter)
    for item in items:
        for phrase in title_phrases(str(item.get("title") or "")):
            key = phrase.lower()
            by_phrase[key].append(item)
            casing[key][phrase] += 1

    candidates = [
        Candidate(phrase=key, display=casing[key].most_common(1)[0][0], items=rows)
        for key, rows in by_phrase.items()
        if len({r["source_key"] for r in rows} - FIREHOSE) >= min_sources
    ]

    # Prefer the most specific phrase: drop a shorter one when a longer one keeps most of its headlines.
    candidates.sort(key=lambda c: (-len(c.phrase.split()), -len(c.items)))
    kept: list[Candidate] = []
    for cand in candidates:
        ids = {i["url"] for i in cand.items}
        padded = f" {cand.phrase} "
        shadowed = any(
            padded in f" {k.phrase} " and len(ids & {i["url"] for i in k.items}) >= 0.8 * len(ids) for k in kept
        )
        if not shadowed:
            kept.append(cand)

    kept.sort(key=lambda c: (-len(c.strong_sources), -len(c.items)))
    return kept[:limit]
