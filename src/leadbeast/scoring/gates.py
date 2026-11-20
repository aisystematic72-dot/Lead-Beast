"""Pass/fail gates, evaluated before any score (rule 3)."""

from __future__ import annotations

from typing import Any

from leadbeast.domain.scoring import GapType, GateResult
from leadbeast.evidence.facts import Facts
from leadbeast.packs.schema import PackSpec
from leadbeast.scoring.parts import weighted_value


def vitality(facts: Facts, pack: PackSpec) -> GateResult:
    g = pack.gates.vitality
    if any(facts.is_yes(s) for s in g.closure_signals):
        return GateResult("vitality", False, 0, 1, "closure_signal")
    if facts.is_no("open_status"):
        return GateResult("vitality", False, 0, 1, "not_open")
    if any(facts.is_yes(s) for s in g.activity_signals):
        return GateResult("vitality", True, 1, 1)
    return GateResult("vitality", False, None, 1, "unknown")


def service_fit(gaps: set[GapType], offer_profile: dict[str, float], pack: PackSpec) -> GateResult:
    t = pack.gates.service_fit_threshold
    if not gaps:
        return GateResult("service_fit", False, 0, t, "no_gap")
    fit = max(offer_profile.get(g.value, 0.0) for g in gaps)
    return GateResult("service_fit", fit >= t, fit, t, "" if fit >= t else "no_suitable_offer")


def winnability(facts: Facts, pack: PackSpec) -> GateResult:
    w = pack.gates.winnability
    v = weighted_value(w.signals, facts) if w.signals else None
    if v is None:
        return GateResult("winnability", False, None, w.threshold, "unknown")
    return GateResult("winnability", v >= w.threshold, round(v, 1), w.threshold)


def deliverability(contact: dict[str, Any] | None, pack: PackSpec) -> GateResult:
    if not contact or not contact.get("usable"):
        return GateResult("deliverability", False, 0, 1, "no_usable_contact")
    ch = contact.get("channel")
    if ch not in pack.policy.allowed_channels:
        return GateResult("deliverability", False, 0, 1, f"channel_not_allowed:{ch}")
    if contact.get("suppressed"):
        return GateResult("deliverability", False, 0, 1, "suppressed")
    return GateResult("deliverability", True, 1, 1)
