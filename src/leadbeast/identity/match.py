"""Blocking, comparison and decision. Pure. Favours a missed merge over a bad one."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from rapidfuzz import fuzz

from leadbeast.identity import normalise as N


class Candidate(Protocol):
    norm_name: str
    phone_e164: str | None
    domain: str | None
    postcode: str | None
    lat: float | None
    lon: float | None
    trade: str | None
    address_key: str


@dataclass
class Rec:
    key: str
    norm_name: str
    phone_e164: str | None = None
    domain: str | None = None
    postcode: str | None = None
    lat: float | None = None
    lon: float | None = None
    trade: str | None = None
    address_key: str = ""


def blocking_keys(r: Candidate) -> set[str]:
    keys: set[str] = set()
    if r.phone_e164:
        keys.add("p:" + r.phone_e164)
    if r.domain and not N.is_webmail("x@" + r.domain):
        keys.add("d:" + r.domain)
    if r.postcode and r.norm_name:
        keys.add(f"z:{r.postcode}:{N.first_token(r.norm_name)}")
    return keys


def blocks(records: list[Rec]) -> dict[str, list[Rec]]:
    out: dict[str, list[Rec]] = defaultdict(list)
    for r in records:
        for k in blocking_keys(r):
            out[k].append(r)
    return out


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(
        (lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class MatchScore:
    score: float
    rule: str
    conflict: bool = False


def compare(a: Candidate, b: Candidate) -> MatchScore:
    """Weighted evidence of sameness, 0..1. Strong keys: exact phone, exact domain."""
    parts: list[tuple[float, float, str]] = []  # (weight, similarity, label)
    if a.phone_e164 and b.phone_e164:
        parts.append((3.0, 1.0 if a.phone_e164 == b.phone_e164 else 0.0, "phone"))
    if a.domain and b.domain:
        parts.append((3.0, 1.0 if a.domain == b.domain else 0.0, "domain"))
    name_sim = fuzz.token_sort_ratio(a.norm_name, b.norm_name) / 100.0
    parts.append((2.0, name_sim, "name"))
    if a.address_key and b.address_key:
        parts.append((1.5, fuzz.token_set_ratio(a.address_key, b.address_key) / 100.0, "address"))
    if a.lat is not None and b.lat is not None and a.lon is not None and b.lon is not None:
        d = haversine_km((a.lat, a.lon), (b.lat, b.lon))
        parts.append((1.0, max(0.0, 1.0 - d / 2.0), "geo"))  # 0 at 2 km apart
    total_w = sum(w for w, _, _ in parts)
    score = sum(w * s for w, s, _ in parts) / total_w if total_w else 0.0
    strong = [lbl for w, s, lbl in parts if lbl in ("phone", "domain") and s == 1.0]
    # Same strong key but a very different name is suspicious: never auto-merge on it alone.
    if strong and name_sim < 0.5:
        score = min(score, 0.80)
    rule = "+".join(strong) if strong else ("name+address" if name_sim > 0.9 else "fuzzy")
    conflict = bool(a.trade and b.trade and a.trade != b.trade and score >= 0.7)
    return MatchScore(round(score, 4), rule, conflict)


class Verdict(StrEnum):
    MERGE = "merge"
    REVIEW = "review"
    SEPARATE = "separate"


def decide(ms: MatchScore, merge_at: float, review_at: float) -> Verdict:
    if ms.conflict:
        return Verdict.REVIEW
    if ms.score >= merge_at:
        return Verdict.MERGE
    if ms.score >= review_at:
        return Verdict.REVIEW
    return Verdict.SEPARATE
