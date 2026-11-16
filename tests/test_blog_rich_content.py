from __future__ import annotations

import json

from src.blog.prompts import FORMATS, KEYWORD_RULE, WRITE_BODY_PROMPT
from src.blog.research import ResearchPack, Source, is_public_http_url
from src.blog.rich_content import drop_advice_sections, normalize_chart, normalize_url, process_body, prose_word_count

SRC = "https://example.org/report"


def _block(kind: str, payload: dict) -> str:
    return f"```{kind}\n{json.dumps(payload)}\n```"


def test_body_prompt_formats_and_keeps_json_examples():
    for name, fmt in FORMATS.items():
        out = WRITE_BODY_PROMPT.format(
            owner_name="Y", portfolio_url="https://y.site", title="T", angle="A", tags="x", sources="[S1] a",
            structure=fmt["structure"], keyword_rule=KEYWORD_RULE.format(keyword="gemini 4"),
        )
        assert '{"type": "line"' in out and "[S1] a" in out and "[^1]" in out, name
        assert '"gemini 4"' in out and "{+an improvement" in out and "==the one number" in out
        assert "Do not tell the reader what to do" in out and "one independent developer" in out
        # no format may ask for an advice section (a course teaches, but still has no "what to do next")
        assert "what to do next" not in str(fmt["structure"]).lower() and "go deeper" not in str(fmt["structure"]).lower()


def test_sourced_chart_kept_only_when_source_is_in_pack():
    chart = {
        "type": "line", "title": "Latency", "x": ["a", "b", "c"],
        "series": [{"name": "p95", "data": ["1,200", 3, 4.5]}],
        "basis": "sourced", "source": {"label": "Report", "url": SRC + "/"},
    }
    spec, note = normalize_chart(chart, {normalize_url(SRC)})
    assert spec and spec["basis"] == "sourced" and spec["series"][0]["data"] == [1200, 3, 4.5] and not note

    spec, note = normalize_chart(chart, set())
    assert spec and spec["basis"] == "illustrative" and "source" not in spec and note


def test_invalid_charts_are_dropped():
    bad = [
        {"type": "pie", "title": "x", "x": [1, 2], "series": [{"data": [1, 2]}]},
        {"type": "bar", "title": "x", "x": [1, 2], "series": [{"data": [1]}]},
        {"type": "bar", "title": "x", "x": [1, 2], "series": [{"data": [1, "n/a"]}]},
        {"type": "bar", "x": [1, 2], "series": [{"data": [1, 2]}]},
        {"type": "line", "title": "x", "x": [1, 2], "series": [{"data": [1, 2]}] * 5},
    ]
    for chart in bad:
        assert normalize_chart(chart, set())[0] is None
    body, report = process_body("## A\n\ntext\n\n```chart\n{not json}\n```\n\nmore", allowed_urls=[])
    assert "```chart" not in body and report.charts_dropped == 1


def test_figures_need_a_resolved_image_with_source():
    md = "## A\n\n" + _block("figure", {"query": "server rack", "caption": "A rack."})
    body, report = process_body(md, allowed_urls=[], figure_resolver=lambda raw: None)
    assert "```figure" not in body and report.figures_dropped == 1

    found = {"src": "https://upload.wikimedia.org/x.jpg", "source_url": "https://commons.wikimedia.org/wiki/File:x.jpg",
             "credit": "Jane", "license": "CC BY-SA 4.0", "width": 1400, "height": 900}
    body, report = process_body(md, allowed_urls=[], figure_resolver=lambda raw: found)
    fig = json.loads(body.split("```figure\n")[1].split("\n```")[0])
    assert report.figures == 1 and fig["credit"] == "Jane" and fig["caption"] == "A rack." and fig["alt"] == "A rack."


def test_links_outside_the_pack_must_answer_or_lose_the_link():
    md = (
        "## A\n\nSee [the report](https://example.org/report) and [a guess](https://nope.invalid/x).[^1][^2]\n\n"
        "```python\nx = '[keep](https://nope.invalid/code)'\n```\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "[^1]: Stated in [the report](https://example.org/report).\n"
    )
    body, report = process_body(md, allowed_urls=[SRC], url_checker=lambda url: False)
    assert "[the report](https://example.org/report)" in body
    assert "[a guess]" not in body and "a guess" in body
    assert "[keep](https://nope.invalid/code)" in body  # code is never rewritten
    assert report.links_unlinked == 1 and report.footnotes == 1 and report.tables == 1
    assert "[^2]" not in body  # reference without a definition is removed


def test_word_count_ignores_fenced_blocks():
    md = "one two three\n\n```chart\n" + json.dumps({"a": "many words in here that do not count"}) + "\n```\n\nfour"
    assert prose_word_count(md) == 4


def test_research_pack_dedupes_and_blocks_private_hosts():
    pack = ResearchPack()
    pack.add(Source("A", "https://www.example.org/report/"))
    pack.add(Source("B", "https://example.org/report"))
    assert len(pack.sources) == 1 and "[S1] A" in pack.prompt_block()
    assert not is_public_http_url("http://127.0.0.1/x")
    assert not is_public_http_url("http://169.254.169.254/latest/meta-data")
    assert not is_public_http_url("file:///etc/passwd")


def test_advice_and_wrap_up_sections_are_removed_but_footnotes_survive():
    md = (
        "## Gemini 4 Carbon costs more to run\n\nBody.[^1]\n\n"
        "```python\n## What to do next (inside code, must stay)\n```\n\n"
        "## What we still do not know\n\nOpen questions.\n\n"
        "## What to Do Next\n\n- Audit your evals\n- Add holdouts\n\n"
        "## Key takeaways\n\nShort recap.\n\n"
        "[^1]: Stated by [DeepMind](https://deepmind.google/x).\n"
    )
    body, removed = drop_advice_sections(md)
    assert removed == ["What to Do Next", "Key takeaways"]
    assert "Audit your evals" not in body and "Short recap" not in body
    assert "What we still do not know" in body and "inside code, must stay" in body
    assert "[^1]: Stated by [DeepMind]" in body


def test_x_posts_are_quoted_only_when_fetched_and_never_reworded():
    from src.blog.social import extract_post_urls

    page = '<a href="https://twitter.com/OpenAI/status/1790072174117613963?s=20">x</a> <a href="https://x.com/intent/status/123456">y</a>'
    assert extract_post_urls(page) == ["https://x.com/OpenAI/status/1790072174117613963"]
    real = {"url": "https://x.com/OpenAI/status/1790072174117613963", "author": "OpenAI", "handle": "OpenAI", "text": "Say hello to GPT-4o", "date": "May 13, 2024"}
    md = (
        "## A\n\nThe lab announced it on X:\n\n"
        + _block("xpost", {"url": "https://twitter.com/OpenAI/status/1790072174117613963/", "text": "words the model made up"})
        + "\n\n" + _block("xpost", {"url": "https://x.com/someone/status/999999999"})
    )
    body, report = process_body(md, allowed_urls=[], x_posts=[real])
    assert report.x_posts == 1 and body.count("```xpost") == 1
    quoted = json.loads(body.split("```xpost\n")[1].split("\n```")[0])
    assert quoted == real and "made up" not in body and "someone/status" not in body
