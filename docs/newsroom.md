# AI news desk: sources, keywords, formats

## Setup
1. Supabase → SQL Editor: run `scripts/supabase_newsroom.sql`.
2. Deploy. The scheduler then runs two new jobs:
   - `news_collect` every 3 hours: reads every enabled source.
   - `keyword_refresh` every 6 hours: scores the topics sources share.
   Override with `SCHEDULER_NEWS_COLLECT_CRON` / `SCHEDULER_KEYWORD_REFRESH_CRON`.

## Code map (`src/newsroom/`, every file under 500 lines)
| File | Job |
|---|---|
| `registry.py` | Seed list of trusted sources with tier and how each is read |
| `fetchers.py` | Read one source: feed, page scan or JSON |
| `collector.py` | Read all sources, record status per source |
| `store.py` | All Supabase access |
| `phrases.py` | Headlines → keyword candidates (no model call) |
| `signals/search.py` | Google suggestions, Google Trends, Wikipedia views |
| `signals/community.py` | Hacker News, GitHub, Hugging Face |
| `scoring.py` | Coverage, momentum, freshness, and the 0-100 score |
| `categories.py` | Category rules and the suggested format |
| `service.py` | The refresh run, and on-demand analysis of any phrase |

## Score
`100 × (0.28 coverage + 0.25 search + 0.18 freshness + 0.17 momentum + 0.12 community)`,
cut to a quarter when a recent post already targets the phrase. Weights live in `scoring.py`.
A candidate needs two independent sources; arXiv and GitHub Trending alone do not count.

## Keyword API (API secret required)
`GET /keywords/best?limit=&category=&status=` · `GET /keywords/analyze?q=` · `GET /keywords/{id}`
· `GET /keywords/{id}/sources` · `POST /keywords/{id}/write` · `POST /keywords/{id}/dismiss` · `POST /keywords/refresh`

Sources admin: `GET|POST /newsroom/sources` · `PATCH /newsroom/sources/{key}` · `GET /newsroom/items` · `POST /newsroom/collect`

Public: `GET /blog/categories`; `/blog` and `/blog/{slug}` now return `category`, `category_label`, `format`.

## Formats
`news`, `analysis`, `explainer`, `course`: outlines and minimum lengths are in `src/blog/prompts.py` (`FORMATS`).
A post written from a keyword stores `category`, `keyword` and `format`, and is researched from the
keyword's trusted pages plus grounded search.

## Known limits (checked 2026-11-15)
- Reuters and Scale SEAL Research refuse automated readers; VentureBeat rate-limited the test read.
- DeepSeek news and Surge AI blog render their lists with script, so the page scan finds nothing.
- Reddit refuses automated readers, so it is not a signal.
- Google Trends and suggestions are unofficial endpoints; when they refuse, that signal is absent and
  the keyword is scored on the rest.
- GitHub search allows 10 unauthenticated calls a minute, so it is often missing on a full refresh.
- Search competition (who already ranks) is not measured: none of the free sources provides it.
