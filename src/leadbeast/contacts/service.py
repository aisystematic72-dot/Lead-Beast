"""Contact stage: find, verify, store contacts and write reach evidence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.contacts import verify as V
from leadbeast.contacts.finder import SiteContactProvider
from leadbeast.core.clock import now
from leadbeast.core.http import Fetcher
from leadbeast.core.ids import uuid7
from leadbeast.cost.budget import Budget
from leadbeast.db import models as m
from leadbeast.detectors.helpers import PageCache
from leadbeast.detectors.runner import build_input
from leadbeast.domain.evidence import EvidenceEvent, State
from leadbeast.evidence.ledger import append, emit
from leadbeast.packs.schema import PackSpec
from leadbeast.policy import suppression

DETECTOR = "contact_finder"
VERSION = "1.0.0"


def _ev(bid: UUID, signal: str, state: State, value: Any, url: str | None) -> EvidenceEvent:
    return EvidenceEvent(id=uuid7(), business_id=bid, signal=signal, state=state, value=value,
                         source="contacts", source_url=url, observed_at=now(),
                         detector=DETECTOR, detector_version=VERSION)


async def find_contacts(s: AsyncSession, business_id: UUID, pack: PackSpec, run_id: str,
                        fetcher: Fetcher | None = None) -> int:
    inp = await build_input(s, business_id, pack)
    own = fetcher is None
    fetcher = fetcher or Fetcher()
    try:
        provider = SiteContactProvider(PageCache(fetcher), inp.records)
        found = await provider.find(inp.business)
        existing = {(c.email, c.phone_e164) for c in (await s.scalars(
            select(m.Contact).where(m.Contact.business_id == business_id))).all()}
        best_email: tuple[bool, str | None] | None = None
        best_phone: tuple[bool, str | None] | None = None
        own_domain: bool | None = None
        stored = 0
        for c in found:
            checks = await provider.verify(c)
            ok = V.eligible(checks)
            sup = await suppression.matches(s, email=c.email, phone=c.phone_e164,
                                            business_id=business_id)
            if sup:
                ok = False
            if (c.email, c.phone_e164) not in existing:
                row = m.Contact(business_id=business_id, name=c.name, role=c.role, email=c.email,
                                phone_e164=c.phone_e164, channel_pref=c.channel_pref,
                                confidence=V.confidence(c, checks), source_url=c.source_url,
                                eligible=ok)
                s.add(row)
                await s.flush()
                for ch in checks:
                    s.add(m.ContactCheck(contact_id=row.id, kind=ch.kind, result=ch.result,
                                         checked_at=ch.checked_at))
                stored += 1
            if c.email:
                if best_email is None or (ok and not best_email[0]):
                    best_email = (ok, c.source_url)
                if own_domain is None:
                    own_domain = not c.is_webmail
            if c.phone_e164 and (best_phone is None or (ok and not best_phone[0])):
                best_phone = (ok, c.source_url)
        events = [
            _ev(business_id, "contact_email_valid",
                State.NOT_DETECTED if best_email is None else
                (State.OBSERVED_YES if best_email[0] else State.OBSERVED_NO),
                None if best_email is None else best_email[0],
                None if best_email is None else best_email[1]),
            _ev(business_id, "contact_phone_valid",
                State.NOT_DETECTED if best_phone is None else
                (State.OBSERVED_YES if best_phone[0] else State.OBSERVED_NO),
                None if best_phone is None else best_phone[0],
                None if best_phone is None else best_phone[1]),
            _ev(business_id, "own_domain_email",
                State.UNKNOWN if own_domain is None else
                (State.OBSERVED_YES if own_domain else State.OBSERVED_NO), own_domain, None),
        ]
        await append(s, events)
        await Budget(s).record_free(DETECTOR, units=max(1, len(found)), run_id=run_id)
        await emit(s, "EvidenceAdded", business_id, {"stage": "contact"})
        return stored
    finally:
        if own:
            await fetcher.aclose()


async def best_contact(s: AsyncSession, business_id: UUID) -> m.Contact | None:
    rows = (await s.scalars(select(m.Contact).where(
        m.Contact.business_id == business_id, m.Contact.eligible.is_(True)))).all()
    order = {"high": 0, "medium": 1, "low": 2}
    emails = sorted((c for c in rows if c.email), key=lambda c: order.get(c.confidence, 3))
    return emails[0] if emails else (rows[0] if rows else None)


async def contact_summary(s: AsyncSession, business_id: UUID) -> dict[str, Any] | None:
    c = await best_contact(s, business_id)
    if c is None:
        return None
    sup = await suppression.matches(s, email=c.email, phone=c.phone_e164, business_id=business_id)
    return {"usable": True, "channel": "email" if c.email else "phone",
            "contact_id": str(c.id), "suppressed": bool(sup)}
