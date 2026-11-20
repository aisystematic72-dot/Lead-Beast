"""Score stage: load evidence, run the pure engine, save an immutable snapshot, emit events."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.contacts.service import contact_summary
from leadbeast.db import models as m
from leadbeast.domain.scoring import ScoreResult
from leadbeast.evidence.ledger import emit, load_facts
from leadbeast.packs.service import offer_for_campaign, pack_for_campaign


async def latest_snapshot(s: AsyncSession, business_id: UUID, campaign_id: UUID | None = None
                          ) -> m.ScoreSnapshot | None:
    q = select(m.ScoreSnapshot).where(m.ScoreSnapshot.business_id == business_id)
    if campaign_id is not None:
        q = q.where(m.ScoreSnapshot.campaign_id == campaign_id)
    return await s.scalar(q.order_by(m.ScoreSnapshot.created_at.desc(),
                                     m.ScoreSnapshot.id.desc()).limit(1))


def _reasons_json(res: ScoreResult) -> dict[str, object]:
    return {
        "codes": res.reason_codes,
        "items": [{**asdict(r), "evidence_id": str(r.evidence_id) if r.evidence_id else None}
                  for r in res.reasons],
    }


async def identity_conflict(s: AsyncSession, business_id: UUID) -> bool:
    row = await s.scalar(select(m.ReviewItem.id).where(
        m.ReviewItem.business_id == business_id, m.ReviewItem.kind == "identity_match",
        m.ReviewItem.resolved_at.is_(None)).limit(1))
    return row is not None


async def score_business(s: AsyncSession, business_id: UUID, campaign_id: UUID
                         ) -> tuple[m.ScoreSnapshot, bool]:
    from leadbeast.scoring.engine import score

    pack = await pack_for_campaign(s, campaign_id)
    offer = await offer_for_campaign(s, campaign_id)
    facts = await load_facts(s, business_id, pack,
                             meta={"identity_conflict": await identity_conflict(s, business_id)})
    contact = await contact_summary(s, business_id)
    res = score(facts, pack, offer.service_fit_profile or {}, contact)

    prev = await latest_snapshot(s, business_id, campaign_id)
    snap = m.ScoreSnapshot(
        business_id=business_id, campaign_id=campaign_id, pack_version=res.pack_version,
        need=Decimal(str(res.need)), ability=Decimal(str(res.ability)),
        timing=Decimal(str(res.timing)), reach=Decimal(str(res.reach)), coverage=res.coverage,
        confidence=res.confidence, total=Decimal(str(res.total)), cls=res.cls,
        gap_types=res.gap_types, reasons=_reasons_json(res),
    )
    s.add(snap)
    await s.flush()
    for g in res.gates:
        s.add(m.GateResultRow(snapshot_id=snap.id, gate=g.gate, passed=g.passed, value=g.value,
                              threshold=g.threshold, reason=g.reason or None))
    changed = prev is None or prev.cls != snap.cls
    if prev is None or prev.total != snap.total:
        await emit(s, "ScoreChanged", business_id, {"snapshot_id": str(snap.id)})
    if changed:
        await emit(s, "ClassChanged", business_id, {
            "snapshot_id": str(snap.id), "from": prev.cls if prev else None, "to": snap.cls,
            "campaign_id": str(campaign_id)})
        if snap.cls == "review":
            s.add(m.ReviewItem(kind="class_review", business_id=business_id,
                               detail={"snapshot_id": str(snap.id), "codes": res.reason_codes}))
        if facts.conflicts():
            await emit(s, "ConflictRaised", business_id, {"signals": facts.conflicts()})
    return snap, changed
