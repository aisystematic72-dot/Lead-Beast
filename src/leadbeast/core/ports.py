"""Every edge of the system is a Protocol. The core imports these, never a vendor library."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from leadbeast.domain.business import Business, Contact, SourceRecord


@dataclass(frozen=True)
class Status:
    status: str  # ok | slow | off
    latency_ms: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class SourceQuery:
    area: str
    trades: list[str] = field(default_factory=list)
    limit: int = 500
    bbox: tuple[float, float, float, float] | None = None  # south, west, north, east
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContactCheckResult:
    kind: str
    result: str
    checked_at: datetime


@dataclass(frozen=True)
class ApprovedDraft:
    draft_id: str
    to: str
    subject: str
    body: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderReceipt:
    provider_id: str
    accepted: bool
    detail: str = ""


@dataclass(frozen=True)
class InboundMessage:
    provider_id: str
    in_reply_to: str | None
    sender: str
    body: str
    at: datetime
    kind: str = "reply"  # reply | bounce_hard | bounce_soft | complaint | unsubscribe


@runtime_checkable
class HealthCheck(Protocol):
    async def probe(self) -> Status: ...


class SourceAdapter(HealthCheck, Protocol):
    name: str
    cost_per_unit_cents: float

    def fetch(self, query: SourceQuery) -> AsyncIterator[SourceRecord]: ...


class ContactProvider(HealthCheck, Protocol):
    name: str

    async def find(self, biz: Business) -> list[Contact]: ...
    async def verify(self, contact: Contact) -> list[ContactCheckResult]: ...


class CrmAdapter(HealthCheck, Protocol):
    name: str

    async def upsert_business(self, biz: Business, snap: dict[str, Any],
                              crm_id: str | None = None) -> str: ...
    async def upsert_contact(self, c: Contact, crm_biz_id: str) -> str: ...
    async def log_activity(self, crm_id: str, msg: dict[str, Any]) -> None: ...
    async def set_stage(self, crm_id: str, stage: str) -> None: ...


class ChannelAdapter(HealthCheck, Protocol):
    channel: str

    async def send(self, draft: ApprovedDraft) -> ProviderReceipt: ...
    def inbound(self) -> AsyncIterator[InboundMessage]: ...


class ModelClient(HealthCheck, Protocol):
    name: str
    version: str

    async def json(self, prompt: str, schema: type) -> object: ...
    async def text(self, prompt: str, max_tokens: int) -> str: ...


class ReportRenderer(Protocol):
    def html(self, audit: dict[str, Any]) -> str: ...
    def pdf(self, audit: dict[str, Any]) -> bytes: ...
