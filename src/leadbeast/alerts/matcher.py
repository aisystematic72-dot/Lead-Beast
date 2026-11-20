"""decide(business_state, prev, watch, precision) -> MatchDecision. Pure."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime

from leadbeast.alerts.models import BusinessState, MatchDecision, PressureState, WatchRule

CLASS_RANK = {"D": 0, "C": 1, "REVIEW": 2, "review": 2, "B": 3, "A": 4}
ESCALATION_POINTS = 15


def class_rank(c: str) -> int:
    return CLASS_RANK.get(c, 0)


def _area_match(b: BusinessState, kind: str, v: str) -> bool:
    if kind == "city":
        return (b.city or "").lower() == v.split(",")[0].strip()
    if kind == "zip":
        return (b.postcode or "").startswith(v)
    if kind == "state":
        return (b.state or "").lower() == v
    return False


def in_scope(b: BusinessState, w: WatchRule) -> bool:
    if w.industries and (b.industry or "") not in w.industries:
        return False
    if w.areas and not any(_area_match(b, a.kind, a.value.lower()) for a in w.areas):
        return False
    if w.problems and not set(w.problems) & set(b.problems):
        return False
    return not (w.technology and not set(w.technology) & set(b.technology))


def top_aspect(p: PressureState, wanted: list[str]) -> str | None:
    items = [(v, k) for k, v in p.aspects.items() if (not wanted or k in wanted) and v > 0]
    return max(items)[1] if items else None


def classify_event(prev: PressureState | None, snap: PressureState) -> str | None:
    if prev is None:
        return "new_match"
    if prev.direction == "fading" and snap.direction != "fading":
        return "reopen"
    if snap.index - prev.index >= ESCALATION_POINTS or (
            prev.direction == "steady" and snap.direction == "rising"):
        return "escalation"
    if set(snap.trigger_ids) - set(prev.trigger_ids):
        return "new_trigger"
    return None


def is_unverified(aspect: str, precision: Mapping[str, float],
                  operator_verified: frozenset[str] = frozenset()) -> bool:
    """An aspect is verified when its evidence was checked by the operator, or when its
    detector signal reached the golden-set precision bar (0.90)."""
    return aspect not in operator_verified and precision.get(aspect, 0.0) < 0.90


def decide(biz: BusinessState, prev: PressureState | None, w: WatchRule,
           precision: Mapping[str, float],
           operator_verified: frozenset[str] = frozenset()) -> MatchDecision:
    if not (biz.vitality_pass and biz.can_pay):
        return MatchDecision(matched=False, reason="gate")
    if not in_scope(biz, w):
        return MatchDecision(matched=False, reason="scope")
    snap = biz.pressure
    if snap.index < w.min_pressure or (w.rising_only and snap.direction != "rising"):
        return MatchDecision(matched=False, reason="below_threshold")
    if class_rank(biz.priority_class) < class_rank(w.min_class):
        return MatchDecision(matched=False, reason="class")
    aspect = top_aspect(snap, w.aspects)
    if aspect is None:
        return MatchDecision(matched=False, reason="aspect")
    event = classify_event(prev, snap)
    if event is None:
        return MatchDecision(matched=False, reason="no_change")
    return MatchDecision(matched=True, event=event, aspect=aspect,  # type: ignore[arg-type]
                         unverified=is_unverified(aspect, precision, operator_verified),
                         reason="ok")


def dedupe_key(business_id: str, aspect: str, event: str, at: datetime, cooldown_days: int,
               pressure: float | None = None) -> str:
    bucket = math.floor(at.timestamp() / (cooldown_days * 86400))
    key = f"{business_id}:{aspect}:{event}:{bucket}"
    if event == "escalation" and pressure is not None:
        key += f":band{int(pressure // ESCALATION_POINTS)}"
    return key
