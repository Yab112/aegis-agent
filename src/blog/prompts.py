"""Prompts for the blog writer. Kept apart so wording changes never touch pipeline logic."""

IDEA_PROMPT = """You suggest ONE technical blog post idea for {owner_name}'s site ({portfolio_url}).
Focus areas: full-stack engineering, AI/ML in production, automation, developer tooling.

Rules:
- Propose something useful and specific, not generic fluff.
- Do NOT claim real-time news, product release dates, or statistics you cannot verify.
- topic_key must be a stable snake_case identifier (e.g. rag_chunking_strategies), unique to this angle.

Respond with ONLY valid JSON (no markdown fences):
{{"title": "string", "topic_key": "string", "angle": "one sentence", "tags": ["tag1", "tag2"]}}
"""

WRITE_META_PROMPT = """You plan a technical blog post for {owner_name}'s site ({portfolio_url}).

Topic: {title}
Angle: {angle}
Suggested tags: {tags}

Return ONLY a small JSON object (no markdown fences, no body text here):
- slug: kebab-case URL segment
- title: final headline (plain text, no line breaks)
- description: one line for SEO/cards, max 200 characters, no line breaks or double quotes inside
- tags: array of lowercase strings (max 12)

Example shape:
{{"slug": "my-topic-slug", "title": "My Title", "description": "One line only.", "tags": ["tag1"]}}
"""

WRITE_BODY_PROMPT = """You write the ARTICLE ONLY for {owner_name}'s technical blog ({portfolio_url}).
The standard is a long-form research post: organised, evidenced, and honest about uncertainty.

Planned title: {title}
Angle: {angle}
Tags (use naturally if they fit): {tags}
{keyword_rule}
SOURCES — the only pages you may cite or link to:
{sources}

STRUCTURE
{structure}

EVIDENCE
- Every factual claim about a product, company, date, benchmark or statistic needs a footnote:
  write `[^1]` in the sentence and define it at the very end as
  `[^1]: What the source says. [Source title](URL)` using a URL from SOURCES, character for character.
- Never cite or link a URL that is not in SOURCES. If SOURCES do not support a claim, leave it out.
- Attribute in the sentence itself, not only in the footnote: "OpenAI says", "according to The Verge",
  "the paper reports". The reader must always know who is speaking.
- No opinion or prediction of your own in the body. If the format has a closing section
  (`## Why it matters`, `## What to do next`), keep it to what the sources support and
  phrase it as reasoning from them ("this suggests", "the numbers point to").
- Give estimates as ranges and say how confident you are. Name disagreements between sources.

RICH BLOCKS — use them where they explain something better than prose
1. Chart (1 to 3 per post). A fenced block tagged `chart` holding JSON:
```chart
{{"type": "line", "title": "What the chart shows", "x": ["2021", "2022", "2023"],
  "series": [{{"name": "Series A", "data": [12, 19, 31]}}],
  "x_label": "Year", "y_label": "Requests", "unit": "ms",
  "caption": "One sentence telling the reader what to notice.",
  "basis": "sourced", "source": {{"label": "Source title", "url": "URL from SOURCES"}}}}
```
   - type is one of line, area, bar, scatter. Scatter has no "x"; its data is [[x, y], ...].
   - At most 4 series and 60 points. Every series has exactly as many values as "x". One unit per chart.
   - "basis": "sourced" ONLY when every value comes from a page in SOURCES. For a conceptual or
     back-of-envelope chart use "basis": "illustrative" and omit "source"; the site labels it as such.
2. Table (at least one). A normal Markdown table with a header row, for comparisons and trade-offs.
   Put a blank line before and after it.
3. Diagram (optional). A fenced block tagged `mermaid` with a small flowchart or sequence diagram
   (under 15 nodes, plain labels without parentheses or quotes).
4. Figure (0 to 2). A fenced block tagged `figure` holding JSON with a search query for a real,
   freely licensed photo or diagram; the pipeline finds the image and adds the credit and licence:
```figure
{{"query": "specific searchable subject", "caption": "What the image shows and why it is here.", "alt": "Plain description"}}
```
   Only ask for something that plausibly exists as a photograph or published diagram.
5. Callout (optional): a blockquote whose first line is `> [!KEY]`, `> [!NOTE]` or `> [!WARNING]`.
6. Emphasis colours, used sparingly (at most 6 in a post):
   - `==the one number or phrase a skimmer must not miss==` (highlight)
   - `{{+an improvement, e.g. 38% faster+}}` and `{{-a regression or risk, e.g. 2x the cost-}}`
7. Code: fence with the language, and name the file when it helps: ```python title="retry.py"
8. Emoji: at most one at the start of a list item or callout line where it carries meaning
   (✅ ⚠️ 📈 🧪). Never in headings and never inside a sentence.

OUTPUT RULES
- Output ONLY the Markdown body. No JSON wrapper, no YAML frontmatter, no title line.
- First line must be `{first_heading}`.
- Short code blocks are welcome when the topic is code.
- Voice: a clear, neutral reporter. Never "I", "we", "my" or "our": this post is assembled from
  other people's reporting, not from personal experience. Plain words, no hype.
- Do not wrap the whole article in a code fence. No preamble.
"""

FIGURE_PICK_PROMPT = """Pick an image for a blog figure.

Figure caption: {caption}
Search query: {query}

Candidates:
{candidates}

Reply with ONLY the number of the candidate that clearly shows what the caption describes,
or 0 if none of them does. A wrong image is worse than no image.
"""

IMAGE_PROMPT_PROMPT = """You write visual direction for a technical blog cover image.

Blog title: {title}
Description: {description}
Angle: {angle}
Tags: {tags}

Return ONLY valid JSON (no markdown fences):
{{
    "image_prompt": "A concise, vivid prompt for an AI image model (no text in image, no logos, no watermarks).",
    "image_alt": "A short alt text sentence describing the generated cover image."
}}
"""


# One outline per format. `first` is the heading the body must open with; `min_words` feeds the quality gate.
FORMATS: dict[str, dict[str, object]] = {
    "analysis": {
        "first": "## Introduction",
        "min_words": 900,
        "structure": """- Start with `## Introduction`: what prompted the post, then the thesis as ONE bold sentence,
  then a short roadmap of the sections that follow.
- 4 to 7 further `##` sections, with `###` sub-sections where a section has distinct parts.
  Open a section with its claim, then support it.
- Include a section on limits, trade-offs or where this advice fails.
- End with a short `## What to watch next` section: the open questions and upcoming dates the sources mention.
  No "in conclusion" summary.
- 1400 to 2200 words of prose. Paragraphs of 3 to 6 sentences.""",
    },
    "news": {
        "first": "## What happened",
        "min_words": 280,
        "structure": """- A news brief: 350 to 650 words. Facts only, each attributed to its source.
- `## What happened`: two or three sentences a reader could repeat, with the date and who announced it.
  Follow it with a `> [!KEY]` callout of 2 to 4 bullet facts.
- `## The details`: the numbers, prices, limits and dates, as a table when there are three or more.
- `## Why it matters`: who this changes things for, and how.
- `## What we do not know yet`: open questions and anything only one source claims.
- Attribute every fact to who said it ("OpenAI says", "according to The Verge").""",
    },
    "explainer": {
        "first": "## The short version",
        "min_words": 700,
        "structure": """- An explainer for an engineer who has not followed this topic. 1000 to 1600 words.
- `## The short version`: the idea in three sentences, then ONE bold sentence with the takeaway.
- `## How it works`: build it up step by step, with a diagram or a small worked example.
- `## Where it is used` and `## Common misunderstandings` (a list of myth, then what is true).
- `## Trade-offs`: a table. `## Go deeper`: what to read or try next.""",
    },
    "course": {
        "first": "## What you will learn",
        "min_words": 1200,
        "structure": """- A self-contained lesson page. 1800 to 2800 words.
- `## What you will learn`: 3 to 5 outcomes as a list, then `## Before you start` (what to know first).
- 3 to 5 lessons, each `## Lesson N: title`, each with: the idea, a worked example (code or numbers),
  and a `### Check yourself` list of 2 questions with short answers.
- `## Put it together`: one exercise that uses every lesson.
- `## Where to go next`: further reading from SOURCES.""",
    },
}

KEYWORD_RULE = """Target search phrase: "{keyword}"
- Use this exact phrase in the first paragraph and in one `##` heading, where it reads naturally.
- Never repeat it mechanically; a reader should not notice it was targeted.
"""
