from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.api.keyword_routes import topic_from_keyword
from src.newsroom import fetchers, service
from src.newsroom.categories import categorize, suggest_format
from src.newsroom.phrases import extract_candidates, title_phrases
from src.newsroom.registry import SEED_SOURCES
from src.newsroom.scoring import already_covered, coverage_signals, score_keyword

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def _item(source, title, hours_ago=5, tier=2):
    return {"source_key": source, "tier": tier, "title": title, "url": f"https://{source}.test/{abs(hash(title))}",
            "published_at": (NOW - timedelta(hours=hours_ago)).isoformat()}


def test_registry_is_consistent():
    keys = [s["key"] for s in SEED_SOURCES]
    assert len(keys) == len(set(keys)) >= 30
    for s in SEED_SOURCES:
        assert s["kind"] != "feed" or s["feed_url"], s["key"]
        assert not (s["enabled"] and s["kind"] == "reference")


def test_phrases_skip_bare_brands_and_generic_words():
    phrases = {p.lower() for p in title_phrases("Google's Gemini 4 Launches With New Enterprise Search Tools")}
    assert "gemini 4" in phrases and "google gemini 4" in phrases
    assert not phrases & {"google", "gemini", "enterprise search", "launches", "google s"}
    assert "gpt-5.5" in {p.lower() for p in title_phrases("OpenAI ships GPT-5.5")}
    assert "4.1" not in {p.lower() for p in title_phrases("Update 4.1 arrives")}


def test_candidates_need_two_independent_sources_and_prefer_specific_phrase():
    items = [
        _item("deepmind-blog", "Introducing Gemini 4 Hydrogen", tier=1),
        _item("ars-ai", "Gemini 4 Hydrogen is Google's new flagship"),
        _item("the-decoder", "Gemini 4 Carbon targets cheap inference"),
        _item("arxiv-cs-cl", "Sparse Widgets 9 for transformers", tier=3),
        _item("arxiv-cs-lg", "Sparse Widgets 9 revisited", tier=3),
        _item("verge-ai", "Only one outlet mentions Zeta 7"),
    ]
    found = {c.phrase: c for c in extract_candidates(items)}
    assert "gemini 4" in found and len(found["gemini 4"].strong_sources) == 3
    assert "gemini 4 hydrogen" in found
    assert "sparse widgets 9" not in found  # two firehose feeds are not independent coverage
    assert "zeta 7" not in found


def test_scoring_rewards_lab_coverage_and_freshness_and_penalises_repeats():
    fresh = [_item("openai-news", "x", 2, 1), _item("verge-ai", "x", 3), _item("ars-ai", "x", 4)]
    stale = [_item("verge-ai", "x", 200), _item("ars-ai", "x", 220)]
    s_fresh, s_stale = coverage_signals(fresh, NOW), coverage_signals(stale, NOW)
    assert s_fresh["coverage"]["score"] > s_stale["coverage"]["score"]
    assert s_fresh["freshness"]["score"] > 0.9 > 0.2 > s_stale["freshness"]["score"]
    assert s_fresh["momentum"]["last_48h"] == 3 and s_stale["momentum"]["last_48h"] == 0
    ext = {"search": {"score": 0.5}, "community": {"score": 0.5}}
    high, reason = score_keyword({**s_fresh, **ext})
    low, _ = score_keyword({**s_stale, **ext})
    cut, cut_reason = score_keyword({**s_fresh, **ext}, covered_by="Gemini 4: what changed")
    assert high > low and abs(cut - high * 0.25) < 0.11 and "Already covered" in cut_reason and "Strongest" in reason
    assert already_covered("gemini 4", ["What Gemini 4 changes for agents"]) and not already_covered("gemini 4", ["Gemini 3 review"])


def test_category_and_format_rules():
    assert categorize("gemini 4", ["Google launches Gemini 4 preview"], {"verge-ai"}) == "model-releases"
    assert categorize("reward hacking", ["New alignment paper on reward hacking and safety"], {"anthropic-alignment"}) == "safety-alignment"
    assert categorize("xyz", ["nothing matches"], {"arxiv-cs-cl"}) == "research-papers"
    assert suggest_format({"freshness": {"score": 0.9}, "coverage": {"score": 0.5}})[0] == "news"
    assert suggest_format({"freshness": {"score": 0.1}, "coverage": {"score": 0.6}, "search": {"score": 0.8, "suggestions": ["a"] * 9}})[0] == "course"
    assert suggest_format({"freshness": {"score": 0.3}, "coverage": {"score": 0.2}, "search": {"score": 0.5, "suggestions": []}})[0] == "explainer"


class _Store:
    def __init__(self, items, existing=None):
        self.items, self.existing, self.saved = items, existing or {}, []

    def recent_items(self, days=7):
        return self.items

    def existing_keywords(self):
        return self.existing

    def recent_post_titles(self):
        return []

    def upsert_keywords(self, rows):
        self.saved = rows


def test_refresh_reuses_recent_outside_signals_and_keeps_your_decisions(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "external_signals", lambda phrase, **_k: calls.append(phrase) or {"search": {"score": 0.4, "suggestions": []}, "community": {"score": 0.2}, "external_at": service.now_iso()})
    items = [_item("deepmind-blog", "Gemini 4 arrives", 2, 1), _item("ars-ai", "Gemini 4 tested", 3),
             _item("openai-news", "ChatGPT Wallet opens", 2, 1), _item("verge-ai", "ChatGPT Wallet hands-on", 2)]
    cached = {"chatgpt wallet": {"status": "dismissed", "signals": {"search": {"score": 0.9, "suggestions": []}, "community": {"score": 0.9}, "external_at": service.now_iso()}}}
    store = _Store(items, cached)
    out = service.refresh_keywords(store, pause=0)
    rows = {r["phrase"]: r for r in store.saved}
    assert calls == ["gemini 4"] and out["external_lookups"] == 1
    assert rows["chatgpt wallet"]["status"] == "dismissed" and rows["chatgpt wallet"]["signals"]["search"]["score"] == 0.9
    assert "status" not in rows["gemini 4"] and rows["gemini 4"]["sources"][0]["tier"] == 1
    assert rows["gemini 4"]["category"] == "model-releases" and rows["gemini 4"]["suggested_format"] == "news"

    topic = topic_from_keyword({**rows["gemini 4"], "id": "k1"})
    assert topic["keyword"] == "Gemini 4" and topic["format"] == "news" and topic["source_url"] == rows["gemini 4"]["sources"][0]["url"]
    assert "Gemini 4" in topic["title"] and len(topic["sources"]) == 2


def test_page_scan_keeps_matching_links_only(monkeypatch):
    html = """<a href="/news/claude-ships-thing"><h3>Claude ships a thing today</h3></a>
              <a href="/news/claude-ships-thing">Read</a><a href="/careers">Careers at the company</a>
              <a href="https://other.site/news/not-ours-at-all">Someone else's article here</a>"""
    monkeypatch.setattr(fetchers, "_get", lambda url, timeout=25: type("R", (), {"text": html})())
    src = {"key": "anthropic-news", "kind": "page", "url": "https://www.anthropic.com/news", "link_pattern": r"^/news/[a-z0-9-]+$"}
    items = fetchers.fetch_source(src)
    assert [(i["title"], i["url"]) for i in items] == [("Claude ships a thing today", "https://www.anthropic.com/news/claude-ships-thing")]
