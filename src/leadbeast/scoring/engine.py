"""score(facts, pack) -> ScoreResult. Pure, deterministic, explainable. Step order is fixed.

1 gates, 2 part scores, 3 coverage, 4 part totals, 5 confidence, 6 total, 7 class, 8 reasons.
"""

from __future__ import annotations

from typing import Any

from leadbeast.domain.evidence import State
from leadbeast.domain.scoring import Confidence, GateResult, PriorityClass, Reason, ScoreResult
from leadbeast.evidence.facts import Facts
from leadbeast.packs.schema import PART_NAMES, PackSpec
from leadbeast.scoring import gates as G
from leadbeast.scoring.gaps import gap_types
from leadbeast.scoring.parts import PartResult, score_part


def confidence_for(
    coverage: dict[str, float], facts: Facts, pack: PackSpec, signals: list[str]
) -> Confidence:
    weights = {p: pack.parts[p].max_points for p in PART_NAMES}
    avg = sum(coverage[p] * weights[p] for p in PART_NAMES) / sum(weights.values())
    rules = pack.confidence
    if avg >= rules.high_min_coverage:
        c = Confidence.HIGH
    elif avg >= rules.medium_min_coverage:
        c = Confidence.MEDIUM
    else:
        c = Confidence.LOW
    # source agreement: any open conflict caps confidence at low
    if any(facts.state(s) is State.CONFLICTING for s in signals):
        return Confidence.LOW
    # (inferred-only evidence is handled in classify: it can never lift a business to A)
    # manual verification of a key signal raises it one step
    if any(facts.state(s) is State.MANUALLY_VERIFIED for s in pack.key_signals()):
        c = Confidence.HIGH if c is Confidence.MEDIUM else (Confidence.MEDIUM if c is Confidence.LOW else c)
    return c


def _top_reasons(parts: dict[str, PartResult], n: int = 4) -> list[Reason]:
    allr = [r for p in parts.values() for r in p.reasons]
    pos = sorted((r for r in allr if r.direction == "positive"), key=lambda r: (-r.points, r.signal))
    neg = sorted((r for r in allr if r.direction == "negative"), key=lambda r: (r.points, r.signal))
    miss = sorted((r for r in allr if r.direction == "missing"), key=lambda r: (r.points, r.signal))
    return pos[:n] + neg[:n] + miss[:n]


def score(
    facts: Facts,
    pack: PackSpec,
    offer_profile: dict[str, float] | None = None,
    contact: dict[str, Any] | None = None,
) -> ScoreResult:
    offer_profile = offer_profile or {}
    signals = pack.all_signals()

    # 1. Gates first.
    gaps = gap_types(facts, pack.gap_thresholds)
    vit = G.vitality(facts, pack)
    fit = G.service_fit(gaps, offer_profile, pack)
    win = G.winnability(facts, pack)
    dlv = G.deliverability(contact, pack)
    gate_list: list[GateResult] = [vit, fit, win, dlv]
    gap_list = sorted(g.value for g in gaps)

    if not vit.passed and vit.reason != "unknown":
        return ScoreResult(
            need=0, ability=0, timing=0, reach=0,
            coverage={p: 0.0 for p in PART_NAMES}, confidence=Confidence.LOW.value, total=0.0,
            cls=PriorityClass.D.value, gates=gate_list, reasons=[], pack_version=pack.version,
            reason_codes=["vitality_fail", vit.reason], gap_types=gap_list,
        )

    # 2-4. Parts and coverage.
    parts = {name: score_part(name, pack.parts[name], facts) for name in PART_NAMES}
    coverage = {name: round(p.coverage, 4) for name, p in parts.items()}

    # 5. Confidence.
    conf = confidence_for(coverage, facts, pack, signals)

    # 6. Total.
    raw = sum(p.points for p in parts.values())
    total = round(raw * conf.multiplier, 1)

    # 7. Priority class.
    cls, codes = classify(facts, pack, parts, coverage, conf, total, gate_list, gaps)

    return ScoreResult(
        need=round(parts["need"].points, 2), ability=round(parts["ability"].points, 2),
        timing=round(parts["timing"].points, 2), reach=round(parts["reach"].points, 2),
        coverage=coverage, confidence=conf.value, total=total, cls=cls.value, gates=gate_list,
        reasons=_top_reasons(parts), pack_version=pack.version, reason_codes=codes,
        gap_types=gap_list,
    )


def classify(
    facts: Facts,
    pack: PackSpec,
    parts: dict[str, PartResult],
    coverage: dict[str, float],
    conf: Confidence,
    total: float,
    gate_list: list[GateResult],
    gaps: set[Any],
) -> tuple[PriorityClass, list[str]]:
    g = {x.gate: x for x in gate_list}
    th = pack.thresholds
    a = th.class_a

    # Review takes precedence over A, B and C.
    review: list[str] = []
    if facts.conflicts():
        review.append("conflicting_evidence")
    if facts.meta.get("identity_conflict"):
        review.append("conflicting_identity")
    if g["vitality"].reason == "unknown":
        review.append("vitality_unknown")
    stale_keys = [s for s in pack.key_signals() if facts.state(s) is State.STALE]
    if stale_keys:
        review.append("stale_key_signal")
    if any(facts.state(s) is State.INFERRED for s in pack.high_impact_signals()):
        review.append("high_impact_claim_inferred")
    if coverage["need"] < th.review_min_coverage:
        review.append("low_coverage")
    if review:
        return PriorityClass.REVIEW, review

    # D: no marketing problem, or no suitable offer.
    if not gaps:
        return PriorityClass.D, ["not_marketing_problem"]
    if not g["service_fit"].passed:
        return PriorityClass.D, ["no_suitable_offer"]

    # A requires everything.
    blockers: list[str] = []
    if total < a.score:
        blockers.append("score_below_a")
    if coverage["need"] < a.coverage_need or coverage["ability"] < a.coverage_ability:
        blockers.append("missing_evidence_layer")
    if coverage["reach"] < a.coverage_reach:
        blockers.append("weak_reach_coverage")
    if conf.order < Confidence(a.min_confidence).order:
        blockers.append("low_confidence")
    if (g["service_fit"].value or 0) < a.service_fit:
        blockers.append("weak_service_fit")
    if not g["winnability"].passed:
        blockers.append("winnability")
    if not g["deliverability"].passed:
        blockers.append("weaker_contact")
    if parts["need"].observed_contributors == 0:
        blockers.append("inferred_only")  # inferred facts alone cannot lift to A
    if not blockers:
        return PriorityClass.A, ["all_gates_passed"]

    if total >= th.class_b_score:
        return PriorityClass.B, blockers
    if total >= th.class_c_score:
        return PriorityClass.C, ["score_40_54"]
    return PriorityClass.C, ["no_current_reason"]
