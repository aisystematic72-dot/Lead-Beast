"""Facebook activity workflow over CSV files.

1. `write_template` lists every business that has a Facebook link in a posts sheet
   (one row per business, page_status=pending).
2. Post dates are collected into that sheet (browser session or by hand).
3. `analyze` computes gaps/verdicts, asks the model for a short summary, and writes
   the results file plus `last post date` / `facebook activity` in the checklist.
"""

from __future__ import annotations

import asyncio
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import structlog

from leadbeast.core.config import get_settings
from leadbeast.core.ports import ModelClient
from leadbeast.social.fb_activity import Activity, Rules, Verdict, assess, fallback_summary

log = structlog.get_logger(__name__)

POSTS_FIELDS = ["name", "trade", "city", "facebook", "page_status", "post_dates",
                "collected_on", "collector_note"]
RESULT_FIELDS = ["name", "trade", "city", "facebook", "verdict", "summary", "posts_found",
                 "last_post_date", "days_since_last_post", "avg_gap_days", "gaps_days",
                 "sample_post_dates", "reason", "summary_source", "collected_on", "analyzed_on",
                 "flags"]
PAGE_STATUSES = {"pending", "ok", "unavailable"}
_DATE_SPLIT = re.compile(r"[;,|\n]+")


def rules_from_settings() -> Rules:
    s = get_settings()
    return Rules(s.fb_sample_posts, s.fb_active_max_gap_days, s.fb_stale_after_days)


# ------------------------------------------------------------------ CSV helpers
def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), [dict(row) for row in r]


def _write(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)  # atomic: a crash never leaves a half-written sheet


def parse_dates(raw: str) -> list[date]:
    out: list[date] = []
    for part in _DATE_SPLIT.split(raw or ""):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(date.fromisoformat(part[:10]))
        except ValueError as e:
            raise ValueError(f"bad date {part!r} (use YYYY-MM-DD)") from e
    return out


# ------------------------------------------------------------------ step 1: template
def write_template(checklist: Path, posts: Path) -> tuple[int, int]:
    """Add every business with a Facebook link to the posts sheet. Keeps collected rows.

    Returns (added, kept).
    """
    _, rows = _read(checklist)
    existing: dict[str, dict[str, str]] = {}
    if posts.exists():
        _, prev = _read(posts)
        existing = {r["name"]: r for r in prev}
    out: list[dict[str, str]] = []
    added = 0
    for r in rows:
        fb = (r.get("facebook") or "").strip()
        if not fb:
            continue
        old = existing.get(r["name"])
        if old:
            old["facebook"] = fb  # follow link corrections made in the checklist
            out.append(old)
            continue
        out.append({"name": r["name"], "trade": r.get("trade", ""), "city": r.get("city", ""),
                    "facebook": fb, "page_status": "pending", "post_dates": "",
                    "collected_on": "", "collector_note": ""})
        added += 1
    _write(posts, POSTS_FIELDS, out)
    return added, len(out) - added


# ------------------------------------------------------------------ step 3: analyze
@dataclass
class Result:
    row: dict[str, str]
    activity: Activity
    as_of: date
    summary: str = ""
    source: str = ""


def _prompt(r: Result) -> str:
    a = r.activity
    dates = ", ".join(d.isoformat() for d in a.sample)
    return (
        "You are helping a local-marketing consultant judge whether a small business is "
        "actively marketing itself on Facebook.\n\n"
        f"Business: {r.row['name']} ({r.row.get('trade', '')}, {r.row.get('city', '')}, CA)\n"
        f"Checked on: {r.as_of.isoformat()}\n"
        f"Dates of its {len(a.sample)} most recent Facebook posts (newest first): {dates}\n"
        f"Days between those posts: {', '.join(map(str, a.gaps_days))}\n"
        f"Average gap: {a.avg_gap_days:g} days. Days since the latest post: {a.days_since_last}.\n"
        f"Verdict (already decided, do not change it): {a.verdict.value} - {a.reason}.\n\n"
        "Write 2 short plain-English sentences. Sentence 1 states the verdict "
        "(actively posting / posting irregularly / not actively marketing on Facebook). "
        "Sentence 2 gives the average gap between posts and how recent the last post is. "
        "Use only the facts above; do not guess what the posts contain. No headings, no bullets."
    )


def _clean(text: str) -> str:
    return " ".join(text.replace("*", "").split())


async def _summarise(results: list[Result], model: ModelClient | None, concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(r: Result) -> None:
        a = r.activity
        needs_model = model is not None and not a.too_few_posts and a.verdict in (
            Verdict.ACTIVE, Verdict.IRREGULAR, Verdict.INACTIVE)
        if not needs_model:
            r.summary, r.source = fallback_summary(r.row["name"], a), "rule"
            return
        assert model is not None
        async with sem:
            try:
                r.summary = _clean(await model.text(_prompt(r), max_tokens=160))
                r.source = f"{model.name}:{model.version}"
            except Exception as e:  # never lose a row to a model hiccup
                log.warning("fb.summary_failed", business=r.row["name"], error=str(e))
                r.summary, r.source = fallback_summary(r.row["name"], a), "rule (model failed)"

    await asyncio.gather(*(one(r) for r in results))


def _assess_rows(rows: list[dict[str, str]], rules: Rules, today: date) -> list[Result]:
    results: list[Result] = []
    for row in rows:
        status = (row.get("page_status") or "pending").strip().lower()
        if status not in PAGE_STATUSES:
            raise ValueError(f"{row['name']}: page_status must be one of {sorted(PAGE_STATUSES)}")
        as_of = date.fromisoformat(row["collected_on"]) if row.get("collected_on") else today
        try:
            dates = parse_dates(row.get("post_dates", ""))
        except ValueError as e:
            raise ValueError(f"{row['name']}: {e}") from e
        results.append(Result(row, assess(dates, as_of, rules, status), as_of))
    return results


def _result_row(r: Result, today: date) -> dict[str, Any]:
    a = r.activity
    return {
        "name": r.row["name"], "trade": r.row.get("trade", ""), "city": r.row.get("city", ""),
        "facebook": r.row.get("facebook", ""), "verdict": a.verdict.value, "summary": r.summary,
        "posts_found": a.posts_found if a.verdict not in (Verdict.PENDING, Verdict.UNAVAILABLE) else "",
        "last_post_date": a.last_post.isoformat() if a.last_post else "",
        "days_since_last_post": "" if a.days_since_last is None else a.days_since_last,
        "avg_gap_days": "" if a.avg_gap_days is None else a.avg_gap_days,
        "gaps_days": "; ".join(map(str, a.gaps_days)),
        "sample_post_dates": "; ".join(d.isoformat() for d in a.sample),
        "reason": a.reason, "summary_source": r.source,
        "collected_on": r.row.get("collected_on", ""), "analyzed_on": today.isoformat(),
        "flags": "; ".join(a.flags),
    }


CHECKLIST_COLS = ("last post date", "facebook activity", "facebook summary")


def _write_checklist(checklist: Path, values: dict[str, tuple[str, str, str]]) -> int:
    """values: business name -> (last post date, verdict, summary). Returns rows updated."""
    fields, rows = _read(checklist)
    for col in CHECKLIST_COLS:
        if col not in fields:
            fields.append(col)
    n = 0
    for row in rows:
        v = values.get(row["name"])
        if v is None:
            continue
        row.update(dict(zip(CHECKLIST_COLS, v, strict=True)))
        n += 1
    _write(checklist, fields, rows)
    return n


def _update_checklist(checklist: Path, results: list[Result]) -> int:
    return _write_checklist(checklist, {
        r.row["name"]: (r.activity.last_post.isoformat() if r.activity.last_post else "",
                        r.activity.verdict.value, r.summary)
        for r in results if r.activity.verdict is not Verdict.PENDING})


def sync_checklist(results_csv: Path, checklist: Path) -> int:
    """Copy verdicts and summaries from an existing results file (no model calls)."""
    _, rows = _read(results_csv)
    return _write_checklist(checklist, {
        r["name"]: (r.get("last_post_date", ""), r.get("verdict", ""), r.get("summary", ""))
        for r in rows if r.get("verdict") and r.get("verdict") != Verdict.PENDING.value})


async def analyze(posts: Path, output: Path, checklist: Path | None,
                  model: ModelClient | None, concurrency: int = 4,
                  today: date | None = None) -> dict[str, int]:
    today = today or datetime.now().date()
    _, rows = _read(posts)
    results = _assess_rows(rows, rules_from_settings(), today)
    await _summarise(results, model, concurrency)
    _write(output, RESULT_FIELDS, [_result_row(r, today) for r in results])
    counts: dict[str, int] = {}
    for r in results:
        counts[r.activity.verdict.value] = counts.get(r.activity.verdict.value, 0) + 1
    if checklist is not None:
        counts["checklist_rows_updated"] = _update_checklist(checklist, results)
    return counts
