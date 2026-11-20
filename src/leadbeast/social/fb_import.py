"""Push the checklist (with Facebook activity) into a campaign, inline (no job queue).

collect (csv) -> resolve identity -> listing_claimed detector -> rescore. Only the listing
detector reruns: it is the one that turns `facebook activity` / `last post date` into the
`social_active` evidence the dashboard shows. Nothing is fetched from the web.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.db import session
from leadbeast.core.ids import new_run_id
from leadbeast.core.ports import SourceQuery
from leadbeast.db import models as m
from leadbeast.detectors.runner import run_detectors
from leadbeast.evidence.ledger import append, manual_event
from leadbeast.identity.service import resolve_record
from leadbeast.packs.service import pack_for_campaign
from leadbeast.scoring.service import score_business
from leadbeast.sources import get_adapter
from leadbeast.sources.service import collect

DETECTOR = "listing_claimed"
SOCIAL_PROFILES = "social_profiles"
FACEBOOK_ACTIVITY = "facebook_activity"


async def import_checklist(campaign_id: UUID, checklist: Path, limit: int = 5000,
                           results: Path | None = None, posts: Path | None = None
                           ) -> dict[str, Any]:
    run_id = new_run_id()
    details = load_activity_details(results, posts)
    details_updated = 0
    async with session() as s:
        camp = await s.get(m.Campaign, campaign_id)
        if camp is None:
            raise ValueError(f"campaign {campaign_id} not found")
        adapter = get_adapter("csv", path=str(checklist))
        record_ids = await collect(s, adapter, SourceQuery(area=camp.area, limit=limit),
                                   campaign_id, run_id)

    identity: Counter[str] = Counter()
    business_ids: list[UUID] = []
    links_updated = 0
    for rid in record_ids:
        async with session() as s:
            bid, verdict = await resolve_record(s, rid, campaign_id)
            if bid is not None:
                rec = await s.get(m.SourceRecord, rid)
                payload = rec.payload if rec else {}
                links_updated += await _sync_facebook_link(s, bid, payload)
                detail = details.get(str(payload.get("name") or ""))
                if detail:
                    details_updated += await _sync_activity_detail(s, bid, detail)
        identity[verdict] += 1
        if bid is not None and bid not in business_ids:
            business_ids.append(bid)

    classes: Counter[str] = Counter()
    for bid in business_ids:
        async with session() as s:
            pack = await pack_for_campaign(s, campaign_id)
            await run_detectors(s, bid, pack, run_id, only=[DETECTOR])
        async with session() as s:
            snap, _ = await score_business(s, bid, campaign_id)
            classes[snap.cls] += 1

    return {"run_id": run_id, "records": len(record_ids), "businesses": len(business_ids),
            "facebook_links_updated": links_updated,
            "activity_details_updated": details_updated, "identity": dict(identity),
            "classes": dict(classes)}


def _read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(";") if x.strip()]


def load_activity_details(results: Path | None, posts: Path | None) -> dict[str, dict[str, Any]]:
    """Business name -> the posting details shown on Business 360 (from the fb-analyze files)."""
    notes = {r["name"]: r.get("collector_note", "") for r in _read_csv(posts)}
    out: dict[str, dict[str, Any]] = {}
    for r in _read_csv(results):
        if not r.get("verdict") or r["verdict"] == "pending":
            continue
        out[r["name"]] = {
            "verdict": r["verdict"], "summary": r.get("summary", ""),
            "reason": r.get("reason", ""),
            "posts_found": int(_num(r.get("posts_found")) or 0) if r.get("posts_found") else None,
            "last_post_date": r.get("last_post_date") or None,
            "days_since_last_post": _num(r.get("days_since_last_post")),
            "avg_gap_days": _num(r.get("avg_gap_days")),
            "gaps_days": [int(g) for g in _split(r.get("gaps_days"))],
            "post_dates": _split(r.get("sample_post_dates")),
            "page": r.get("facebook", ""), "note": notes.get(r["name"], ""),
            "checked_on": r.get("collected_on") or r.get("analyzed_on", ""),
        }
    return out


async def _sync_activity_detail(s: AsyncSession, bid: UUID, detail: dict[str, Any]) -> int:
    """Record the posting details as operator evidence when they changed. Append-only."""
    cur = await s.scalar(text(
        "select value from evidence_current where business_id = :b and signal = :sig"),
        {"b": bid, "sig": FACEBOOK_ACTIVITY})
    if cur == detail:
        return 0
    ev = manual_event(bid, FACEBOOK_ACTIVITY, detail, "fb-activity-check",
                      note=detail.get("summary") or None
                      ).model_copy(update={"source_url": detail.get("page") or None})
    await append(s, [ev], recorded_by="fb-activity-check")
    return 1


def _norm(url: str | None) -> str:
    u = (url or "").strip().lower().rstrip("/")
    for p in ("https://", "http://", "www.", "m."):
        u = u.removeprefix(p)
    return u


async def _sync_facebook_link(s: AsyncSession, bid: UUID, payload: dict[str, Any]) -> int:
    """Checklist Facebook link differs from the dashboard's -> record it as operator-verified.

    Append-only: the earlier social_profiles event stays in the history.
    """
    link = str(payload.get("facebook") or "").strip()
    if not link:
        return 0
    cur = await s.scalar(text(
        "select value from evidence_current where business_id = :b and signal = :sig"),
        {"b": bid, "sig": SOCIAL_PROFILES})
    old: dict[str, Any] = cur if isinstance(cur, dict) else {}
    if _norm(old.get("facebook")) == _norm(link):
        return 0
    value = {**{"other": [], "instagram": None}, **old, "facebook": link,
             "checked": f"Facebook page confirmed in the operator's browser ({now().date()})"}
    await append(s, [manual_event(bid, SOCIAL_PROFILES, value, "fb-activity-check",
                                  note="Facebook link from the activity checklist")],
                 recorded_by="fb-activity-check")
    return 1
