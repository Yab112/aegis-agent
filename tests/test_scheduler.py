from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.scheduler.cron import CronSpec
from src.scheduler.jobs import JobContext, JobDef, _run_blog_draft
from src.scheduler.service import Scheduler
from src.scheduler.store import InMemorySchedulerStore, parse_ts


def _dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _settings(**over):
    base = dict(
        scheduler_enabled=True,
        scheduler_tick_seconds=60,
        scheduler_max_attempts=3,
        scheduler_retry_backoff_minutes=10,
        scheduler_run_missed_on_first_boot=False,
        scheduler_keep_awake=True,
        scheduler_keep_awake_minutes=10,
        scheduler_blog_draft_status="published",
        public_base_url=None,
        portfolio_url="https://example.test",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _make(run, *, cron="0 9 * * *", now=None, **settings_over):
    clock = _Clock(now or _dt(2026, 1, 10, 8, 0))
    store = InMemorySchedulerStore()
    notes: list[str] = []
    job = JobDef(name="job", title="Job", description="d", cron=cron, run=run)
    sched = Scheduler(store, {"job": job}, _settings(**settings_over), now_fn=clock, notifier=notes.append)
    return sched, store, clock, notes


# ── cron ────────────────────────────────────────────────────────────────────


def test_cron_weekday_list_matches_sunday_and_wednesday():
    spec = CronSpec.parse("0 10 * * 0,3")
    # 2026-01-08 is a Thursday → previous fire is Wednesday the 7th.
    assert spec.prev_fire(_dt(2026, 1, 8, 12, 0)) == _dt(2026, 1, 7, 10, 0)
    assert spec.next_fire(_dt(2026, 1, 8, 12, 0)) == _dt(2026, 1, 11, 10, 0)  # Sunday


def test_cron_day_step_resets_each_month():
    spec = CronSpec.parse("0 9 */5 * *")
    assert spec.prev_fire(_dt(2026, 1, 30, 0, 0)) == _dt(2026, 1, 26, 9, 0)
    assert spec.next_fire(_dt(2026, 1, 31, 10, 0)) == _dt(2026, 2, 1, 9, 0)
    assert spec.prev_fire(_dt(2026, 2, 1, 8, 59)) == _dt(2026, 1, 31, 9, 0)


def test_cron_exact_minute_is_inclusive_for_prev_and_exclusive_for_next():
    spec = CronSpec.parse("0 12 * * *")
    at = _dt(2026, 3, 1, 12, 0)
    assert spec.prev_fire(at) == at
    assert spec.next_fire(at) == _dt(2026, 3, 2, 12, 0)


@pytest.mark.parametrize("bad", ["", "* * * *", "61 * * * *", "0 9 */0 * *", "a b c d e"])
def test_cron_rejects_invalid(bad):
    with pytest.raises(ValueError):
        CronSpec.parse(bad)


# ── scheduling ──────────────────────────────────────────────────────────────


def test_first_boot_does_not_backfill_then_runs_next_slot_once():
    calls: list[JobContext] = []
    sched, store, clock, _ = _make(lambda ctx: calls.append(ctx) or {"ok": True})

    assert sched.tick() == []  # registered, seeded to yesterday's slot
    assert calls == []

    clock.now = _dt(2026, 1, 10, 9, 0)
    out = sched.tick()
    assert [o["status"] for o in out] == ["success"]
    assert calls[0].slot == _dt(2026, 1, 10, 9, 0)
    assert calls[0].run_key == "sched-job-20260110T0900Z"

    clock.now = _dt(2026, 1, 10, 9, 5)
    assert sched.tick() == []  # same slot never runs twice
    assert len(calls) == 1
    assert len(store.runs) == 1


def test_first_boot_backfills_when_enabled():
    calls = []
    sched, *_ = _make(lambda ctx: calls.append(ctx) or {}, scheduler_run_missed_on_first_boot=True)
    sched.tick()
    assert calls[0].slot == _dt(2026, 1, 9, 9, 0)


def test_missed_slots_while_asleep_coalesce_into_one_catch_up_run():
    calls = []
    sched, store, clock, _ = _make(lambda ctx: calls.append(ctx) or {})
    sched.tick()

    clock.now = _dt(2026, 1, 14, 15, 30)  # host slept through four daily slots
    sched.tick()
    assert [c.slot for c in calls] == [_dt(2026, 1, 14, 9, 0)]
    assert parse_ts(store.get_job("job")["last_slot"]) == _dt(2026, 1, 14, 9, 0)


def test_failure_backs_off_retries_then_gives_up_and_notifies():
    def boom(ctx):
        raise RuntimeError("gemini down")

    sched, store, clock, notes = _make(boom)
    sched.tick()
    clock.now = _dt(2026, 1, 10, 9, 0)

    assert sched.tick()[0]["status"] == "failed"
    row = store.get_job("job")
    assert row["slot_attempts"] == 1
    assert parse_ts(row["next_attempt_at"]) == clock.now + timedelta(minutes=10)
    assert parse_ts(row["last_slot"]) == _dt(2026, 1, 9, 9, 0)  # slot still pending
    assert row["locked_until"] is None
    assert notes == []

    clock.now += timedelta(minutes=5)
    assert sched.tick() == []  # still backing off

    clock.now += timedelta(minutes=5)
    sched.tick()  # attempt 2 → 20 min backoff
    assert parse_ts(store.get_job("job")["next_attempt_at"]) == clock.now + timedelta(minutes=20)

    clock.now += timedelta(minutes=20)
    sched.tick()  # attempt 3 → give up on this slot
    row = store.get_job("job")
    assert parse_ts(row["last_slot"]) == _dt(2026, 1, 10, 9, 0)
    assert row["slot_attempts"] == 0 and row["next_attempt_at"] is None
    assert len(notes) == 1 and "gemini down" in notes[0]
    assert [r["status"] for r in store.runs] == ["failed"] * 3

    clock.now += timedelta(minutes=30)
    assert sched.tick() == []


def test_lock_held_by_another_instance_blocks_run_and_expired_lock_is_reclaimed():
    calls = []
    sched, store, clock, _ = _make(lambda ctx: calls.append(ctx) or {})
    sched.tick()
    assert store.claim("job", "other-instance", 3600)

    clock.now = _dt(2026, 1, 10, 9, 0)
    assert sched.run_job("job", trigger="manual")["status"] == "locked"
    assert calls == []

    store.update_job("job", {"locked_until": datetime.now(timezone.utc) - timedelta(seconds=1)})
    assert sched.run_job("job", trigger="manual")["status"] == "success"


def test_manual_run_does_not_consume_the_schedule_slot():
    calls = []
    sched, store, clock, _ = _make(lambda ctx: calls.append(ctx) or {})
    sched.tick()
    before = store.get_job("job")["last_slot"]

    out = sched.run_job("job", trigger="manual")
    assert out["status"] == "success" and calls[0].trigger == "manual"
    assert calls[0].run_key.startswith("manual-job-")
    assert store.get_job("job")["last_slot"] == before


def test_paused_job_is_skipped_until_resumed():
    calls = []
    sched, _, clock, _ = _make(lambda ctx: calls.append(ctx) or {})
    sched.tick()
    sched.set_enabled("job", False)
    clock.now = _dt(2026, 1, 10, 9, 0)
    assert sched.tick() == []
    sched.set_enabled("job", True)
    assert len(sched.tick()) == 1


def test_published_post_sends_notification_with_link():
    sched, _, clock, notes = _make(
        lambda ctx: {"status": "inserted", "post_status": "published", "slug": "hello-world"}
    )
    sched.tick()
    clock.now = _dt(2026, 1, 10, 9, 0)
    sched.tick()
    assert notes == ["📝 New blog post published: https://example.test/blog/hello-world"]


def test_storage_outage_is_reported_not_raised():
    sched, store, _, _ = _make(lambda ctx: {})

    def broken(_name):
        raise RuntimeError("relation scheduler_jobs does not exist")

    store.get_job = broken
    assert sched.tick() == []
    assert "scheduler_jobs" in (sched.storage_error or "")


def test_keep_awake_url_resolution(monkeypatch):
    sched, *_ = _make(lambda ctx: {})
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("KOYEB_PUBLIC_DOMAIN", raising=False)
    assert sched.keep_awake_url() is None
    monkeypatch.setenv("KOYEB_PUBLIC_DOMAIN", "app.koyeb.app")
    assert sched.keep_awake_url() == "https://app.koyeb.app/health/livez"
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://x.onrender.com/")
    assert sched.keep_awake_url() == "https://x.onrender.com/health/livez"
    sched.settings.scheduler_keep_awake = False
    assert sched.keep_awake_url() is None


# ── blog draft job ──────────────────────────────────────────────────────────


def test_blog_draft_job_sets_run_key_status_and_retries_on_quota(monkeypatch):
    import src.blog.pipeline as pipeline

    seen = []

    def fake_run(cfg):
        seen.append((cfg.pipeline_run_key, cfg.post_status_override))
        if len(seen) < 3:
            raise RuntimeError("429 Quota exceeded")
        return {"status": "inserted"}

    monkeypatch.setattr(pipeline, "run_blog_draft_once", fake_run)
    settings = _settings(
        gemini_api_key="k",
        gemini_model="m",
        supabase_url="u",
        supabase_service_key="s",
        owner_name="Y",
    )
    sleeps: list[float] = []
    ctx = JobContext(settings=settings, trigger="schedule", slot=_dt(2026, 1, 10, 9), run_key="sched-x")
    assert _run_blog_draft(ctx, sleep=sleeps.append) == {"status": "inserted"}
    assert seen == [("sched-x", "published")] * 3
    assert sleeps == [15, 30]

    seen.clear()
    manual = JobContext(settings=settings, trigger="manual", slot=None, run_key="manual-x")
    monkeypatch.setattr(pipeline, "run_blog_draft_once", lambda cfg: seen.append(cfg.post_status_override) or {})
    _run_blog_draft(manual)
    assert seen == [None]  # manual runs keep BLOG_POST_STATUS_AFTER_WRITE semantics


def test_post_status_override_wins_over_env(monkeypatch):
    from src.blog.pipeline import _post_status_after_write

    monkeypatch.setenv("BLOG_POST_STATUS_AFTER_WRITE", "draft")
    assert _post_status_after_write()[0] == "draft"
    status, published_at = _post_status_after_write("published")
    assert status == "published" and published_at


# ── HTTP ────────────────────────────────────────────────────────────────────


def _client(sched: Scheduler) -> TestClient:
    from config.settings import get_settings
    from src.api import scheduler_routes

    app = FastAPI()
    app.include_router(scheduler_routes.internal_router)
    app.include_router(scheduler_routes.public_router)
    app.dependency_overrides[scheduler_routes.get_scheduler] = lambda: sched
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(api_secret_key="s3cret")
    return TestClient(app)


def test_internal_routes_require_secret_and_run_jobs():
    calls = []
    sched, store, _, _ = _make(lambda ctx: calls.append(ctx.trigger) or {"n": 1})
    client = _client(sched)
    auth = {"Authorization": "Bearer s3cret"}

    assert client.get("/internal/scheduler/status").status_code == 401
    assert client.post("/internal/scheduler/jobs/job/run").status_code == 401
    assert client.post("/internal/scheduler/jobs/nope/run", headers=auth).status_code == 404

    assert client.post("/internal/scheduler/jobs/job/run", headers=auth).status_code == 202
    assert calls == ["manual"]  # TestClient runs background tasks before returning

    body = client.get("/internal/scheduler/status", headers=auth).json()
    assert body["jobs"][0]["last_status"] == "success"
    assert body["jobs"][0]["last_result"] == {"n": 1}
    assert len(body["recent_runs"]) == 1

    assert client.post("/internal/scheduler/jobs/job/pause", headers=auth).json() == {"job": "job", "enabled": False}
    assert store.get_job("job")["enabled"] is False


def test_public_status_is_sanitized():
    def boom(ctx):
        raise RuntimeError("secret-ish internal detail")

    sched, _, _, _ = _make(boom)
    sched.run_job("job", trigger="manual")
    body = _client(sched).get("/automation/status").json()
    job = body["jobs"][0]
    assert job["last_status"] == "failed" and job["next_run_at"]
    assert "last_error" not in job and "last_result" not in job
    assert "owner" not in body and "storage_error" not in body
    assert "secret-ish" not in str(body)


# ── change-driven jobs (knowledge-base sync) ────────────────────────────────


def _make_change_job(run, fingerprint, **settings_over):
    clock = _Clock(_dt(2026, 1, 10, 8, 0))
    store = InMemorySchedulerStore()
    notes: list[str] = []
    job = JobDef(name="kb", title="KB", description="d", cron=None, run=run, fingerprint=fingerprint)
    sched = Scheduler(store, {"kb": job}, _settings(**settings_over), now_fn=clock, notifier=notes.append)
    return sched, store, clock


def test_change_job_runs_once_per_fingerprint_and_survives_restart():
    calls = []
    run = lambda ctx: calls.append(ctx.trigger) or {"fingerprint": "v1"}
    sched, store, clock = _make_change_job(run, lambda: "v1")

    assert [o["status"] for o in sched.tick()] == ["success"]  # first boot: ingest
    assert sched.tick() == [] and calls == ["change"]

    # A fresh process with the same docs reads the stored fingerprint and stays idle.
    restarted = Scheduler(store, sched.jobs, _settings(), now_fn=clock)
    assert restarted.tick() == []

    # A deploy that changes the docs triggers exactly one more run.
    job = JobDef(name="kb", title="KB", description="d", cron=None,
                 run=lambda ctx: calls.append("v2") or {"fingerprint": "v2"}, fingerprint=lambda: "v2")
    deployed = Scheduler(store, {"kb": job}, _settings(), now_fn=clock)
    assert len(deployed.tick()) == 1 and deployed.tick() == []
    assert deployed.status(public=True)["jobs"][0]["next_run_at"] is None


def test_change_job_failure_backs_off_then_waits_a_day():
    def boom(ctx):
        raise RuntimeError("embeddings down")

    sched, store, clock = _make_change_job(boom, lambda: "v1", scheduler_max_attempts=2)
    sched.tick()
    assert parse_ts(store.get_job("kb")["next_attempt_at"]) == clock.now + timedelta(minutes=10)
    assert sched.tick() == []
    clock.now += timedelta(minutes=10)
    sched.tick()
    assert parse_ts(store.get_job("kb")["next_attempt_at"]) == clock.now + timedelta(hours=24)


# ── knowledge-base ingest ───────────────────────────────────────────────────


class _Docs:
    """Minimal stand-in for the Supabase `documents` table."""

    def __init__(self, rows):
        self.rows = {r["id"]: r for r in rows}
        self._op = None

    def table(self, _name):
        return self

    def select(self, _cols):
        self._op = ("select",)
        return self

    def range(self, a, b):
        self._op = ("select", a, b)
        return self

    def upsert(self, rows):
        self._op = ("upsert", rows)
        return self

    def delete(self):
        self._op = ("delete",)
        return self

    def in_(self, _col, ids):
        self._op = ("delete", ids)
        return self

    def execute(self):
        op = self._op
        if op[0] == "select":
            ids = sorted(self.rows)[op[1] : op[2] + 1]
            return SimpleNamespace(data=[{"id": i} for i in ids])
        if op[0] == "upsert":
            for r in op[1]:
                self.rows[r["id"]] = r
        if op[0] == "delete":
            for i in op[1]:
                self.rows.pop(i, None)
        return SimpleNamespace(data=[])


def test_kb_ingest_is_idempotent_and_prunes_stale_rows(tmp_path, monkeypatch):
    import supabase

    import src.rag.embedder as embedder
    from src.rag import ingest

    kb = tmp_path / "kb"
    (kb / "general").mkdir(parents=True)
    (kb / "general" / "about.md").write_text("# About\n\nYabibal builds AI systems.\n")
    (kb / "general" / "stack.md").write_text("# Stack\n\nNext.js and FastAPI.\n")

    docs = _Docs([{"id": "11111111-1111-1111-1111-111111111111", "content": "old random-id row"}])
    embedded: list[list[str]] = []
    monkeypatch.setattr(supabase, "create_client", lambda *_a: docs)
    monkeypatch.setattr(embedder, "embed_texts", lambda texts: embedded.append(list(texts)) or [[0.1, 0.2]] * len(texts))

    first = ingest.run_kb_ingest("u", "k", base=kb)
    assert first["embedded"] == first["chunks"] >= 2 and first["removed"] == 1
    assert "11111111-1111-1111-1111-111111111111" not in docs.rows
    assert all(r["embedding"] == "[0.1,0.2]" for r in docs.rows.values())

    again = ingest.run_kb_ingest("u", "k", base=kb)  # nothing changed → no embedding calls
    assert again["embedded"] == 0 and again["removed"] == 0 and again["fingerprint"] == first["fingerprint"]
    assert len(embedded) == 1

    (kb / "general" / "stack.md").unlink()
    after = ingest.run_kb_ingest("u", "k", base=kb)
    assert after["removed"] >= 1 and after["embedded"] == 0
    assert after["fingerprint"] != first["fingerprint"]
    assert len(docs.rows) == after["chunks"]


def test_kb_ingest_refuses_to_wipe_store_when_docs_missing(tmp_path, monkeypatch):
    from src.rag import ingest

    with pytest.raises(RuntimeError):
        ingest.run_kb_ingest("u", "k", base=tmp_path / "missing")


def test_kb_ingest_refuses_a_prune_larger_than_what_remains(tmp_path, monkeypatch):
    import supabase

    import src.rag.embedder as embedder
    from src.rag import ingest

    kb = tmp_path / "kb" / "general"
    kb.mkdir(parents=True)
    for i in range(4):
        (kb / f"doc{i}.md").write_text(f"# Doc {i}\n\nBody number {i}.\n")
    docs = _Docs([])
    monkeypatch.setattr(supabase, "create_client", lambda *_a: docs)
    monkeypatch.setattr(embedder, "embed_texts", lambda texts: [[0.0]] * len(texts))
    ingest.run_kb_ingest("u", "k", base=kb.parent)
    before = dict(docs.rows)

    for i in range(1, 4):  # a broken build ships only one of the four docs
        (kb / f"doc{i}.md").unlink()
    with pytest.raises(RuntimeError, match="Refusing to remove"):
        ingest.run_kb_ingest("u", "k", base=kb.parent)
    assert docs.rows == before

    monkeypatch.setenv("KB_INGEST_ALLOW_LARGE_PRUNE", "true")
    assert ingest.run_kb_ingest("u", "k", base=kb.parent)["removed"] == len(before) - 1
