"""Facebook posting activity from a page's recent post dates. Pure: no I/O, no model calls.

The numbers (gaps, averages, verdict) are computed here so they are exact and repeatable;
the language model only turns them into a sentence or two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class Verdict(StrEnum):
    ACTIVE = "active"            # posting regularly
    IRREGULAR = "irregular"      # posts recently, but with long gaps
    INACTIVE = "inactive"        # not actively marketing on Facebook
    UNAVAILABLE = "unavailable"  # page missing, removed or not viewable
    PENDING = "pending"          # post dates not collected yet


@dataclass(frozen=True)
class Rules:
    sample_posts: int = 5
    active_max_gap_days: float = 14.0
    stale_after_days: int = 60


@dataclass(frozen=True)
class Activity:
    verdict: Verdict
    reason: str
    posts_found: int = 0
    sample: tuple[date, ...] = ()           # newest first, at most Rules.sample_posts
    gaps_days: tuple[int, ...] = ()         # between consecutive posts in the sample
    avg_gap_days: float | None = None
    days_since_last: int | None = None
    flags: list[str] = field(default_factory=list)
    too_few_posts: bool = False

    @property
    def last_post(self) -> date | None:
        return self.sample[0] if self.sample else None


def assess(post_dates: list[date], as_of: date, rules: Rules = Rules(),
           page_status: str = "ok") -> Activity:
    """Judge a page from the dates of its posts (any order, duplicates allowed)."""
    status = (page_status or "ok").strip().lower()
    if status == "pending":
        return Activity(Verdict.PENDING, "post dates not collected yet")
    if status == "unavailable":
        return Activity(Verdict.UNAVAILABLE, "Facebook page is unavailable or removed")

    flags: list[str] = []
    future = [d for d in post_dates if d > as_of]
    if future:
        flags.append(f"ignored {len(future)} post date(s) after {as_of.isoformat()}")
    dates = sorted((d for d in post_dates if d <= as_of), reverse=True)
    n = len(dates)
    sample = tuple(dates[: rules.sample_posts])
    days_since = (as_of - sample[0]).days if sample else None

    if n < rules.sample_posts:
        why = "no posts on the page" if n == 0 else f"only {n} post(s) on the page"
        return Activity(Verdict.INACTIVE, why, n, sample, _gaps(sample),
                        _avg(_gaps(sample)), days_since, flags, too_few_posts=True)

    gaps = _gaps(sample)
    avg = _avg(gaps)
    assert days_since is not None and avg is not None
    if days_since > rules.stale_after_days:
        verdict, why = Verdict.INACTIVE, f"last post was {days_since} days ago"
    elif avg <= rules.active_max_gap_days and days_since <= 2 * rules.active_max_gap_days:
        verdict, why = Verdict.ACTIVE, f"posts about every {avg:g} days"
    elif avg <= rules.active_max_gap_days:
        # a burst of posts followed by a long silence is not ongoing marketing
        verdict, why = Verdict.IRREGULAR, (
            f"posted in a burst (every {avg:g} days) but nothing for the last {days_since} days")
    else:
        verdict, why = Verdict.IRREGULAR, f"posts on average every {avg:g} days"
    return Activity(verdict, why, n, sample, gaps, avg, days_since, flags)


def _gaps(sample: tuple[date, ...]) -> tuple[int, ...]:
    return tuple((a - b).days for a, b in zip(sample, sample[1:], strict=False))


def _avg(gaps: tuple[int, ...]) -> float | None:
    return round(sum(gaps) / len(gaps), 1) if gaps else None


def fallback_summary(name: str, a: Activity) -> str:
    """Deterministic summary, used for inactive pages and when the model is off or fails."""
    if a.verdict is Verdict.PENDING:
        return "Not checked yet."
    if a.verdict is Verdict.UNAVAILABLE:
        return f"{name}'s Facebook page is unavailable, so it is not actively marketing on Facebook."
    if a.too_few_posts:
        return (f"{name} is not actively marketing its business on Facebook: "
                f"{a.reason}.")
    last = a.last_post.isoformat() if a.last_post else "unknown"
    base = (f"Last {len(a.sample)} posts are on average {a.avg_gap_days:g} days apart; "
            f"the latest was {last} ({a.days_since_last} days ago).")
    if a.verdict is Verdict.INACTIVE:
        return f"{name} is not actively marketing its business on Facebook. {base}"
    if a.verdict is Verdict.ACTIVE:
        return f"{name} is actively posting on Facebook. {base}"
    return f"{name} posts on Facebook, but irregularly. {base}"
