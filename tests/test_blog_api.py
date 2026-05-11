from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.blog_routes import _public_image_urls, router as blog_router
import src.api.blog_routes as blog_routes


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, rows):
        self._rows = rows
        self._eq = []
        self._neq = []
        self._overlaps = []
        self._order = None
        self._range = None
        self._limit = None
        self._count = None

    def select(self, _cols, count=None):
        self._count = count
        return self

    def eq(self, key, value):
        self._eq.append((key, value))
        return self

    def neq(self, key, value):
        self._neq.append((key, value))
        return self

    def overlaps(self, key, values):
        self._overlaps.append((key, values))
        return self

    def order(self, key, desc=False):
        self._order = (key, desc)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, value):
        self._limit = value
        return self

    def execute(self):
        rows = [deepcopy(r) for r in self._rows]

        for key, value in self._eq:
            rows = [r for r in rows if r.get(key) == value]

        for key, value in self._neq:
            rows = [r for r in rows if r.get(key) != value]

        for key, values in self._overlaps:
            target = set(values or [])
            rows = [r for r in rows if bool(target.intersection(set(r.get(key) or [])))]

        count_before_paging = len(rows)

        if self._order:
            key, desc = self._order
            rows.sort(key=lambda r: r.get(key) or "", reverse=desc)

        if self._range:
            start, end = self._range
            rows = rows[start : end + 1]

        if self._limit is not None:
            rows = rows[: self._limit]

        count_val = count_before_paging if self._count == "exact" else None
        return _Result(rows, count=count_val)


class _FakeClient:
    def __init__(self, posts):
        self._posts = posts

    def table(self, name):
        if name != "blog_posts":
            raise AssertionError(f"Unexpected table {name}")
        return _Query(self._posts)


def _build_test_client(posts):
    app = FastAPI()
    app.include_router(blog_router)
    blog_routes._client = lambda _settings: _FakeClient(posts)
    return TestClient(app)


def _sample_posts():
    return [
        {
            "slug": "fastapi-routing-patterns",
            "title": "FastAPI routing patterns",
            "description": "Routing and validation tips",
            "body_md": "## FastAPI\nContent",
            "published_at": "2026-04-20T09:00:00+00:00",
            "updated_at": "2026-04-20T09:00:00+00:00",
            "status": "published",
            "tags": ["fastapi", "python"],
            "topic_key": "fastapi_routing",
            "resource_links": [
                {
                    "title": "FastAPI Docs",
                    "url": "https://fastapi.tiangolo.com/",
                    "description": "Official docs",
                }
            ],
            "image_url": None,
            "image_alt": None,
            "og_image_url": None,
            "canonical_url": None,
        },
        {
            "slug": "python-testing-evals",
            "title": "Python testing evals",
            "description": "How to test LLM-heavy flows",
            "body_md": "## Testing\nContent",
            "published_at": "2026-04-18T09:00:00+00:00",
            "updated_at": "2026-04-18T09:00:00+00:00",
            "status": "published",
            "tags": ["python", "testing"],
            "topic_key": "python_testing",
            "resource_links": [],
            "image_url": None,
            "image_alt": None,
            "og_image_url": None,
            "canonical_url": None,
        },
        {
            "slug": "agentic-rag-evals",
            "title": "Agentic RAG evals",
            "description": "Evaluation loops for retrieval",
            "body_md": "## Evals\nContent",
            "published_at": "2026-04-15T09:00:00+00:00",
            "updated_at": "2026-04-15T09:00:00+00:00",
            "status": "published",
            "tags": ["ai", "fastapi"],
            "topic_key": "agentic_rag_evals",
            "resource_links": [],
            "image_url": None,
            "image_alt": None,
            "og_image_url": None,
            "canonical_url": None,
        },
        {
            "slug": "draft-internal-post",
            "title": "Draft internal",
            "description": "Should never be public",
            "body_md": "## Draft",
            "published_at": None,
            "updated_at": None,
            "status": "draft",
            "tags": ["fastapi"],
            "topic_key": "draft_topic",
            "resource_links": [],
            "image_url": None,
            "image_alt": None,
            "og_image_url": None,
            "canonical_url": None,
        },
    ]


def test_public_image_urls_falls_back_when_row_empty():
    d = "https://cdn.example.com/default-og.png"
    assert _public_image_urls({}, d) == (d, d)
    assert _public_image_urls({"image_url": "", "og_image_url": ""}, d) == (d, d)
    row = {"image_url": "https://cdn.example.com/cover.png", "og_image_url": None}
    assert _public_image_urls(row, d) == ("https://cdn.example.com/cover.png", "https://cdn.example.com/cover.png")


def test_list_blog_posts_topic_filter():
    client = _build_test_client(_sample_posts())

    r = client.get("/blog", params={"topic": "fastapi", "page": 1, "page_size": 10})

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [x["slug"] for x in body["items"]] == [
        "fastapi-routing-patterns",
        "agentic-rag-evals",
    ]


def test_list_blog_topics_counts_published_only():
    client = _build_test_client(_sample_posts())

    r = client.get("/blog/topics")

    assert r.status_code == 200
    topics = {item["topic"]: item["count"] for item in r.json()["items"]}
    assert topics["fastapi"] == 2
    assert topics["python"] == 2
    assert "draft_topic" not in topics


def test_blog_detail_includes_resource_links_and_related_posts():
    client = _build_test_client(_sample_posts())

    r = client.get("/blog/fastapi-routing-patterns")

    assert r.status_code == 200
    body = r.json()
    assert body["topic_key"] == "fastapi_routing"
    assert body["resource_links"][0]["url"] == "https://fastapi.tiangolo.com/"
    assert any(x["slug"] == "agentic-rag-evals" for x in body["related_posts"])


def test_blog_search_returns_matching_published_posts():
    client = _build_test_client(_sample_posts())

    r = client.get("/blog/search", params={"q": "fastapi", "page": 1, "page_size": 10})

    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "fastapi"
    assert body["total"] == 2
    assert [x["slug"] for x in body["items"]] == [
        "fastapi-routing-patterns",
        "agentic-rag-evals",
    ]
