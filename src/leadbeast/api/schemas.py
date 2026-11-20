from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from leadbeast.packs.schema import Offer


class Error(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class CampaignIn(BaseModel):
    name: str
    offer_id: UUID | None = None
    offer: Offer | None = None
    pack: str = "visibility_gap"
    area: str
    size_min: int | None = None
    size_max: int | None = None
    budget_cap_cents: int = 0
    target_by: dict[str, Any] = {}
    partner: bool = False


class CampaignOut(BaseModel):
    id: UUID
    name: str
    offer_id: UUID
    pack_id: UUID
    area: str
    status: str


class LaunchIn(BaseModel):
    source: Literal["csv", "osm"] = "osm"
    path: str | None = None
    limit: int = Field(500, ge=1, le=10000)
    sample: int = Field(50, ge=1, le=500)


class BusinessRow(BaseModel):
    id: UUID
    canonical_name: str
    trade: str | None
    city: str | None
    cls: str | None = Field(None, alias="class")
    total: float | None
    confidence: str | None
    gap_types: list[str] = []
    pressure: float | None = None
    pressure_dir: str | None = None

    model_config = {"populate_by_name": True}


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None


class EvidenceIn(BaseModel):
    signal: str
    value: Any = True
    note: str | None = None
    supersedes_id: UUID | None = None


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    edited_body: str | None = None


class MysteryIn(BaseModel):
    searched: bool
    called: bool
    site_opened: bool
    passed: bool
    notes: str | None = None


class OutcomeIn(BaseModel):
    business_id: UUID
    campaign_id: UUID | None = None
    result: Literal["won", "lost", "no_reply", "replied", "meeting"]
    reason_code: str | None = None
    value: float | None = None


class InboundIn(BaseModel):
    provider_id: str
    in_reply_to: str | None = None
    sender: str
    body: str = ""
    at: datetime | None = None
    kind: Literal["reply", "bounce_hard", "bounce_soft", "complaint", "unsubscribe"] = "reply"


class SuppressionIn(BaseModel):
    kind: Literal["email", "phone", "domain", "business"]
    value: str
    reason: str = "manual"


class WatchIn(BaseModel):
    name: str
    rule: dict[str, Any]
    style: Literal["instant", "hourly", "daily"] = "instant"
    channel_order: list[UUID] = []
    quiet_start: str | None = None
    quiet_end: str | None = None
    tz: str = "America/New_York"
    breakthrough_min: int | None = None
    cap_per_hour: int = 5
    cap_per_day: int = 20
    cooldown_days: int = 7


class ChannelIn(BaseModel):
    kind: Literal["inapp", "ntfy", "telegram", "webhook"]
    label: str
    config: dict[str, Any] = {}


class ReviewResolveIn(BaseModel):
    resolution: Literal["cleared", "merge", "keep_separate", "suppress"]
