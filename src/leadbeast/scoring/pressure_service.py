"""Derived pressure snapshots and problem strengths (section 19). Rebuildable from the ledger."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.db import models as m
from leadbeast.evidence.ledger import emit, load_facts
from leadbeast.packs.schema import PackSpec
from leadbeast.scoring.gates import vitality
from leadbeast.scoring.problems import Trigger, direction, pressure_index, problem_strength


async def refresh_problems(s: AsyncSession, business_id: UUID, pack: PackSpec) -> int:
    facts = await load_facts(s, business_id, pack)
    await s.execute(delete(m.BusinessProblem).where(m.BusinessProblem.business_id == business_id))
    n = 0
    t = now()
    for p in pack.problems:
        strength, conf = problem_strength(facts, p)
        if strength <= 0:
            continue
        ids = [eid for sym in p.symptoms if (eid := facts.evidence_id(sym.signal))]
        s.add(m.BusinessProblem(business_id=business_id, problem_id=p.id,
                                strength=Decimal(str(round(strength, 3))), confidence=conf.value,
                                evidence_ids=ids, first_seen=t, last_seen=t))
        n += 1
    return n


async def refresh_pressure(s: AsyncSession, business_id: UUID, pack: PackSpec) -> float | None:
    facts = await load_facts(s, business_id, pack)
    if not vitality(facts, pack).passed:
        return None  # pressure is ignored when the vitality gate fails
    weights = {t.id: t.weight for t in pack.pressure.triggers}
    rows = (await s.scalars(select(m.TriggerEvent).where(
        m.TriggerEvent.business_id == business_id))).all()
    trig = [Trigger(r.type, weights.get(r.type, 0.0), r.half_life_days, r.observed_at)
            for r in rows if r.type in weights]
    t = now()
    idx = pressure_index(trig, t)
    prev = await s.scalar(select(m.PressureSnapshot).where(
        m.PressureSnapshot.business_id == business_id,
        m.PressureSnapshot.at <= t - timedelta(days=14)).order_by(m.PressureSnapshot.at.desc()))
    d = direction(idx, float(prev.idx) if prev else None)
    aspects: dict[str, float] = {}
    for tr in trig:
        aspects[tr.id] = aspects.get(tr.id, 0.0) + tr.weight
    s.add(m.PressureSnapshot(business_id=business_id, at=t, idx=Decimal(str(idx)), direction=d,
                             contributors={"aspects": aspects,
                                           "trigger_ids": sorted({x.id for x in trig}),
                                           "evidence_ids": [str(r.evidence_id) for r in rows]}))
    await emit(s, "PressureChanged", business_id, {"idx": idx, "direction": d})
    return idx
