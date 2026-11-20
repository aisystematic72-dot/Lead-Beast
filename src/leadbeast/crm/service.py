"""CRM sync: one way for evidence (notes, fields), two way for stage. Hash-skips unchanged rows."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.core.ports import CrmAdapter
from leadbeast.db import models as m
from leadbeast.detectors.runner import business_from_row
from leadbeast.domain.business import Contact
from leadbeast.scoring.service import latest_snapshot


def get_crm(name: str | None = None) -> CrmAdapter:
    name = name or ("espocrm" if get_settings().espocrm_url else "csv")
    if name == "espocrm":
        from leadbeast.crm.espocrm import EspoCrmAdapter

        return EspoCrmAdapter()
    from leadbeast.crm.csv_crm import CsvCrmAdapter

    return CsvCrmAdapter()


async def sync_business(s: AsyncSession, business_id: UUID, crm: CrmAdapter | None = None) -> str:
    crm = crm or get_crm()
    b = await s.get(m.Business, business_id)
    assert b is not None
    snap = await latest_snapshot(s, business_id)
    snap_d = {"class": snap.cls, "total": float(snap.total), "confidence": snap.confidence,
              "gap_types": snap.gap_types} if snap else {}
    h = hashlib.sha256(json.dumps({"b": [b.canonical_name, b.phone_e164, b.website_url],
                                   "s": snap_d}, sort_keys=True).encode()).hexdigest()
    row = await s.scalar(select(m.CrmSync).where(m.CrmSync.business_id == business_id,
                                                 m.CrmSync.crm == crm.name))
    if row is not None and row.hash == h:
        return row.crm_id
    crm_id = await crm.upsert_business(business_from_row(b), snap_d, row.crm_id if row else None)
    for c in (await s.scalars(select(m.Contact).where(m.Contact.business_id == business_id,
                                                      m.Contact.eligible.is_(True)))).all():
        await crm.upsert_contact(Contact(business_id=business_id, name=c.name, role=c.role,
                                         email=c.email, phone_e164=c.phone_e164), crm_id)
    if row is None:
        s.add(m.CrmSync(business_id=business_id, crm=crm.name, crm_id=crm_id,
                        last_synced_at=now(), hash=h))
    else:
        row.crm_id, row.hash, row.last_synced_at = crm_id, h, now()
    return crm_id
