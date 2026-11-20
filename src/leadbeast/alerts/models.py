from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Area(BaseModel):
    kind: Literal["city", "zip", "county", "state"] = "city"
    value: str
    radius_km: float | None = None


class WatchRule(BaseModel):
    industries: list[str] = []
    areas: list[Area] = []
    aspects: list[str] = []
    problems: list[str] = []
    technology: list[str] = []
    min_pressure: int = Field(60, ge=0, le=100)
    rising_only: bool = True
    # D lets every class through (D < C < REVIEW < B < A, see matcher.CLASS_RANK)
    min_class: Literal["A", "B", "REVIEW", "C", "D"] = "B"
    # Gates are not fields. They are always applied (vitality, ability to pay).


class PressureState(BaseModel):
    index: float
    direction: Literal["rising", "steady", "fading"]
    aspects: dict[str, float] = {}  # aspect -> contribution
    trigger_ids: list[str] = []


class BusinessState(BaseModel):
    business_id: str
    vitality_pass: bool
    can_pay: bool
    priority_class: str
    industry: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    problems: list[str] = []
    technology: list[str] = []
    pressure: PressureState
    evidence_ids: list[str] = []


class MatchDecision(BaseModel):
    matched: bool
    event: Literal["new_match", "escalation", "new_trigger", "reopen"] | None = None
    aspect: str | None = None
    unverified: bool = False
    reason: str


class CardAction(BaseModel):
    label: str
    url: str


class AlertCard(BaseModel):
    title: str  # business, industry, area. No contact details
    line: str  # neutral reason, from a fixed phrase table
    pressure: int
    direction: str
    klass: str
    evidence_count: int
    confidence: str
    detected_ago: str
    url: str
    actions: list[CardAction] = []


class DeliveryResult(BaseModel):
    ok: bool
    detail: str = ""
