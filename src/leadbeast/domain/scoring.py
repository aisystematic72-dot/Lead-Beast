from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def multiplier(self) -> float:
        return {"high": 1.0, "medium": 0.7, "low": 0.4}[self.value]

    @property
    def order(self) -> int:
        return {"high": 2, "medium": 1, "low": 0}[self.value]


class PriorityClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    REVIEW = "review"


class GapType(StrEnum):
    INVISIBLE = "A"
    WEAK_SITE = "B"
    UNRANKED = "C"
    MARKETING_INACTIVE = "D"

    @property
    def label(self) -> str:
        return {
            "A": "Invisible", "B": "Weak site", "C": "Unranked", "D": "Marketing inactive",
        }[self.value]


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    value: float | None
    threshold: float | None
    reason: str = ""


@dataclass(frozen=True)
class Reason:
    signal: str
    part: str
    direction: str  # "positive" | "negative" | "missing"
    points: float
    evidence_id: UUID | None = None
    note: str = ""


@dataclass(frozen=True)
class ScoreResult:
    need: float
    ability: float
    timing: float
    reach: float
    coverage: dict[str, float]
    confidence: str
    total: float
    cls: str
    gates: list[GateResult]
    reasons: list[Reason]
    pack_version: str
    reason_codes: list[str] = field(default_factory=list)
    gap_types: list[str] = field(default_factory=list)
