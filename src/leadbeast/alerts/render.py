"""Lock-screen-safe alert cards. Reason lines come only from a fixed phrase table."""

from __future__ import annotations

import re
from datetime import datetime

from leadbeast.alerts.models import AlertCard, CardAction

PHRASES: dict[str, str] = {
    "enquiries": "Time-sensitive: enquiries appear to have slowed",
    "reviews": "Time-sensitive: recent review activity changed",
    "hiring": "Time-sensitive: actively hiring for this role",
    "site": "Time-sensitive: website issue detected",
    "permit": "Time-sensitive: permit activity changed",
    "listing": "Time-sensitive: listing details changed",
    "ranking": "Time-sensitive: local ranking moved",
    "activity": "Time-sensitive: online activity went quiet",
    "registry": "Time-sensitive: registry change recorded",
    "delivery_stuck": "Time-sensitive: service delivery appears delayed",
    "default": "Time-sensitive: pressure rising",
}
BANNED = ("desperate", "failing", "struggling", "bankrupt", "dying", "going under", "crisis")
CONTACT_RE = re.compile(r"(@|\+?\d[\d\s().-]{7,}\d)")


def phrase_for(aspect: str) -> str:
    return PHRASES.get(aspect, PHRASES["default"])


def ago(at: datetime, now: datetime) -> str:
    mins = int((now - at).total_seconds() // 60)
    if mins < 60:
        return f"{mins}m ago"
    if mins < 1440:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def card(*, alert_id: str, business_name: str, industry: str | None, area: str | None,
         aspect: str, pressure: int, direction: str, klass: str, evidence_count: int,
         confidence: str, created_at: datetime, now: datetime, base_url: str) -> AlertCard:
    title = " · ".join(x for x in (business_name, industry, area) if x)
    title = CONTACT_RE.sub("", title).strip()  # never a phone or email on a lock screen
    url = f"{base_url}/business/{alert_id}"
    return AlertCard(
        title=title, line=phrase_for(aspect), pressure=pressure, direction=direction,
        klass=klass, evidence_count=evidence_count, confidence=confidence,
        detected_ago=ago(created_at, now), url=url,
        actions=[CardAction(label=a, url=f"{base_url}/alerts/{alert_id}/feedback?kind={k}")
                 for a, k in (("Not a fit", "not_a_fit"), ("Snooze", "snooze"),
                              ("Mute watch", "mute_watch"), ("Acted", "acted"))],
    )
