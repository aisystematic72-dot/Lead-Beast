"""SQLAlchemy 2 typed models. Observed (append-only evidence) is kept apart from concluded.

All primary keys are UUIDv7. All tables carry created_at; mutable tables carry updated_at.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from leadbeast.core.ids import uuid7

EVIDENCE_STATES = (
    "'observed_yes','observed_no','not_detected','unknown',"
    "'conflicting','inferred','manually_verified','stale'"
)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def fk(target: str, nullable: bool = False) -> Mapped[Any]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target), nullable=nullable, index=True)


# ---------------------------------------------------------------- setup


class Pack(Base):
    __tablename__ = "pack"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(40))
    spec: Mapped[dict[str, Any]]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()


class Offer(Base):
    __tablename__ = "offer"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200))
    monthly_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    customer_value_low: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    customer_value_high: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    service_fit_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    competes_with: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class Campaign(Base):
    __tablename__ = "campaign"
    __table_args__ = (
        CheckConstraint("status in ('draft','dry_run','running','paused','done')"),
    )
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200))
    offer_id: Mapped[uuid.UUID] = fk("offer.id")  # rule 1: required
    pack_id: Mapped[uuid.UUID] = fk("pack.id")
    area: Mapped[str] = mapped_column(String(200))
    size_min: Mapped[int] = mapped_column(Integer, default=1)
    size_max: Mapped[int] = mapped_column(Integer, default=25)
    budget_cap: Mapped[int] = mapped_column(Integer, default=0)  # cents
    target_by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    partner: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


# ---------------------------------------------------------------- identity


class Business(Base):
    __tablename__ = "business"
    id: Mapped[uuid.UUID] = pk()
    canonical_name: Mapped[str] = mapped_column(String(300))
    norm_name: Mapped[str] = mapped_column(String(300), index=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    website_url: Mapped[str | None] = mapped_column(Text)
    address: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    postcode: Mapped[str | None] = mapped_column(String(20), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    trade: Mapped[str | None] = mapped_column(String(100), index=True)
    size_band: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class CampaignBusiness(Base):
    __tablename__ = "campaign_business"
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign.id"), primary_key=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business.id"), primary_key=True
    )
    created_at: Mapped[datetime] = created()


class SourceRecord(Base):
    __tablename__ = "source_record"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[uuid.UUID] = pk()
    source: Mapped[str] = mapped_column(String(60))
    external_id: Mapped[str] = mapped_column(String(300))
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]]
    business_id: Mapped[uuid.UUID | None] = fk("business.id", nullable=True)
    created_at: Mapped[datetime] = created()


class IdentityLink(Base):
    __tablename__ = "identity_link"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    source_record_id: Mapped[uuid.UUID] = fk("source_record.id")
    match_score: Mapped[float] = mapped_column(Float)
    rule: Mapped[str] = mapped_column(String(100))
    merged_by: Mapped[str] = mapped_column(String(100), default="system")
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class ReviewItem(Base):
    """Things a person must decide: ambiguous merges, conflicts, review-class businesses."""

    __tablename__ = "review_item"
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(40))  # identity_match | conflict | class_review
    business_id: Mapped[uuid.UUID | None] = fk("business.id", nullable=True)
    source_record_id: Mapped[uuid.UUID | None] = fk("source_record.id", nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolution: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- evidence


class EvidenceEvent(Base):
    __tablename__ = "evidence_event"
    __table_args__ = (
        CheckConstraint(f"state in ({EVIDENCE_STATES})", name="evidence_state_valid"),
        CheckConstraint(
            "state <> 'inferred' or span is not null or source = 'rule'",
            name="inferred_needs_span",
        ),
        Index("ix_evidence_biz_signal_time", "business_id", "signal", "observed_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("business.id"))
    signal: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(20))
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(60))
    source_url: Mapped[str | None] = mapped_column(Text)
    span: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detector: Mapped[str] = mapped_column(String(80))
    detector_version: Mapped[str] = mapped_column(String(40))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_event.id")
    )
    recorded_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = created()


class DetectorRun(Base):
    __tablename__ = "detector_run"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    run_id: Mapped[str | None] = mapped_column(String(40))
    detector: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- conclusions


class ModelVersion(Base):
    __tablename__ = "model_version"
    __table_args__ = (CheckConstraint("kind in ('default','challenger')"),)
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(20))
    weights: Mapped[dict[str, Any]]
    trained_on_n: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="shadow")
    created_at: Mapped[datetime] = created()


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshot"
    __table_args__ = (
        CheckConstraint("confidence in ('high','medium','low')"),
        CheckConstraint("class in ('A','B','C','D','review')"),
        Index("ix_snapshot_biz_campaign_time", "business_id", "campaign_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("business.id"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaign.id"))
    pack_version: Mapped[str] = mapped_column(String(40))
    need: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    ability: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    timing: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    reach: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    coverage: Mapped[dict[str, Any]]
    confidence: Mapped[str] = mapped_column(String(10))
    total: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    cls: Mapped[str] = mapped_column("class", String(10))
    gap_types: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    reasons: Mapped[dict[str, Any]]
    model_version: Mapped[str] = mapped_column(String(40), default="pack-default")
    created_at: Mapped[datetime] = created()


class GateResultRow(Base):
    __tablename__ = "gate_result"
    __table_args__ = (
        CheckConstraint("gate in ('vitality','service_fit','winnability','deliverability')"),
    )
    id: Mapped[uuid.UUID] = pk()
    snapshot_id: Mapped[uuid.UUID] = fk("score_snapshot.id")
    gate: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool] = mapped_column(Boolean)
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- contacts and audit


class Contact(Base):
    __tablename__ = "contact"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    channel_pref: Mapped[str] = mapped_column(String(20), default="email")
    confidence: Mapped[str] = mapped_column(String(10), default="low")
    source_url: Mapped[str | None] = mapped_column(Text)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class ContactCheck(Base):
    __tablename__ = "contact_check"
    __table_args__ = (
        CheckConstraint("kind in ('syntax','mx','smtp_soft','phone_type','role','webmail')"),
    )
    id: Mapped[uuid.UUID] = pk()
    contact_id: Mapped[uuid.UUID] = fk("contact.id")
    kind: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(40))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    snapshot_id: Mapped[uuid.UUID | None] = fk("score_snapshot.id", nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready | insufficient
    findings: Mapped[dict[str, Any]]
    peer_set: Mapped[dict[str, Any]]
    payback: Mapped[dict[str, Any]]
    open_items: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    report_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- outreach


class OutreachDraft(Base):
    __tablename__ = "outreach_draft"
    __table_args__ = (CheckConstraint("status in ('draft','approved','rejected','sent','blocked')"),)
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True)
    contact_id: Mapped[uuid.UUID | None] = fk("contact.id", nullable=True)
    channel: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64))
    cited_evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    blocked_reasons: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[uuid.UUID] = pk()
    draft_id: Mapped[uuid.UUID] = fk("outreach_draft.id")
    user_id: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(20))  # approve | reject | edit
    edited_body: Mapped[str | None] = mapped_column(Text)
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class MysteryCheck(Base):
    __tablename__ = "mystery_check"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    user_id: Mapped[str] = mapped_column(String(100))
    searched: Mapped[bool] = mapped_column(Boolean, default=False)
    called: Mapped[bool] = mapped_column(Boolean, default=False)
    site_opened: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = created()


class Conversation(Base):
    __tablename__ = "conversation"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    contact_id: Mapped[uuid.UUID | None] = fk("contact.id", nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    thread_key: Mapped[str | None] = mapped_column(String(300), index=True)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class Message(Base):
    __tablename__ = "message"
    id: Mapped[uuid.UUID] = pk()
    conversation_id: Mapped[uuid.UUID] = fk("conversation.id")
    draft_id: Mapped[uuid.UUID | None] = fk("outreach_draft.id", nullable=True)
    direction: Mapped[str] = mapped_column(String(10))  # out | in
    channel: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    provider_id: Mapped[str | None] = mapped_column(String(300), index=True)
    sender: Mapped[str | None] = mapped_column(String(320))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class Outcome(Base):
    __tablename__ = "outcome"
    __table_args__ = (CheckConstraint("result in ('won','lost','no_reply','replied','meeting')"),)
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True)
    result: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(60))
    value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class CrmSync(Base):
    __tablename__ = "crm_sync"
    __table_args__ = (UniqueConstraint("business_id", "crm"),)
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    crm: Mapped[str] = mapped_column(String(40))
    crm_id: Mapped[str] = mapped_column(String(100))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- protect


class Connector(Base):
    __tablename__ = "connector"
    __table_args__ = (CheckConstraint("circuit in ('closed','open','half_open')"),)
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(40))  # source | contact | crm | channel | model
    name: Mapped[str] = mapped_column(String(80), unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # secrets by reference
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    monthly_cap: Mapped[int] = mapped_column(Integer, default=0)  # cents; 0 = free only
    circuit: Mapped[str] = mapped_column(String(10), default="closed")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class ConnectorHealth(Base):
    __tablename__ = "connector_health"
    id: Mapped[uuid.UUID] = pk()
    connector_id: Mapped[uuid.UUID] = fk("connector.id")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))  # ok | slow | off
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class CostLedger(Base):
    __tablename__ = "cost_ledger"
    id: Mapped[uuid.UUID] = pk()
    connector: Mapped[str] = mapped_column(String(80), index=True)
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40))
    budget_line: Mapped[str] = mapped_column(String(40), default="pipeline")
    units: Mapped[int] = mapped_column(Integer, default=1)
    cents: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = created()


class CostReservation(Base):
    __tablename__ = "cost_reservation"
    id: Mapped[uuid.UUID] = pk()
    connector: Mapped[str] = mapped_column(String(80), index=True)
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40))
    cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="held")  # held|committed|released
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class Suppression(Base):
    __tablename__ = "suppression"
    __table_args__ = (
        UniqueConstraint("kind", "value_hash"),
        CheckConstraint("kind in ('email','phone','domain','business')"),
    )
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(20))
    value_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(60))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class PolicyDecisionRow(Base):
    __tablename__ = "policy_decision"
    id: Mapped[uuid.UUID] = pk()
    action: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(20))
    reasons: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    inputs: Mapped[dict[str, Any]]
    actor: Mapped[str | None] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class PolicyRule(Base):
    __tablename__ = "policy_rule"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(80), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class GoldenLabel(Base):
    __tablename__ = "golden_label"
    __table_args__ = (UniqueConstraint("business_id", "signal"),)
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    signal: Mapped[str] = mapped_column(String(80))
    truth: Mapped[bool] = mapped_column(Boolean)
    labelled_by: Mapped[str] = mapped_column(String(100))
    labelled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class SignalPrecision(Base):
    """Latest golden-set result per signal and detector version: the release gate."""

    __tablename__ = "signal_precision"
    __table_args__ = (UniqueConstraint("signal", "detector_version"),)
    id: Mapped[uuid.UUID] = pk()
    signal: Mapped[str] = mapped_column(String(80))
    detector_version: Mapped[str] = mapped_column(String(40))
    precision: Mapped[float | None] = mapped_column(Float)
    recall: Mapped[float | None] = mapped_column(Float)
    n_labelled: Mapped[int] = mapped_column(Integer)
    n_positive_calls: Mapped[int] = mapped_column(Integer)
    live: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class EventOutbox(Base):
    __tablename__ = "event_outbox"
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(40), index=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = pk()
    actor: Mapped[str] = mapped_column(String(100))
    table_name: Mapped[str] = mapped_column(String(60))
    row_id: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(10))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (CheckConstraint("role in ('operator','approver','viewer')"),)
    id: Mapped[uuid.UUID] = pk()
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=20)
    created_at: Mapped[datetime] = created()


class SendingDomain(Base):
    __tablename__ = "sending_domain"
    id: Mapped[uuid.UUID] = pk()
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    daily_cap: Mapped[int] = mapped_column(Integer, default=20)
    day_index: Mapped[int] = mapped_column(Integer, default=0)
    spf_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    dkim_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    dmarc_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


# ---------------------------------------------------------------- problems and pressure (s.19)


class Problem(Base):
    __tablename__ = "problem"
    __table_args__ = (CheckConstraint("kind in ('structural','acute')"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    pack_id: Mapped[uuid.UUID | None] = fk("pack.id", nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    areas: Mapped[list[str]] = mapped_column(ARRAY(String))
    kind: Mapped[str] = mapped_column(String(20))
    half_life_days: Mapped[int | None] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = created()


class BusinessProblem(Base):
    __tablename__ = "business_problem"
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business.id"), primary_key=True
    )
    problem_id: Mapped[str] = mapped_column(String(80), ForeignKey("problem.id"), primary_key=True)
    strength: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    confidence: Mapped[str] = mapped_column(String(10))
    evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TriggerEvent(Base):
    __tablename__ = "trigger_event"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    type: Mapped[str] = mapped_column(String(60))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    half_life_days: Mapped[int] = mapped_column(Integer)
    evidence_id: Mapped[uuid.UUID] = fk("evidence_event.id")
    created_at: Mapped[datetime] = created()


class PressureSnapshot(Base):
    __tablename__ = "pressure_snapshot"
    __table_args__ = (CheckConstraint("direction in ('rising','steady','fading')"),)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business.id"), primary_key=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    idx: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    direction: Mapped[str] = mapped_column(String(10))
    contributors: Mapped[dict[str, Any]]


class OfferProblem(Base):
    __tablename__ = "offer_problem"
    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offer.id"), primary_key=True
    )
    problem_id: Mapped[str] = mapped_column(String(80), ForeignKey("problem.id"), primary_key=True)
    fit: Mapped[Decimal] = mapped_column(Numeric(3, 2))


class BusinessOffering(Base):
    __tablename__ = "business_offering"
    id: Mapped[uuid.UUID] = pk()
    business_id: Mapped[uuid.UUID] = fk("business.id")
    category: Mapped[str] = mapped_column(String(100))
    source_event: Mapped[uuid.UUID | None] = fk("evidence_event.id", nullable=True)
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- panic alerts (M24)


class PanicWatch(Base):
    __tablename__ = "panic_watch"
    __table_args__ = (CheckConstraint("style in ('instant','hourly','daily')"),)
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rule: Mapped[dict[str, Any]]
    style: Mapped[str] = mapped_column(String(10), default="instant")
    channel_order: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    tz: Mapped[str] = mapped_column(String(60), default="America/New_York")
    breakthrough_min: Mapped[int | None] = mapped_column(Integer)
    cap_per_hour: Mapped[int] = mapped_column(Integer, default=5)
    cap_per_day: Mapped[int] = mapped_column(Integer, default=20)
    cooldown_days: Mapped[int] = mapped_column(Integer, default=7)
    consecutive_not_a_fit: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class NotificationChannel(Base):
    __tablename__ = "notification_channel"
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(100))
    config: Mapped[dict[str, Any]]
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class PanicAlert(Base):
    __tablename__ = "panic_alert"
    __table_args__ = (
        UniqueConstraint("watch_id", "dedupe_key"),
        CheckConstraint("event in ('new_match','escalation','new_trigger','reopen')"),
        CheckConstraint(
            "status in ('pending','held','sent','digested','failed','read','dismissed')"
        ),
        Index("ix_panic_alert_watch_time", "watch_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    watch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("panic_watch.id"))
    business_id: Mapped[uuid.UUID] = fk("business.id")
    event: Mapped[str] = mapped_column(String(20))
    aspect: Mapped[str] = mapped_column(String(60))
    pressure: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(String(10))
    evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    dedupe_key: Mapped[str] = mapped_column(String(300))
    unverified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = created()


class AlertDelivery(Base):
    __tablename__ = "alert_delivery"
    id: Mapped[uuid.UUID] = pk()
    alert_id: Mapped[uuid.UUID] = fk("panic_alert.id")
    channel_id: Mapped[uuid.UUID] = fk("notification_channel.id")
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text)


class AlertFeedback(Base):
    __tablename__ = "alert_feedback"
    __table_args__ = (CheckConstraint("kind in ('not_a_fit','snooze','mute_watch','acted')"),)
    id: Mapped[uuid.UUID] = pk()
    alert_id: Mapped[uuid.UUID] = fk("panic_alert.id")
    kind: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
