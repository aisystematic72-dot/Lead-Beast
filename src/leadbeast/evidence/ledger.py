"""Evidence ledger I/O. Append-only: there is no update or delete function here, by design."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.ids import uuid7
from leadbeast.db import models as m
from leadbeast.domain.evidence import EvidenceEvent, State
from leadbeast.evidence.facts import Facts
from leadbeast.evidence.fold import fold_all
from leadbeast.evidence.freshness import windows
from leadbeast.packs.schema import PackSpec


def to_domain(row: m.EvidenceEvent) -> EvidenceEvent:
    return EvidenceEvent(
        id=row.id, business_id=row.business_id, signal=row.signal, state=State(row.state),
        value=row.value, source=row.source, source_url=row.source_url, span=row.span,
        observed_at=row.observed_at, detector=row.detector, detector_version=row.detector_version,
        supersedes_id=row.supersedes_id,
    )


async def append(
    s: AsyncSession, events: Iterable[EvidenceEvent], recorded_by: str | None = None
) -> list[UUID]:
    ids: list[UUID] = []
    for e in events:
        s.add(m.EvidenceEvent(
            id=e.id, business_id=e.business_id, signal=e.signal, state=e.state.value,
            value=e.value, source=e.source, source_url=e.source_url, span=e.span,
            observed_at=e.observed_at, detector=e.detector, detector_version=e.detector_version,
            supersedes_id=e.supersedes_id, recorded_by=recorded_by,
        ))
        ids.append(e.id)
    if ids:
        await s.flush()
    return ids


async def events_for(s: AsyncSession, business_id: UUID) -> list[EvidenceEvent]:
    rows = (await s.scalars(
        select(m.EvidenceEvent).where(m.EvidenceEvent.business_id == business_id)
        .order_by(m.EvidenceEvent.observed_at)
    )).all()
    return [to_domain(r) for r in rows]


async def load_facts(
    s: AsyncSession, business_id: UUID, pack: PackSpec, meta: dict[str, Any] | None = None
) -> Facts:
    evs = await events_for(s, business_id)
    sigs = sorted({e.signal for e in evs} | set(pack.all_signals()))
    resolved = fold_all(evs, windows(sigs, pack.freshness_days))
    return Facts(resolved, meta)


def manual_event(
    business_id: UUID, signal: str, value: Any, user: str, note: str | None = None,
    supersedes: UUID | None = None,
) -> EvidenceEvent:
    return EvidenceEvent(
        id=uuid7(), business_id=business_id, signal=signal, state=State.MANUALLY_VERIFIED,
        value=value, source="operator", source_url=None, span=note, observed_at=now(),
        detector="manual", detector_version="1", supersedes_id=supersedes,
    )


async def emit(
    s: AsyncSession, kind: str, business_id: UUID | None, payload: dict[str, Any] | None = None
) -> None:
    """Write a domain event to the outbox in the caller's transaction."""
    s.add(m.EventOutbox(id=uuid7(), kind=kind, business_id=business_id, payload=payload or {}))


async def pending_outbox(s: AsyncSession, limit: int = 100) -> Sequence[m.EventOutbox]:
    return (await s.scalars(
        select(m.EventOutbox).where(m.EventOutbox.processed_at.is_(None))
        .order_by(m.EventOutbox.id).limit(limit).with_for_update(skip_locked=True)
    )).all()
