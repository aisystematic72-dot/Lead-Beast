"""Evaluate watches for a business and deliver alerts to the operator's own channels."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.alerts import matcher, quiet, render
from leadbeast.alerts.models import BusinessState, PressureState, WatchRule
from leadbeast.alerts.notify import notifier_for
from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.db import models as m
from leadbeast.domain.evidence import State
from leadbeast.golden.service import precision_map


async def business_state(
    s: AsyncSession, business_id: UUID
) -> tuple[BusinessState, PressureState | None] | None:
    b = await s.get(m.Business, business_id)
    snaps = (await s.scalars(select(m.PressureSnapshot).where(
        m.PressureSnapshot.business_id == business_id)
        .order_by(m.PressureSnapshot.at.desc()).limit(2))).all()
    score = await s.scalar(select(m.ScoreSnapshot).where(m.ScoreSnapshot.business_id == business_id)
                           .order_by(m.ScoreSnapshot.created_at.desc()).limit(1))
    if b is None or not snaps or score is None:
        return None
    gates = {g.gate: g for g in (await s.scalars(select(m.GateResultRow).where(
        m.GateResultRow.snapshot_id == score.id))).all()}

    def ps(x: m.PressureSnapshot) -> PressureState:
        c = x.contributors or {}
        return PressureState(index=float(x.idx), direction=x.direction,  # type: ignore[arg-type]
                             aspects=c.get("aspects", {}), trigger_ids=c.get("trigger_ids", []))

    problems = (await s.scalars(select(m.BusinessProblem.problem_id).where(
        m.BusinessProblem.business_id == business_id))).all()
    vit = gates.get("vitality")
    st = BusinessState(
        business_id=str(business_id), vitality_pass=bool(vit and vit.passed),
        can_pay=float(score.ability) >= 10, priority_class=score.cls, industry=b.trade,
        city=(b.address or {}).get("city"), state=(b.address or {}).get("state"),
        postcode=b.postcode, problems=list(problems), pressure=ps(snaps[0]),
        evidence_ids=[str(x) for x in (snaps[0].contributors or {}).get("evidence_ids", [])],
    )
    return st, (ps(snaps[1]) if len(snaps) > 1 else None)


async def operator_verified_aspects(s: AsyncSession, business_id: UUID) -> frozenset[str]:
    """Warning-sign types whose evidence an operator checked by hand (manually_verified)."""
    rows = (await s.execute(select(m.TriggerEvent.type).join(
        m.EvidenceEvent, m.EvidenceEvent.id == m.TriggerEvent.evidence_id).where(
        m.TriggerEvent.business_id == business_id,
        m.EvidenceEvent.state == State.MANUALLY_VERIFIED.value))).scalars().all()
    return frozenset(rows)


async def reverify(s: AsyncSession) -> int:
    """Re-mark existing alerts with the current verification rule. Returns alerts changed."""
    prec = await precision_map(s)
    changed = 0
    for a in (await s.scalars(select(m.PanicAlert))).all():
        unverified = matcher.is_unverified(
            a.aspect, prec, await operator_verified_aspects(s, a.business_id))
        if unverified != a.unverified:
            a.unverified = unverified
            changed += 1
    return changed


async def evaluate(s: AsyncSession, business_id: UUID) -> list[m.PanicAlert]:
    got = await business_state(s, business_id)
    if got is None:
        return []
    st, prev = got
    prec = await precision_map(s)
    verified = await operator_verified_aspects(s, business_id)
    t = now()
    created: list[m.PanicAlert] = []
    watches = (await s.scalars(select(m.PanicWatch).where(
        m.PanicWatch.enabled.is_(True),
        or_(m.PanicWatch.expires_at.is_(None), m.PanicWatch.expires_at > t)))).all()
    for w in watches:
        rule = WatchRule.model_validate(w.rule)
        d = matcher.decide(st, prev, rule, prec, verified)
        if not d.matched or d.event is None or d.aspect is None:
            continue
        key = matcher.dedupe_key(st.business_id, d.aspect, d.event, t, w.cooldown_days,
                                 st.pressure.index)
        res = await s.execute(insert(m.PanicAlert).values(
            watch_id=w.id, business_id=business_id, event=d.event, aspect=d.aspect,
            pressure=int(st.pressure.index), direction=st.pressure.direction,
            evidence_ids=[UUID(x) for x in st.evidence_ids], dedupe_key=key,
            unverified=d.unverified, status="pending",
        ).on_conflict_do_nothing(index_elements=["watch_id", "dedupe_key"])
            .returning(m.PanicAlert.id))
        aid = res.scalar_one_or_none()
        if aid is None:
            continue  # already alerted in this cooldown bucket
        alert = await s.get(m.PanicAlert, aid)
        assert alert is not None
        await _route(s, w, alert)
        created.append(alert)
    return created


async def _route(s: AsyncSession, w: m.PanicWatch, a: m.PanicAlert) -> None:
    t = now()
    hour = int(await s.scalar(select(func.count()).select_from(m.PanicAlert).where(
        m.PanicAlert.watch_id == w.id, m.PanicAlert.status == "sent",
        m.PanicAlert.created_at >= t - timedelta(hours=1))) or 0)
    day = int(await s.scalar(select(func.count()).select_from(m.PanicAlert).where(
        m.PanicAlert.watch_id == w.id, m.PanicAlert.status == "sent",
        m.PanicAlert.created_at >= t - timedelta(days=1))) or 0)
    if hour >= w.cap_per_hour or day >= w.cap_per_day:
        a.status = "held"
        return
    if a.unverified or w.style != "instant":
        a.status = "digested"
        return
    if quiet.should_hold(t, w.quiet_start, w.quiet_end, w.tz, a.pressure, w.breakthrough_min):
        a.status = "held"
        return
    await deliver(s, a.id)


async def deliver(s: AsyncSession, alert_id: UUID) -> bool:
    a = await s.get(m.PanicAlert, alert_id)
    assert a is not None
    w = await s.get(m.PanicWatch, a.watch_id)
    b = await s.get(m.Business, a.business_id)
    assert w is not None and b is not None
    snap = await s.scalar(select(m.ScoreSnapshot).where(m.ScoreSnapshot.business_id == b.id)
                          .order_by(m.ScoreSnapshot.created_at.desc()).limit(1))
    c = render.card(alert_id=str(b.id), business_name=b.canonical_name, industry=b.trade,
                    area=(b.address or {}).get("city"), aspect=a.aspect, pressure=a.pressure,
                    direction=a.direction, klass=snap.cls if snap else "?",
                    evidence_count=len(a.evidence_ids or []),
                    confidence=snap.confidence if snap else "low", created_at=a.created_at or now(),
                    now=now(), base_url=get_settings().public_base_url)
    channels = [await s.get(m.NotificationChannel, cid) for cid in (w.channel_order or [])]
    for ch in [x for x in channels if x is not None and x.healthy]:
        res = await notifier_for(ch.kind, ch.config).send(c)
        s.add(m.AlertDelivery(alert_id=a.id, channel_id=ch.id, attempted_at=now(), ok=res.ok,
                              detail=res.detail[:500]))
        if res.ok:
            ch.last_ok_at = now()
            a.status = "sent"
            return True
    a.status = "sent" if not channels else "failed"  # in-app inbox always has it
    return a.status == "sent"


async def feedback(s: AsyncSession, alert_id: UUID, kind: str) -> None:
    a = await s.get(m.PanicAlert, alert_id)
    assert a is not None
    s.add(m.AlertFeedback(alert_id=alert_id, kind=kind, at=now()))
    w = await s.get(m.PanicWatch, a.watch_id)
    assert w is not None
    if kind == "not_a_fit":
        w.consecutive_not_a_fit += 1
        if w.consecutive_not_a_fit >= 10:
            w.enabled = False
    else:
        w.consecutive_not_a_fit = 0
    if kind == "mute_watch":
        w.enabled = False
    a.status = "dismissed" if kind in ("not_a_fit", "mute_watch") else "read"
