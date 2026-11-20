"""Collect stage: run a source adapter for a campaign and store records verbatim."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.errors import CircuitOpen
from leadbeast.core.ports import SourceAdapter, SourceQuery
from leadbeast.cost.budget import Budget
from leadbeast.db import models as m


async def collect(
    s: AsyncSession, adapter: SourceAdapter, query: SourceQuery, campaign_id: UUID | None,
    run_id: str,
) -> list[UUID]:
    conn = await s.scalar(select(m.Connector).where(m.Connector.name == adapter.name))
    if conn is not None and (conn.circuit == "open" or not conn.enabled):
        raise CircuitOpen(adapter.name)
    budget = Budget(s)
    ids: list[UUID] = []
    n = 0
    async for rec in adapter.fetch(query):
        stmt = insert(m.SourceRecord).values(
            source=rec.source, external_id=rec.external_id, fetched_at=rec.fetched_at,
            payload=rec.payload, campaign_id=campaign_id,
        ).on_conflict_do_update(
            index_elements=["source", "external_id"],
            set_={"payload": rec.payload, "fetched_at": rec.fetched_at},
        ).returning(m.SourceRecord.id)
        ids.append((await s.execute(stmt)).scalar_one())
        n += 1
    if adapter.cost_per_unit_cents == 0:
        await budget.record_free(adapter.name, units=n, run_id=run_id, campaign_id=campaign_id)
    return ids
