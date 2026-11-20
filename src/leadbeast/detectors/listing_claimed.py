"""Listing signals from the operator's own imported listing data (CSV / source payloads).

Map platforms forbid automated scraping, so this detector never fetches map pages. It reads
listing columns already present in source records and emits `unknown` when there are none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from leadbeast.core.clock import now
from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.domain.evidence import EvidenceEvent, State

TRUE = {"true", "yes", "y", "1", "claimed", "verified"}
FALSE = {"false", "no", "n", "0", "unclaimed", "not claimed"}
NO_POSTS = {"none", "no posts", "never", "no social", "no page", "n/a", "na", "-"}
SOCIAL_ACTIVE_DAYS = 90
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%b %d %Y", "%b %d, %Y", "%B %d, %Y",
                "%Y/%m/%d")


def parse_post_date(v: Any) -> datetime | None:
    s = str(v or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


FB_ACTIVE = {"active"}
FB_QUIET = {"irregular", "inactive", "unavailable"}


def social_activity(rec: dict[str, Any]) -> tuple[State, Any, str] | None:
    """Operator-noted social activity -> social_active. None when the row says nothing about it.

    A Facebook verdict from `leadbeast social fb-analyze` (posting gaps over the last posts) wins
    over the bare last-post-date rule, and its summary becomes the evidence note.
    """
    verdict = str(rec.get("facebook_activity", "")).strip().lower()
    if verdict in FB_ACTIVE | FB_QUIET:
        note = str(rec.get("facebook_summary") or "").strip() or f"Facebook: {verdict}"
        active = verdict in FB_ACTIVE
        return State.OBSERVED_YES if active else State.OBSERVED_NO, active, note[:500]
    raw = str(rec.get("last_post_date", "")).strip()
    pages = [str(rec.get(k) or "").strip() for k in ("facebook", "instagram")]
    if not raw:
        return None
    if raw.lower() in NO_POSTS:
        has_pages = any(p and p.lower() not in NO_POSTS for p in pages)
        return State.OBSERVED_NO, False, "pages exist but no posts" if has_pages else "no social pages"
    d = parse_post_date(raw)
    if d is None:
        return None
    age = (now().replace(tzinfo=None) - d).days
    active = age <= SOCIAL_ACTIVE_DAYS
    return (State.OBSERVED_YES if active else State.OBSERVED_NO, active,
            f"last post {d.date()} ({age} days ago), followers {rec.get('followers') or 'n/a'}")


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


class ListingClaimed(BaseDetector):
    name = "listing_claimed"
    version = "1.1.0"
    produces = ("listing_claimed", "photo_count", "hours_present", "review_count",
                "last_review_age", "staff_count", "years_in_business", "in_map_pack",
                "licence_active", "social_active")
    source = "listing_import"
    window_days = 30

    async def run(self, inp: DetectorInput, fetch: Any) -> list[EvidenceEvent]:
        out: list[EvidenceEvent] = []
        for rec in inp.records:
            tag = f"record:{rec.get('_record_id')}"
            src = f"listing:{rec.get('_source', 'import')}"
            claimed = str(rec.get("claimed", "")).strip().lower()
            if claimed in TRUE:
                out.append(self.ev(inp, "listing_claimed", State.OBSERVED_YES, True, url=tag,
                                   source=src))
            elif claimed in FALSE:
                out.append(self.ev(inp, "listing_claimed", State.OBSERVED_NO, False, url=tag,
                                   source=src))
            for col, sig in (("photo_count", "photo_count"), ("review_count", "review_count"),
                             ("staff_count", "staff_count"),
                             ("years_in_business", "years_in_business"),
                             ("last_review_age", "last_review_age")):
                n = _num(rec.get(col))
                if n is not None:
                    out.append(self.ev(inp, sig, State.OBSERVED_YES, n, url=tag, source=src))
            if "hours" in rec:
                has = bool(str(rec.get("hours") or "").strip())
                out.append(self.ev(inp, "hours_present",
                                   State.OBSERVED_YES if has else State.NOT_DETECTED,
                                   True if has else None, url=tag, source=src))
            lic = str(rec.get("licence_status", "")).strip().lower()
            if lic in ("active", "valid", "current", "yes", "true"):
                out.append(self.ev(inp, "licence_active", State.OBSERVED_YES, True, url=tag,
                                   source=src, span=f"licence: {lic}"))
            elif lic in ("expired", "revoked", "suspended", "inactive", "no", "false"):
                out.append(self.ev(inp, "licence_active", State.OBSERVED_NO, False, url=tag,
                                   source=src, span=f"licence: {lic}"))
            social = social_activity(rec)
            if social is not None:
                state, val, note = social
                out.append(self.ev(inp, "social_active", state, val, url=tag, source=src, span=note))
            rank = _num(rec.get("map_pack_rank"))
            if rank is not None:
                inpack = rank <= 3
                out.append(self.ev(inp, "in_map_pack",
                                   State.OBSERVED_YES if inpack else State.OBSERVED_NO, inpack,
                                   url=tag, source=src))
        return self.fill_unknown(inp, out, "no_listing_data")
