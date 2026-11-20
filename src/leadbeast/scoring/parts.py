"""Part scores and coverage.

part_p = M_p * (sum_{U} w v / sum_{U} w) * c_p,   c_p = sum_{U} w / sum_{P} w
"""

from __future__ import annotations

from dataclasses import dataclass

from leadbeast.domain.evidence import Resolved, State
from leadbeast.domain.scoring import Reason
from leadbeast.evidence.facts import Facts
from leadbeast.packs.schema import PartSpec, SignalSpec


def signal_value(spec: SignalSpec, r: Resolved) -> float | None:
    """Map a resolved signal to 0..1, or None when it counts as missing coverage."""
    st = r.state
    if st in (State.UNKNOWN, State.STALE, State.CONFLICTING):
        return None
    if st is State.NOT_DETECTED:
        if spec.when_not_detected is not None:
            return spec.when_not_detected
        return None
    if spec.numeric is not None:
        v = r.value
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, bool) or not isinstance(v, int | float):
            if st is State.OBSERVED_NO and spec.when_no is not None:
                return spec.when_no
            return None
        return spec.numeric.apply(float(v))
    if r.is_yes:
        return spec.when_yes
    if r.is_no:
        return spec.when_no
    return None


@dataclass(frozen=True)
class PartResult:
    points: float
    coverage: float
    reasons: list[Reason]
    observed_contributors: int


def score_part(name: str, part: PartSpec, facts: Facts) -> PartResult:
    total_w = sum(s.weight for s in part.signals)
    used_w = 0.0
    weighted = 0.0
    reasons: list[Reason] = []
    observed = 0
    for s in part.signals:
        r = facts.get(s.signal)
        v = signal_value(s, r)
        if v is None:
            reasons.append(Reason(
                signal=s.signal, part=name, direction="missing",
                points=-part.max_points * s.weight / total_w, evidence_id=r.event_id,
                note=r.state.value,
            ))
            continue
        used_w += s.weight
        weighted += s.weight * v
        if v > 0 and r.state in (State.OBSERVED_YES, State.OBSERVED_NO, State.MANUALLY_VERIFIED):
            observed += 1
        contrib = part.max_points * s.weight * v / total_w
        lost = part.max_points * s.weight * (1 - v) / total_w
        if contrib >= lost:
            reasons.append(Reason(s.signal, name, "positive", round(contrib, 2), r.event_id))
        else:
            reasons.append(Reason(s.signal, name, "negative", -round(lost, 2), r.event_id))
    coverage = used_w / total_w if total_w else 0.0
    points = part.max_points * (weighted / used_w) * coverage if used_w else 0.0
    return PartResult(points=points, coverage=coverage, reasons=reasons,
                      observed_contributors=observed)


def weighted_value(signals: list[SignalSpec], facts: Facts) -> float | None:
    """0..100 from a list of signals, None if none usable (used by winnability)."""
    used_w = 0.0
    acc = 0.0
    for s in signals:
        v = signal_value(s, facts.get(s.signal))
        if v is None:
            continue
        used_w += s.weight
        acc += s.weight * v
    return None if used_w == 0 else 100.0 * acc / used_w
