"""Resolve source records into businesses; reversible merges (M3)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.core.errors import NotFound
from leadbeast.core.ids import uuid7
from leadbeast.db import models as m
from leadbeast.domain.evidence import EvidenceEvent, State
from leadbeast.evidence.ledger import append, emit, events_for
from leadbeast.identity import normalise as N
from leadbeast.identity.match import Rec, Verdict, compare, decide


def norm_from_payload(p: dict[str, Any]) -> dict[str, Any]:
    addr = N.address(p.get("address"))
    if p.get("postcode") and not addr.postcode:
        addr = addr.model_copy(update={"postcode": str(p["postcode"])[:5]})
    if p.get("city") and not addr.city:
        addr = addr.model_copy(update={"city": p["city"]})
    if p.get("state") and not addr.state:
        addr = addr.model_copy(update={"state": p["state"]})
    site = N.website_url(p.get("website"))
    return {
        "name": (p.get("name") or "").strip(),
        "norm_name": N.name(p.get("name")),
        "phone_e164": N.phone(p.get("phone")),
        "domain": N.domain(site) if site else None,
        "website_url": site,
        "address": addr.model_dump(),
        "postcode": addr.postcode,
        "lat": _f(p.get("lat")),
        "lon": _f(p.get("lon")),
        "trade": (p.get("trade") or None),
    }


def _f(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _rec(key: str, n: dict[str, Any]) -> Rec:
    return Rec(key=key, norm_name=n["norm_name"], phone_e164=n["phone_e164"], domain=n["domain"],
               postcode=n["postcode"], lat=n["lat"], lon=n["lon"], trade=n["trade"],
               address_key=N.address_key(N.address(n["address"])))


def _biz_rec(b: m.Business) -> Rec:
    return Rec(key=str(b.id), norm_name=b.norm_name, phone_e164=b.phone_e164, domain=b.domain,
               postcode=b.postcode, lat=b.lat, lon=b.lon, trade=b.trade,
               address_key=N.address_key(N.address(b.address or {})))


async def _candidates(s: AsyncSession, n: dict[str, Any]) -> list[m.Business]:
    conds = []
    if n["phone_e164"]:
        conds.append(m.Business.phone_e164 == n["phone_e164"])
    if n["domain"] and not N.is_webmail("x@" + n["domain"]):
        conds.append(m.Business.domain == n["domain"])
    if n["postcode"] and n["norm_name"]:
        conds.append((m.Business.postcode == n["postcode"]) &
                     m.Business.norm_name.like(N.first_token(n["norm_name"]) + "%"))
    if not conds:
        return []
    return list((await s.scalars(select(m.Business).where(or_(*conds)).limit(50))).all())


async def resolve_record(s: AsyncSession, record_id: UUID, campaign_id: UUID | None = None
                         ) -> tuple[UUID | None, str]:
    """Returns (business_id, verdict). Idempotent: an already-linked record returns its business."""
    rec = await s.get(m.SourceRecord, record_id)
    if rec is None:
        raise NotFound(f"source_record {record_id}")
    if rec.business_id is not None:
        return rec.business_id, "already_linked"
    n = norm_from_payload(rec.payload)
    if not n["norm_name"]:
        return None, "no_name"
    st = get_settings()
    me = _rec(str(rec.id), n)
    best: tuple[float, m.Business, Any] | None = None
    review: list[tuple[m.Business, Any]] = []
    for b in await _candidates(s, n):
        ms = compare(me, _biz_rec(b))
        v = decide(ms, st.identity_merge_threshold, st.identity_review_threshold)
        if v is Verdict.MERGE and (best is None or ms.score > best[0]):
            best = (ms.score, b, ms)
        elif v is Verdict.REVIEW:
            review.append((b, ms))

    if best is not None:
        _, biz, ms = best
        _fill_missing(biz, n)
        verdict = "merged"
    else:
        biz = m.Business(
            id=uuid7(), canonical_name=n["name"], norm_name=n["norm_name"],
            phone_e164=n["phone_e164"], domain=n["domain"], website_url=n["website_url"],
            address=n["address"], postcode=n["postcode"], lat=n["lat"], lon=n["lon"],
            trade=n["trade"], status="active",
        )
        s.add(biz)
        await s.flush()
        ms = None
        verdict = "new"
        for other, rms in review:  # never auto-merge an ambiguous match: ask a person
            s.add(m.ReviewItem(kind="identity_match", business_id=biz.id,
                               source_record_id=rec.id,
                               detail={"candidate": str(other.id), "score": rms.score,
                                       "rule": rms.rule, "trade_conflict": rms.conflict}))
            if rms.conflict:
                await emit(s, "ConflictRaised", biz.id, {"kind": "identity", "with": str(other.id)})
        if review:
            verdict = "new_with_review"

    rec.business_id = biz.id
    s.add(m.IdentityLink(business_id=biz.id, source_record_id=rec.id,
                         match_score=ms.score if ms else 1.0,
                         rule=ms.rule if ms else "new_business"))
    if campaign_id is not None:
        exists = await s.get(m.CampaignBusiness, (campaign_id, biz.id))
        if exists is None:
            s.add(m.CampaignBusiness(campaign_id=campaign_id, business_id=biz.id))
    await emit(s, "BusinessResolved", biz.id, {"source_record_id": str(rec.id), "verdict": verdict})
    await s.flush()
    return biz.id, verdict


def _fill_missing(b: m.Business, n: dict[str, Any]) -> None:
    for attr in ("phone_e164", "domain", "website_url", "postcode", "lat", "lon", "trade"):
        if getattr(b, attr) in (None, "") and n.get(attr) not in (None, ""):
            setattr(b, attr, n[attr])
    if not b.address and n["address"]:
        b.address = n["address"]


async def unmerge(s: AsyncSession, link_id: UUID, user: str) -> UUID:
    """Split a source record off its business and replay its evidence onto a new owner."""
    link = await s.get(m.IdentityLink, link_id)
    if link is None or link.undone_at is not None:
        raise NotFound(f"identity_link {link_id}")
    rec = await s.get(m.SourceRecord, link.source_record_id)
    assert rec is not None
    old_biz = link.business_id
    link.undone_at = now()
    rec.business_id = None
    await s.flush()
    new_id, _ = await resolve_record_forced_new(s, rec)
    # replay evidence derived from this record: copy to new owner, retract on old (append-only)
    tag = f"record:{rec.id}"
    moved: list[EvidenceEvent] = []
    for e in await events_for(s, old_biz):
        if e.source_url == tag:
            moved.append(e.model_copy(update={"id": uuid7(), "business_id": new_id}))
            moved.append(EvidenceEvent(
                id=uuid7(), business_id=old_biz, signal=e.signal, state=State.UNKNOWN,
                source=e.source, source_url=tag, observed_at=now(), detector="unmerge",
                detector_version="1", supersedes_id=e.id,
            ))
    await append(s, moved, recorded_by=user)
    s.add(m.AuditLog(actor=user, table_name="identity_link", row_id=str(link_id),
                     action="unmerge", at=now()))
    await emit(s, "EvidenceAdded", old_biz, {"reason": "unmerge"})
    await emit(s, "EvidenceAdded", new_id, {"reason": "unmerge"})
    return new_id


async def resolve_record_forced_new(s: AsyncSession, rec: m.SourceRecord) -> tuple[UUID, str]:
    n = norm_from_payload(rec.payload)
    biz = m.Business(
        id=uuid7(), canonical_name=n["name"] or "Unknown", norm_name=n["norm_name"],
        phone_e164=n["phone_e164"], domain=n["domain"], website_url=n["website_url"],
        address=n["address"], postcode=n["postcode"], lat=n["lat"], lon=n["lon"], trade=n["trade"],
    )
    s.add(biz)
    await s.flush()
    rec.business_id = biz.id
    s.add(m.IdentityLink(business_id=biz.id, source_record_id=rec.id, match_score=1.0,
                         rule="unmerge_split", merged_by="operator"))
    return biz.id, "new"
