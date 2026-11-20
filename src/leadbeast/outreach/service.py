"""Draft, approve, send, and receive. The policy engine runs at draft time AND at send time."""

from __future__ import annotations

import re
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.contacts.service import best_contact
from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.core.errors import NotFound, PolicyDenied, ValidationFailed
from leadbeast.core.ports import ApprovedDraft, InboundMessage
from leadbeast.db import models as m
from leadbeast.evidence.ledger import emit, load_facts
from leadbeast.golden.service import precision_map
from leadbeast.outreach.channels import channel_for
from leadbeast.outreach.compose import compose, footer
from leadbeast.outreach.lint import lint
from leadbeast.packs.schema import PackSpec
from leadbeast.packs.service import offer_for_campaign, pack_for_campaign
from leadbeast.policy import engine as P
from leadbeast.policy import suppression
from leadbeast.policy.log import record
from leadbeast.scoring.service import latest_snapshot

STOP_RE = re.compile(r"^\s*(stop|unsubscribe|remove me|opt out)\b", re.I)


def _pressure_signals(pack: PackSpec) -> set[str]:
    return {t.id for t in pack.pressure.triggers}


def _restricted_problem_signals(pack: PackSpec) -> set[str]:
    return {s.signal for p in pack.problems if not p.use_in_message for s in p.symptoms}


async def _review_cleared(s: AsyncSession, business_id: UUID) -> bool:
    open_item = await s.scalar(select(m.ReviewItem.id).where(
        m.ReviewItem.business_id == business_id, m.ReviewItem.resolved_at.is_(None)).limit(1))
    cleared = await s.scalar(select(m.ReviewItem.id).where(
        m.ReviewItem.business_id == business_id, m.ReviewItem.resolution == "cleared").limit(1))
    return open_item is None and cleared is not None


async def _effective_class(s: AsyncSession, business_id: UUID, campaign_id: UUID) -> str | None:
    snap = await latest_snapshot(s, business_id, campaign_id)
    if snap is None:
        return None
    if snap.cls == "review" and await _review_cleared(s, business_id):
        return "B"
    return snap.cls


async def _competitor_overlap(s: AsyncSession, business_id: UUID, competes: list[str]
                              ) -> tuple[str, ...]:
    if not competes:
        return ()
    rows = (await s.scalars(select(m.BusinessOffering.category).where(
        m.BusinessOffering.business_id == business_id))).all()
    return tuple(sorted(set(rows) & set(competes)))


async def build_context(s: AsyncSession, draft: m.OutreachDraft, body: str | None = None
                        ) -> P.PolicyContext:
    st = get_settings()
    assert draft.campaign_id is not None
    pack = await pack_for_campaign(s, draft.campaign_id)
    offer = await offer_for_campaign(s, draft.campaign_id)
    camp = await s.get(m.Campaign, draft.campaign_id)
    facts = await load_facts(s, draft.business_id, pack)
    prec = await precision_map(s)
    pressure = _pressure_signals(pack)
    restricted = _restricted_problem_signals(pack)

    cited: list[P.CitedSignal] = []
    for eid in draft.cited_evidence_ids or []:
        ev = await s.get(m.EvidenceEvent, eid)
        if ev is None:
            continue
        cited.append(P.CitedSignal(
            signal=ev.signal, state=facts.state(ev.signal).value, precision=prec.get(ev.signal),
            is_pressure=ev.signal in pressure, use_in_message=ev.signal not in restricted))

    contact = await s.get(m.Contact, draft.contact_id) if draft.contact_id else None
    biz = await s.get(m.Business, draft.business_id)
    sup = await suppression.matches(
        s, email=contact.email if contact else None,
        phone=contact.phone_e164 if contact else None,
        domain=biz.domain if biz else None, business_id=draft.business_id)

    approval = await s.scalar(select(m.Approval).where(
        m.Approval.draft_id == draft.id, m.Approval.decision.in_(("approve", "edit")))
        .order_by(m.Approval.decided_at.desc()).limit(1))
    mystery = await s.scalar(select(m.MysteryCheck).where(
        m.MysteryCheck.business_id == draft.business_id, m.MysteryCheck.passed.is_(True)).limit(1))
    day0 = now().replace(hour=0, minute=0, second=0, microsecond=0)
    sends_today = int(await s.scalar(select(func.count()).select_from(m.Message).where(
        m.Message.direction == "out", m.Message.channel == draft.channel,
        m.Message.at >= day0)) or 0)
    dom = None
    if st.sender_email:
        dom = await s.scalar(select(m.SendingDomain).where(
            m.SendingDomain.domain == st.sender_email.split("@")[-1]))
    q0 = tuple(int(x) for x in pack.policy.quiet_start.split(":"))
    q1 = tuple(int(x) for x in pack.policy.quiet_end.split(":"))
    from datetime import time as _t

    text = body if body is not None else draft.body
    return P.PolicyContext(
        priority_class=await _effective_class(s, draft.business_id, draft.campaign_id),
        channel=draft.channel, suppressed=sup, body=text, cited=tuple(cited),
        distress_words=tuple(st.distress_words),
        competitor_overlap=await _competitor_overlap(s, draft.business_id,
                                                     list(offer.competes_with or [])),
        partner_campaign=bool(camp and camp.partner), open_conflict=bool(facts.conflicts()),
        approval_hash=approval.approved_hash if approval else None,
        mystery_check_passed=mystery is not None, sends_today=sends_today,
        daily_cap=min(pack.policy.daily_send_cap, dom.daily_cap if dom else 10**6),
        sender_paused=bool(dom and dom.paused),
        recipient_local_time=P.local_time_for(now(), st.default_recipient_tz),
        quiet_start=_t(*q0), quiet_end=_t(*q1), operator_identity_complete=st.operator_complete,
        has_unsubscribe="unsubscribe" in text.lower(),
        allowed_channels=tuple(pack.policy.allowed_channels) + ("manual",),
        threshold=st.golden_precision_threshold,
    )


async def draft_outreach(s: AsyncSession, business_id: UUID, campaign_id: UUID,
                         actor: str = "system") -> m.OutreachDraft:
    st = get_settings()
    pack = await pack_for_campaign(s, campaign_id)
    cls = await _effective_class(s, business_id, campaign_id)
    if cls not in ("A", "B"):
        raise ValidationFailed(f"class {cls} is not eligible for a draft")
    audit = await s.scalar(select(m.Audit).where(m.Audit.business_id == business_id)
                           .order_by(m.Audit.created_at.desc()).limit(1))
    if audit is None or audit.status != "ready":
        raise ValidationFailed("needs a ready Visibility Audit first")
    contact = await best_contact(s, business_id)
    if contact is None:
        raise ValidationFailed("no eligible contact")
    biz = await s.get(m.Business, business_id)
    assert biz is not None
    template = getattr(pack.templates, "opener_email", "") or ""
    if not template:
        raise ValidationFailed("pack has no opener_email template")
    c = compose(template, pack.templates.opener_email_subject, business_name=biz.canonical_name,
                trade=biz.trade, city=(biz.address or {}).get("city"), contact_name=contact.name,
                findings=audit.findings.get("items", []), operator_name=st.operator_name,
                operator_phone=st.operator_phone)
    if not c.cited_evidence_ids:
        raise ValidationFailed("no cited, qualified findings to mention")
    channel = "email" if contact.email else "manual"
    body = c.body
    if channel == "email":
        body += footer(st.operator_name, st.operator_address,
                       f"{st.unsubscribe_base_url}?b={business_id}")
    draft = m.OutreachDraft(
        business_id=business_id, campaign_id=campaign_id, contact_id=contact.id, channel=channel,
        subject=c.subject, body=body, body_hash=P.body_hash(body),
        cited_evidence_ids=[UUID(x) for x in c.cited_evidence_ids], status="draft",
    )
    s.add(draft)
    await s.flush()
    ctx = await build_context(s, draft)
    decision = P.check("draft", ctx)
    lr = lint(body, st.distress_words,
              [x.signal for x in ctx.cited if x.is_pressure or not x.use_in_message])
    await record(s, "draft", ctx, decision, actor)
    if decision.verdict is P.Verdict.DENY or not lr.ok:
        draft.status = "blocked"
        draft.blocked_reasons = list(decision.reasons) + list(lr.problems)
    else:
        await emit(s, "DraftReady", business_id, {"draft_id": str(draft.id)})
    return draft


async def decide(s: AsyncSession, draft_id: UUID, user: str, decision: str,
                 edited_body: str | None = None) -> m.OutreachDraft:
    draft = await s.get(m.OutreachDraft, draft_id)
    if draft is None:
        raise NotFound(f"draft {draft_id}")
    if draft.status not in ("draft", "approved"):
        raise ValidationFailed(f"draft is {draft.status}")
    if decision not in ("approve", "reject", "edit"):
        raise ValidationFailed("decision must be approve, reject or edit")
    approved_hash = None
    if decision == "edit":
        if not edited_body:
            raise ValidationFailed("edit needs a new body")
        draft.body = edited_body.strip()
        draft.body_hash = P.body_hash(draft.body)
        ctx = await build_context(s, draft)
        lr = lint(draft.body, get_settings().distress_words, [])
        dec = P.check("draft", ctx)
        await record(s, "draft", ctx, dec, user)
        if dec.verdict is P.Verdict.DENY or not lr.ok:
            raise PolicyDenied(list(dec.reasons) + list(lr.problems))
        approved_hash = draft.body_hash
        draft.status = "approved"
    elif decision == "approve":
        approved_hash = draft.body_hash
        draft.status = "approved"
    else:
        draft.status = "rejected"
    s.add(m.Approval(draft_id=draft.id, user_id=user, decision=decision,
                     edited_body=edited_body if decision == "edit" else None,
                     approved_hash=approved_hash, decided_at=now()))
    if draft.status == "approved":
        await emit(s, "DraftApproved", draft.business_id, {"draft_id": str(draft.id)})
    return draft


async def record_mystery_check(s: AsyncSession, business_id: UUID, user: str, *, searched: bool,
                               called: bool, site_opened: bool, passed: bool,
                               notes: str | None = None) -> m.MysteryCheck:
    from leadbeast.evidence.ledger import append, manual_event

    row = m.MysteryCheck(business_id=business_id, user_id=user, searched=searched, called=called,
                         site_opened=site_opened, passed=passed, notes=notes)
    s.add(row)
    await append(s, [manual_event(business_id, "mystery_check", passed, user, notes)],
                 recorded_by=user)
    return row


async def send_draft(s: AsyncSession, draft_id: UUID, actor: str = "system") -> m.Message:
    draft = await s.get(m.OutreachDraft, draft_id)
    if draft is None:
        raise NotFound(f"draft {draft_id}")
    if draft.status != "approved":
        raise PolicyDenied([f"draft_status:{draft.status}"])
    ctx = await build_context(s, draft)
    decision = P.check("send", ctx)  # fresh check at send time
    await record(s, "send", ctx, decision, actor)
    if not decision.allowed:
        raise PolicyDenied(list(decision.reasons))
    contact = await s.get(m.Contact, draft.contact_id) if draft.contact_id else None
    conv = await s.scalar(select(m.Conversation).where(
        m.Conversation.business_id == draft.business_id).limit(1))
    if conv is None:
        conv = m.Conversation(business_id=draft.business_id,
                              contact_id=contact.id if contact else None)
        s.add(conv)
        await s.flush()
    st = get_settings()
    receipt = await channel_for(draft.channel).send(ApprovedDraft(
        draft_id=str(draft.id), to=(contact.email or "") if contact else "",
        subject=draft.subject or "", body=draft.body,
        headers={"List-Unsubscribe": f"<{st.unsubscribe_base_url}?b={draft.business_id}>"},
    ))
    if not receipt.accepted:
        raise PolicyDenied([f"provider_rejected:{receipt.detail}"])
    msg = m.Message(conversation_id=conv.id, draft_id=draft.id, direction="out",
                    channel=draft.channel, body=draft.body, provider_id=receipt.provider_id,
                    at=now())
    s.add(msg)
    conv.thread_key = receipt.provider_id
    draft.status = "sent"
    return msg


async def handle_inbound(s: AsyncSession, inbound: InboundMessage) -> str:
    """Match a reply to its conversation; STOP, bounces and complaints write suppression."""
    out_msg = None
    if inbound.in_reply_to:
        out_msg = await s.scalar(select(m.Message).where(
            m.Message.provider_id == inbound.in_reply_to).limit(1))
    conv = await s.get(m.Conversation, out_msg.conversation_id) if out_msg else None
    biz_id = conv.business_id if conv else None
    if inbound.kind == "bounce_hard" or inbound.kind == "unsubscribe" or STOP_RE.match(
            inbound.body or ""):
        await suppression.add(s, "email", inbound.sender, inbound.kind if inbound.kind != "reply"
                              else "stop_reply")
        result = "suppressed"
    elif inbound.kind == "complaint":
        await suppression.add(s, "email", inbound.sender, "complaint")
        if biz_id:
            await suppression.add(s, "business", str(biz_id), "complaint")
        await _maybe_pause_sender(s)
        result = "complaint"
    else:
        result = "reply"
    if conv is not None:
        s.add(m.Message(conversation_id=conv.id, direction="in", channel="email",
                        body=inbound.body, provider_id=inbound.provider_id,
                        sender=inbound.sender, at=inbound.at))
        if result == "reply":
            conv.status = "replied"
            s.add(m.Outcome(business_id=conv.business_id, result="replied",
                            reason_code="inbound_reply", at=now()))
    return result


async def _maybe_pause_sender(s: AsyncSession, threshold: float = 0.001) -> None:
    st = get_settings()
    if not st.sender_email:
        return
    dom = await s.scalar(select(m.SendingDomain).where(
        m.SendingDomain.domain == st.sender_email.split("@")[-1]))
    if dom is None:
        return
    since = now() - timedelta(days=7)
    sent = int(await s.scalar(select(func.count()).select_from(m.Message).where(
        m.Message.direction == "out", m.Message.at >= since)) or 0)
    complaints = int(await s.scalar(select(func.count()).select_from(m.Suppression).where(
        m.Suppression.reason == "complaint", m.Suppression.kind == "email",
        m.Suppression.added_at >= since)) or 0)
    if sent and complaints / sent > threshold:
        dom.paused = True


async def log_outcome(s: AsyncSession, business_id: UUID, campaign_id: UUID | None, result: str,
                      reason_code: str | None, value: float | None = None) -> m.Outcome:
    from decimal import Decimal

    row = m.Outcome(business_id=business_id, campaign_id=campaign_id, result=result,
                    reason_code=reason_code,
                    value=Decimal(str(value)) if value is not None else None, at=now())
    s.add(row)
    await emit(s, "OutcomeLogged", business_id, {"result": result, "reason": reason_code})
    return row
