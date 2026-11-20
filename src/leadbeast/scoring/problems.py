"""Problem strength and pressure index (spec section 19.5). Pure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from leadbeast.domain.evidence import OBSERVED_OR_MANUAL
from leadbeast.domain.scoring import Confidence
from leadbeast.evidence.facts import Facts
from leadbeast.packs.schema import ProblemSpec


def _symptom_value(facts: Facts, signal: str, polarity: str) -> float:
    present = facts.is_yes(signal)
    if polarity == "supports":
        return 1.0 if present else 0.0
    return 0.0 if present else 1.0


def problem_strength(facts: Facts, p: ProblemSpec) -> tuple[float, Confidence]:
    used = [s for s in p.symptoms if facts.usable(s.signal)]
    if len(used) < p.min_evidence:
        return 0.0, Confidence.LOW
    num = sum(s.weight * _symptom_value(facts, s.signal, s.polarity) for s in used)
    den = sum(s.weight for s in used)
    coverage = den / sum(s.weight for s in p.symptoms)
    observed = sum(1 for s in used if facts.state(s.signal) in OBSERVED_OR_MANUAL)
    if coverage >= 0.75 and observed == len(used):
        conf = Confidence.HIGH
    elif coverage >= 0.5 and observed > 0:
        conf = Confidence.MEDIUM
    else:
        conf = Confidence.LOW
    return (num / den) * coverage, conf


@dataclass(frozen=True)
class Trigger:
    id: str
    weight: float
    half_life_days: int
    observed_at: datetime


def pressure_index(triggers: list[Trigger], now: datetime) -> float:
    total: float = sum(
        (t.weight * 0.5 ** (max((now - t.observed_at).total_seconds(), 0.0) / 86400 / t.half_life_days)
         for t in triggers),
        0.0,
    )
    return min(100.0, round(total, 2))


def direction(today: float, two_weeks_ago: float | None, band: float = 5.0) -> str:
    if two_weeks_ago is None or today - two_weeks_ago > band:
        return "rising" if today > 0 else "steady"
    if two_weeks_ago - today > band:
        return "fading"
    return "steady"
