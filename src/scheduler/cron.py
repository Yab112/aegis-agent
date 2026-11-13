"""Tiny 5-field cron evaluator (UTC) — no external dependency.

Supports ``*``, ``*/n``, ``a-b``, ``a-b/n`` and comma lists in each field:
minute hour day-of-month month day-of-week (0 or 7 = Sunday).

Day-of-month / day-of-week follow classic cron: when both are restricted, a day
matches if *either* matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_SEARCH_DAYS = 366 * 5


def _parse_field(expr: str, lo: int, hi: int) -> frozenset[int]:
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty cron field segment in {expr!r}")
        step = 1
        if "/" in part:
            part, step_raw = part.split("/", 1)
            step = int(step_raw)
            if step <= 0:
                raise ValueError(f"cron step must be positive: {expr!r}")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = int(part)
            end = hi if step > 1 else start
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron value out of range {lo}-{hi}: {expr!r}")
        out.update(range(start, end + 1, step))
    return frozenset(out)


@dataclass(frozen=True)
class CronSpec:
    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday … 6 = Saturday
    dom_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, expr: str) -> CronSpec:
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"cron expression needs 5 fields, got {expr!r}")
        parsed = [_parse_field(f, lo, hi) for f, (lo, hi) in zip(fields, _RANGES)]
        weekdays = frozenset(d % 7 for d in parsed[4])
        return cls(
            expr=" ".join(fields),
            minutes=parsed[0],
            hours=parsed[1],
            days=parsed[2],
            months=parsed[3],
            weekdays=weekdays,
            dom_any=fields[2] == "*",
            dow_any=fields[4] == "*",
        )

    def _day_matches(self, dt: datetime) -> bool:
        if dt.month not in self.months:
            return False
        dom_ok = dt.day in self.days
        dow_ok = (dt.isoweekday() % 7) in self.weekdays
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow_ok
        if self.dow_any:
            return dom_ok
        return dom_ok or dow_ok

    def prev_fire(self, now: datetime) -> datetime:
        """Latest scheduled time that is <= ``now``."""
        now = _utc(now).replace(second=0, microsecond=0)
        day = now.replace(hour=0, minute=0)
        hours = sorted(self.hours, reverse=True)
        minutes = sorted(self.minutes, reverse=True)
        for _ in range(_SEARCH_DAYS):
            if self._day_matches(day):
                for h in hours:
                    for m in minutes:
                        cand = day.replace(hour=h, minute=m)
                        if cand <= now:
                            return cand
            day -= timedelta(days=1)
        raise ValueError(f"cron {self.expr!r} never fires")

    def next_fire(self, now: datetime) -> datetime:
        """Earliest scheduled time that is strictly > ``now``."""
        now = _utc(now).replace(second=0, microsecond=0)
        day = now.replace(hour=0, minute=0)
        hours = sorted(self.hours)
        minutes = sorted(self.minutes)
        for _ in range(_SEARCH_DAYS):
            if self._day_matches(day):
                for h in hours:
                    for m in minutes:
                        cand = day.replace(hour=h, minute=m)
                        if cand > now:
                            return cand
            day += timedelta(days=1)
        raise ValueError(f"cron {self.expr!r} never fires")


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
