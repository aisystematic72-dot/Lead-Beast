"""JSON API v1. Thin handlers: all logic lives in service functions."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.api.auth import User, require
from leadbeast.api.schemas import (
    CampaignIn,
    CampaignOut,
    ChannelIn,
    DecisionIn,
    EvidenceIn,
    InboundIn,
    LaunchIn,
    MysteryIn,
    OutcomeIn,
    Page,
    ReviewResolveIn,
    SuppressionIn,
    WatchIn,
)
from leadbeast.core.clock import now
from leadbeast.core.db import get_session
from leadbeast.core.errors import NotFound, ValidationFailed
from leadbeast.core.ids import new_run_id
from leadbeast.core.ports import InboundMessage
from leadbeast.cost.budget import Budget
from leadbeast.db import models as m
from leadbeast.evidence.ledger import append, emit, events_for, manual_event
from leadbeast.market.queries import OpportunityFilter, business_view, market_summary, opportunities
from leadbeast.outreach import service as outreach
from leadbeast.packs import service as packs
from leadbeast.policy import suppression

router = APIRouter(prefix="/api")
VIEW, APPROVE, OPERATE = require("viewer"), require("approver"), require("operator")


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_jsonable(v) for v in o]
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "__table__"):
        return {c.key: _jsonable(getattr(o, c.key)) for c in o.__mapper__.column_attrs}
    if hasattr(o, "is_finite"):  # Decimal
        return float(o)
    return o


# ------------------------------------------------------------------ campaigns
@router.post("/campaigns", response_model=CampaignOut)
async def create_campaign(body: CampaignIn, s: AsyncSession = Depends(get_session),
                          _: User = Depends(OPERATE)) -> Any:
    offer_id = body.offer_id
    if offer_id is None:
        if body.offer is None:
            raise ValidationFailed("offer_id or offer is required (offer first)")
        offer_id = (await packs.create_offer(s, body.offer)).id
    c = await packs.create_campaign(
        s, name=body.name, offer_id=offer_id, pack_name=body.pack, area=body.area,
        size_min=body.size_min, size_max=body.size_max, budget_cap=body.budget_cap_cents,
        target_by=body.target_by, partner=body.partner)
    return CampaignOut(id=c.id, name=c.name, offer_id=c.offer_id, pack_id=c.pack_id, area=c.area,
                       status=c.status)


@router.post("/campaigns/{cid}/launch")
async def launch(cid: UUID, body: LaunchIn, dry_run: bool = False,
                 s: AsyncSession = Depends(get_session), _: User = Depends(OPERATE)) -> Any:
    if await s.get(m.Campaign, cid) is None:
        raise NotFound(f"campaign {cid}")
    if dry_run:
        from leadbeast.pipeline import dry_run as do_dry

        await s.commit()
        return await do_dry(cid, body.source, body.sample, body.path)
    from leadbeast.jobs.tasks import collect_source

    job = await collect_source.defer_async(campaign_id=str(cid), source=body.source,
                                           run_id=new_run_id(), path=body.path, limit=body.limit)
    return {"queued": True, "job_id": job}


# ------------------------------------------------------------------ businesses
@router.get("/businesses", response_model=Page)
async def list_businesses(campaign: UUID | None = None, cls: str | None = Query(None, alias="class"),
                          gap: str | None = None, status: str | None = None, q: str | None = None,
                          limit: int = Query(50, le=500), cursor: str | None = None,
                          s: AsyncSession = Depends(get_session), _: User = Depends(VIEW)) -> Any:
    rows = await opportunities(s, OpportunityFilter(campaign, cls, gap, status, q, limit, cursor))
    nxt = f"{rows[-1]['total']}|{rows[-1]['id']}" if len(rows) == limit else None
    return Page(items=_jsonable(rows), next_cursor=nxt)


@router.get("/businesses/{bid}")
async def get_business(bid: UUID, s: AsyncSession = Depends(get_session),
                       _: User = Depends(VIEW)) -> Any:
    v = await business_view(s, bid)
    if v is None:
        raise NotFound(f"business {bid}")
    return _jsonable(v)


@router.get("/businesses/{bid}/evidence")
async def evidence_history(bid: UUID, s: AsyncSession = Depends(get_session),
                           _: User = Depends(VIEW)) -> Any:
    return [e.model_dump(mode="json") for e in await events_for(s, bid)]


@router.post("/businesses/{bid}/evidence")
async def add_manual_evidence(bid: UUID, body: EvidenceIn, s: AsyncSession = Depends(get_session),
                              user: User = Depends(APPROVE)) -> Any:
    if await s.get(m.Business, bid) is None:
        raise NotFound(f"business {bid}")
    ev = manual_event(bid, body.signal, body.value, user.username, body.note, body.supersedes_id)
    await append(s, [ev], recorded_by=user.username)
    await emit(s, "EvidenceAdded", bid, {"manual": True})
    return {"id": str(ev.id)}


@router.post("/businesses/{bid}/rescore")
async def rescore(bid: UUID, s: AsyncSession = Depends(get_session),
                  _: User = Depends(OPERATE)) -> Any:
    from leadbeast.scoring.service import score_business

    out = []
    for cid in (await s.scalars(select(m.CampaignBusiness.campaign_id).where(
            m.CampaignBusiness.business_id == bid))).all():
        snap, changed = await score_business(s, bid, cid)
        out.append({"campaign_id": str(cid), "class": snap.cls, "total": float(snap.total),
                    "class_changed": changed})
    return out


@router.post("/businesses/{bid}/mystery-check")
async def mystery(bid: UUID, body: MysteryIn, s: AsyncSession = Depends(get_session),
                  user: User = Depends(APPROVE)) -> Any:
    row = await outreach.record_mystery_check(s, bid, user.username, **body.model_dump())
    return {"id": str(row.id), "passed": row.passed}


@router.post("/identity/{link_id}/unmerge")
async def unmerge(link_id: UUID, s: AsyncSession = Depends(get_session),
                  user: User = Depends(OPERATE)) -> Any:
    from leadbeast.identity.service import unmerge as do

    return {"new_business_id": str(await do(s, link_id, user.username))}


@router.post("/reviews/{rid}/resolve")
async def resolve_review(rid: UUID, body: ReviewResolveIn, s: AsyncSession = Depends(get_session),
                         user: User = Depends(APPROVE)) -> Any:
    r = await s.get(m.ReviewItem, rid)
    if r is None:
        raise NotFound(f"review {rid}")
    r.resolution, r.resolved_by, r.resolved_at = body.resolution, user.username, now()
    if body.resolution == "suppress" and r.business_id:
        await suppression.add(s, "business", str(r.business_id), "review_suppress")
    if r.business_id:
        await emit(s, "EvidenceAdded", r.business_id, {"review_resolved": body.resolution})
    return {"ok": True}


# ------------------------------------------------------------------ drafts
@router.get("/drafts")
async def list_drafts(status: str = "pending", s: AsyncSession = Depends(get_session),
                      _: User = Depends(VIEW)) -> Any:
    sts = ("draft", "blocked") if status == "pending" else (status,)
    rows = (await s.scalars(select(m.OutreachDraft).where(m.OutreachDraft.status.in_(sts))
                            .order_by(m.OutreachDraft.created_at))).all()
    return _jsonable(list(rows))


@router.post("/businesses/{bid}/draft")
async def make_draft(bid: UUID, campaign_id: UUID, s: AsyncSession = Depends(get_session),
                     user: User = Depends(OPERATE)) -> Any:
    d = await outreach.draft_outreach(s, bid, campaign_id, user.username)
    return _jsonable(d)


@router.post("/drafts/{did}/decision")
async def decide(did: UUID, body: DecisionIn, s: AsyncSession = Depends(get_session),
                 user: User = Depends(APPROVE)) -> Any:
    d = await outreach.decide(s, did, user.username, body.decision, body.edited_body)
    return {"id": str(d.id), "status": d.status}


# ------------------------------------------------------------------ outcomes, inbound, suppression
@router.post("/outcomes")
async def log_outcome(body: OutcomeIn, s: AsyncSession = Depends(get_session),
                      _: User = Depends(APPROVE)) -> Any:
    o = await outreach.log_outcome(s, body.business_id, body.campaign_id, body.result,
                                   body.reason_code, body.value)
    return {"id": str(o.id)}


@router.post("/inbound")
async def inbound(body: InboundIn, s: AsyncSession = Depends(get_session),
                  _: User = Depends(OPERATE)) -> Any:
    res = await outreach.handle_inbound(s, InboundMessage(
        provider_id=body.provider_id, in_reply_to=body.in_reply_to, sender=body.sender,
        body=body.body, at=body.at or now(), kind=body.kind))
    return {"result": res}


@router.post("/suppression")
async def add_suppression(body: SuppressionIn, s: AsyncSession = Depends(get_session),
                          _: User = Depends(APPROVE)) -> Any:
    await suppression.add(s, body.kind, body.value, body.reason)
    return {"ok": True}


# ------------------------------------------------------------------ connectors, cost, market
@router.get("/connectors")
async def connectors(s: AsyncSession = Depends(get_session), _: User = Depends(VIEW)) -> Any:
    return _jsonable(list((await s.scalars(select(m.Connector))).all()))


@router.post("/connectors/{cid}/probe")
async def probe(cid: UUID, s: AsyncSession = Depends(get_session),
                _: User = Depends(OPERATE)) -> Any:
    from leadbeast.health.service import probe_connector

    c = await s.get(m.Connector, cid)
    if c is None:
        raise NotFound(f"connector {cid}")
    st = await probe_connector(s, c)
    return {"status": st.status, "latency_ms": st.latency_ms, "detail": st.detail,
            "circuit": c.circuit}


@router.get("/cost")
async def cost(month: str | None = None, s: AsyncSession = Depends(get_session),
               _: User = Depends(VIEW)) -> Any:
    when = datetime.fromisoformat(month + "-01T00:00:00+00:00") if month else None
    return await Budget(s).month_summary(when)


@router.get("/market/summary")
async def market(campaign: UUID | None = None, s: AsyncSession = Depends(get_session),
                 _: User = Depends(VIEW)) -> Any:
    return _jsonable(await market_summary(s, campaign))


@router.get("/problems")
async def problems(s: AsyncSession = Depends(get_session), _: User = Depends(VIEW)) -> Any:
    return _jsonable(list((await s.scalars(select(m.Problem))).all()))


@router.get("/businesses/{bid}/problems")
async def business_problems(bid: UUID, s: AsyncSession = Depends(get_session),
                            _: User = Depends(VIEW)) -> Any:
    return _jsonable(list((await s.scalars(select(m.BusinessProblem).where(
        m.BusinessProblem.business_id == bid))).all()))


# ------------------------------------------------------------------ alerts (M24)
@router.get("/watches")
async def list_watches(s: AsyncSession = Depends(get_session), _: User = Depends(VIEW)) -> Any:
    return _jsonable(list((await s.scalars(select(m.PanicWatch))).all()))


def _t(v: str | None) -> time | None:
    return time.fromisoformat(v) if v else None


@router.post("/watches")
async def create_watch(body: WatchIn, s: AsyncSession = Depends(get_session),
                       _: User = Depends(OPERATE)) -> Any:
    from leadbeast.alerts.models import WatchRule

    WatchRule.model_validate(body.rule)
    w = m.PanicWatch(name=body.name, rule=body.rule, style=body.style,
                     channel_order=body.channel_order, quiet_start=_t(body.quiet_start),
                     quiet_end=_t(body.quiet_end), tz=body.tz,
                     breakthrough_min=body.breakthrough_min, cap_per_hour=body.cap_per_hour,
                     cap_per_day=body.cap_per_day, cooldown_days=body.cooldown_days)
    s.add(w)
    await s.flush()
    return {"id": str(w.id)}


@router.delete("/watches/{wid}")
async def delete_watch(wid: UUID, s: AsyncSession = Depends(get_session),
                       _: User = Depends(OPERATE)) -> Any:
    w = await s.get(m.PanicWatch, wid)
    if w is None:
        raise NotFound(f"watch {wid}")
    w.enabled = False  # history (alerts) stays attributable
    return {"ok": True}


@router.get("/alerts")
async def list_alerts(status: str | None = None, watch_id: UUID | None = None,
                      s: AsyncSession = Depends(get_session), _: User = Depends(VIEW)) -> Any:
    q = select(m.PanicAlert).order_by(m.PanicAlert.created_at.desc()).limit(200)
    if status:
        q = q.where(m.PanicAlert.status == status)
    if watch_id:
        q = q.where(m.PanicAlert.watch_id == watch_id)
    return _jsonable(list((await s.scalars(q)).all()))


@router.post("/alerts/{aid}/feedback")
async def alert_feedback(aid: UUID, kind: str, s: AsyncSession = Depends(get_session),
                         _: User = Depends(VIEW)) -> Any:
    from leadbeast.alerts.service import feedback

    if kind not in ("not_a_fit", "snooze", "mute_watch", "acted"):
        raise ValidationFailed("bad feedback kind")
    await feedback(s, aid, kind)
    return {"ok": True}


@router.post("/channels")
async def create_channel(body: ChannelIn, s: AsyncSession = Depends(get_session),
                         _: User = Depends(OPERATE)) -> Any:
    bad = [k for k in body.config if any(x in k.lower() for x in ("token", "secret", "password"))
           and not k.endswith("_ref")]
    if bad:
        raise ValidationFailed(f"secrets must be references (*_ref), not inline: {bad}")
    ch = m.NotificationChannel(kind=body.kind, label=body.label, config=body.config)
    s.add(ch)
    await s.flush()
    return {"id": str(ch.id)}
