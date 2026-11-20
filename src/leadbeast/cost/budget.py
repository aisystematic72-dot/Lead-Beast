"""Cost guard (M14). Reserve before spend, commit after. A call that would pass a cap is refused.

Caps exist at three levels: per connector per month, per campaign, and global per month.
At 80% of a cap the operator is notified; at 100% the connector's circuit opens.
Free sources still write ledger rows with cents = 0 so volume is visible.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.core.errors import BudgetExceeded
from leadbeast.core.ids import uuid7
from leadbeast.db import models as m
from leadbeast.evidence.ledger import emit

WARN_AT = 0.8


@dataclass(frozen=True)
class CapCheck:
    level: str
    cap: int
    used: int  # committed + held

    def would_exceed(self, cents: int) -> bool:
        return self.cap > 0 and self.used + cents > self.cap

    def ratio_after(self, cents: int) -> float:
        return (self.used + cents) / self.cap if self.cap > 0 else 0.0


def evaluate(checks: list[CapCheck], cents: int) -> tuple[list[str], list[str]]:
    """Pure: which caps would be exceeded, and which cross the warning line."""
    exceeded = [c.level for c in checks if c.would_exceed(cents)]
    warn = [c.level for c in checks if c.cap > 0 and c.ratio_after(cents) >= WARN_AT]
    return exceeded, warn


@dataclass(frozen=True)
class Reservation:
    id: UUID
    connector: str
    cents: int
    run_id: str | None
    campaign_id: UUID | None


def _month_start(t: datetime) -> datetime:
    return t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class Budget:
    def __init__(self, s: AsyncSession):
        self.s = s

    async def _used(self, *, connector: str | None = None, campaign_id: UUID | None = None,
                    since: datetime | None = None) -> int:
        led = select(func.coalesce(func.sum(m.CostLedger.cents), 0))
        res = select(func.coalesce(func.sum(m.CostReservation.cents), 0)).where(
            m.CostReservation.status == "held")
        if since is not None:
            led = led.where(m.CostLedger.at >= since)
            res = res.where(m.CostReservation.at >= since)
        if connector is not None:
            led = led.where(m.CostLedger.connector == connector)
            res = res.where(m.CostReservation.connector == connector)
        if campaign_id is not None:
            led = led.where(m.CostLedger.campaign_id == campaign_id)
            res = res.where(m.CostReservation.campaign_id == campaign_id)
        return int(await self.s.scalar(led) or 0) + int(await self.s.scalar(res) or 0)

    async def checks(self, connector: str, campaign_id: UUID | None) -> list[CapCheck]:
        t0 = _month_start(now())
        conn = await self.s.scalar(select(m.Connector).where(m.Connector.name == connector))
        out = [CapCheck("global", get_settings().monthly_cap_cents, await self._used(since=t0))]
        if conn is not None:
            out.append(CapCheck(f"connector:{connector}", conn.monthly_cap,
                                await self._used(connector=connector, since=t0)))
        if campaign_id is not None:
            camp = await self.s.get(m.Campaign, campaign_id)
            if camp is not None:
                out.append(CapCheck("campaign", camp.budget_cap,
                                    await self._used(campaign_id=campaign_id)))
        return out

    async def reserve(self, connector: str, cents: int, run_id: str | None = None,
                      campaign_id: UUID | None = None) -> Reservation:
        # serialise reservations so two workers cannot both squeeze under a cap
        await self.s.execute(text("select pg_advisory_xact_lock(:k)"),
                             {"k": zlib.crc32(b"leadbeast-budget")})
        if cents > 0:
            exceeded, warn = evaluate(await self.checks(connector, campaign_id), cents)
            if exceeded:
                await self._open_circuit(connector)
                await emit(self.s, "BudgetExceeded", None,
                           {"connector": connector, "levels": exceeded, "cents": cents})
                raise BudgetExceeded(f"{connector}: {', '.join(exceeded)}")
            if warn:
                await emit(self.s, "BudgetWarning", None, {"connector": connector, "levels": warn})
        r = m.CostReservation(id=uuid7(), connector=connector, campaign_id=campaign_id,
                              run_id=run_id, cents=cents, status="held", at=now())
        self.s.add(r)
        await self.s.flush()
        return Reservation(r.id, connector, cents, run_id, campaign_id)

    async def commit(self, r: Reservation, actual_cents: int, units: int = 1,
                     budget_line: str = "pipeline") -> None:
        await self.s.execute(update(m.CostReservation).where(m.CostReservation.id == r.id)
                             .values(status="committed"))
        self.s.add(m.CostLedger(connector=r.connector, campaign_id=r.campaign_id, run_id=r.run_id,
                                units=units, cents=actual_cents, budget_line=budget_line, at=now()))

    async def release(self, r: Reservation) -> None:
        await self.s.execute(update(m.CostReservation).where(m.CostReservation.id == r.id)
                             .values(status="released"))

    async def record_free(self, connector: str, units: int = 1, run_id: str | None = None,
                          campaign_id: UUID | None = None, budget_line: str = "pipeline") -> None:
        self.s.add(m.CostLedger(connector=connector, campaign_id=campaign_id, run_id=run_id,
                                units=units, cents=0, budget_line=budget_line, at=now()))

    async def _open_circuit(self, connector: str) -> None:
        await self.s.execute(update(m.Connector).where(m.Connector.name == connector)
                             .values(circuit="open"))

    async def month_summary(self, month: datetime | None = None) -> list[dict[str, object]]:
        t0 = _month_start(month or now())
        rows = (await self.s.execute(
            select(m.CostLedger.connector, func.sum(m.CostLedger.cents), func.sum(m.CostLedger.units))
            .where(m.CostLedger.at >= t0).group_by(m.CostLedger.connector)
        )).all()
        caps = {c.name: c.monthly_cap for c in (await self.s.scalars(select(m.Connector))).all()}
        return [{"connector": c, "cents": int(cents or 0), "units": int(units or 0),
                 "cap": caps.get(c, 0)} for c, cents, units in rows]
