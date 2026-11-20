"""PackSpec: an ICP pack is data. Code never hardcodes a trade or a weight (rule 2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PART_NAMES = ("need", "ability", "timing", "reach")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NumericMap(_Strict):
    lo: float
    hi: float
    invert: bool = False

    def apply(self, x: float) -> float:
        if self.hi == self.lo:
            return 1.0 if x >= self.hi else 0.0
        v = (x - self.lo) / (self.hi - self.lo)
        v = max(0.0, min(1.0, v))
        return 1.0 - v if self.invert else v


class SignalSpec(_Strict):
    signal: str
    weight: float = Field(gt=0)
    when_yes: float | None = Field(default=None, ge=0, le=1)
    when_no: float | None = Field(default=None, ge=0, le=1)
    # None means not_detected counts as missing coverage (spec section 10, step 2)
    when_not_detected: float | None = Field(default=None, ge=0, le=1)
    numeric: NumericMap | None = None
    key: bool = False  # a stale key signal sends the business to review
    high_impact: bool = False  # an inferred-only value here sends to review

    @model_validator(mode="after")
    def _has_mapping(self) -> SignalSpec:
        if self.numeric is None and self.when_yes is None and self.when_no is None:
            raise ValueError(f"signal {self.signal}: needs when_yes/when_no or numeric")
        return self


class PartSpec(_Strict):
    max_points: float = Field(gt=0)
    signals: list[SignalSpec]


class ClassAThresholds(_Strict):
    score: float = 70
    coverage_need: float = 0.6
    coverage_ability: float = 0.6
    coverage_reach: float = 0.5
    min_confidence: Literal["high", "medium", "low"] = "medium"
    service_fit: float = 60
    winnability: float = 40


class Thresholds(_Strict):
    class_a: ClassAThresholds = ClassAThresholds()
    class_b_score: float = 55
    class_c_score: float = 40
    review_min_coverage: float = 0.3


class VitalityGate(_Strict):
    closure_signals: list[str] = ["closure_signals"]
    activity_signals: list[str] = ["recent_activity", "open_status"]


class WinnabilityGate(_Strict):
    threshold: float = 40
    signals: list[SignalSpec] = []


class Gates(_Strict):
    vitality: VitalityGate = VitalityGate()
    service_fit_threshold: float = 60
    winnability: WinnabilityGate = WinnabilityGate()


class ConfidenceRules(_Strict):
    high_min_coverage: float = 0.75
    medium_min_coverage: float = 0.5


class GapThresholds(_Strict):
    website_quality_weak: float = 0.5
    review_count_low: float = 10


class PolicySpec(_Strict):
    daily_send_cap: int = 20
    quiet_start: str = "20:00"
    quiet_end: str = "08:00"
    allowed_channels: list[str] = ["email"]


class Symptom(_Strict):
    signal: str
    weight: float = Field(gt=0)
    polarity: Literal["supports", "contradicts"] = "supports"


class ProblemSpec(_Strict):
    id: str
    name: str
    areas: list[str]
    kind: Literal["structural", "acute"]
    half_life_days: int | None = None
    min_evidence: int = 1
    use_in_message: bool = False
    symptoms: list[Symptom]

    @model_validator(mode="after")
    def _acute_has_half_life(self) -> ProblemSpec:
        if self.kind == "acute" and not self.half_life_days:
            raise ValueError(f"problem {self.id}: acute problems need half_life_days")
        return self


class TriggerSpec(_Strict):
    id: str
    weight: float
    half_life_days: int


class PressureSpec(_Strict):
    use_in_message: Literal[False] = False  # pressure signals never appear in a draft
    financial_strain: Literal["off", "review_only", "ability_only"] = "off"
    triggers: list[TriggerSpec] = []


class Templates(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    opener_email_subject: str = "A quick note about {business_name}"
    opener_email: str = ""
    report_intro: str = ""


class PackSpec(_Strict):
    name: str
    version: str
    description: str = ""
    size_min: int = 1
    size_max: int = 25
    trades: list[str] = []
    detectors: list[str]
    freshness_days: dict[str, int] = {}
    parts: dict[str, PartSpec]
    thresholds: Thresholds = Thresholds()
    gates: Gates = Gates()
    confidence: ConfidenceRules = ConfidenceRules()
    gap_thresholds: GapThresholds = GapThresholds()
    policy: PolicySpec = PolicySpec()
    templates: Templates = Templates()
    peer_radius_km: float = 25
    problems: list[ProblemSpec] = []
    pressure: PressureSpec = PressureSpec()

    @model_validator(mode="after")
    def _parts(self) -> PackSpec:
        missing = [p for p in PART_NAMES if p not in self.parts]
        extra = [p for p in self.parts if p not in PART_NAMES]
        if missing or extra:
            raise ValueError(f"parts must be exactly {PART_NAMES}; missing={missing} extra={extra}")
        return self

    @property
    def max_total(self) -> float:
        return sum(p.max_points for p in self.parts.values())

    def all_signals(self) -> list[str]:
        out = {s.signal for p in self.parts.values() for s in p.signals}
        out |= {s.signal for s in self.gates.winnability.signals}
        out |= set(self.gates.vitality.closure_signals) | set(self.gates.vitality.activity_signals)
        return sorted(out)

    def key_signals(self) -> list[str]:
        return sorted({s.signal for p in self.parts.values() for s in p.signals if s.key})

    def high_impact_signals(self) -> list[str]:
        return sorted(
            {s.signal for p in self.parts.values() for s in p.signals if s.high_impact}
        )


class Offer(BaseModel):
    """What is sold (rule 1: every campaign starts from an offer)."""

    id: str | None = None
    name: str
    monthly_fee: float = Field(gt=0)
    customer_value_low: float = Field(ge=0)
    customer_value_high: float = Field(ge=0)
    # gap type letter -> fit 0..100, e.g. {"A": 90, "B": 80, "C": 65, "D": 55}
    service_fit_profile: dict[str, float] = {}
    competes_with: list[str] = []
    provisional: bool = True

    @model_validator(mode="after")
    def _range(self) -> Offer:
        if self.customer_value_high < self.customer_value_low:
            raise ValueError("customer_value_high must be >= customer_value_low")
        return self
