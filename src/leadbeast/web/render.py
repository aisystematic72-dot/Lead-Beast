"""Template helpers shared by web routes and the report job."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.db import models as m
from leadbeast.domain.scoring import GapType

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
GAP_LABELS = {g.value: g.label for g in GapType}
TEMPLATES.env.globals["GAP_LABELS"] = GAP_LABELS
TEMPLATES.env.filters["money"] = lambda c: f"${(c or 0) / 100:,.2f}"
TEMPLATES.env.filters["pct"] = lambda x: f"{round((x or 0) * 100)}%"

# Pack defaults, used when a snapshot's pack is not at hand.
PART_MAX = {"need": 40, "ability": 30, "timing": 10, "reach": 20}
PART_LABELS = {"need": "Need", "ability": "Ability to pay", "timing": "Timing", "reach": "Reachability"}
CLASS_LABELS = {"A": "Class A", "B": "Class B", "C": "Class C", "D": "Class D", "review": "Review"}
STATE_LABELS = {
    "observed_yes": "Observed: Yes", "observed_no": "Observed: No", "manually_verified": "Verified",
    "not_detected": "Not detected", "inferred": "Inferred", "unknown": "Unknown",
    "stale": "Stale", "conflicting": "Conflicting",
}


def humanize(s: object) -> str:
    t = str(s or "").replace("_", " ").strip()
    return t[:1].upper() + t[1:]


def coverage_pct(cov: dict[str, float] | None, maxes: dict[str, int] | None = None) -> str:
    """Coverage weighted by part size, as the engine does for confidence."""
    if not cov:
        return "n/a"
    w = maxes or PART_MAX
    den = sum(w.get(p, 0) for p in cov)
    if not den:
        return "n/a"
    return f"{round(sum(float(v) * w.get(p, 0) for p, v in cov.items()) / den * 100)}%"


def fmt_value(v: object) -> str:
    """Evidence values and JSON details as short readable text."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, dict):
        return " · ".join(f"{humanize(k)}: {fmt_value(x)}" for k, x in v.items())
    if isinstance(v, list | tuple | set):
        return ", ".join(fmt_value(x) for x in v)
    return str(v)


def age(days: int | None) -> str:
    if days is None:
        return "n/a"
    if days <= 0:
        return "today"
    return "1 day" if days == 1 else f"{days} days"


TEMPLATES.env.globals.update(PART_MAX=PART_MAX, PART_LABELS=PART_LABELS, CLASS_LABELS=CLASS_LABELS,
                             STATE_LABELS=STATE_LABELS)
TEMPLATES.env.filters.update(humanize=humanize, coverage=coverage_pct, age=age, fmt=fmt_value)


def _social_label(raw: object) -> str:
    from leadbeast.market.queries import social_activity_text

    return social_activity_text(raw if isinstance(raw, str) else None)


TEMPLATES.env.filters["social_label"] = _social_label


def _alert_reason(aspect: object) -> str:
    """Approved reason wording for an alert aspect (the fixed phrase table), without the prefix."""
    from leadbeast.alerts.render import PHRASES, phrase_for

    a = str(aspect or "")
    if a not in PHRASES:
        return a.replace("_", " ")
    return phrase_for(a).removeprefix("Time-sensitive: ")


TEMPLATES.env.filters["alert_reason"] = _alert_reason


async def audit_context(s: AsyncSession, audit: m.Audit) -> dict[str, Any]:
    b = await s.get(m.Business, audit.business_id)
    assert b is not None
    snap = await s.get(m.ScoreSnapshot, audit.snapshot_id) if audit.snapshot_id else None
    return {
        "id": str(audit.id), "status": audit.status,
        "business": {"name": b.canonical_name, "trade": b.trade,
                     "city": (b.address or {}).get("city"), "website": b.website_url},
        "findings": audit.findings.get("items", []),
        "gap_types": [GAP_LABELS.get(g, g) for g in audit.findings.get("gap_types", [])],
        "peers": audit.peer_set, "payback": audit.payback, "open_items": audit.open_items,
        "score": {"total": float(snap.total), "class": snap.cls} if snap else None,
    }
