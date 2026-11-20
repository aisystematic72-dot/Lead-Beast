"""Pack and campaign persistence. Packs are versioned; a version is never edited in place."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.config import get_settings
from leadbeast.core.errors import NotFound, ValidationFailed
from leadbeast.db import models as m
from leadbeast.packs.loader import load_pack, validate_pack
from leadbeast.packs.schema import Offer, PackSpec


async def upsert_pack(s: AsyncSession, spec: PackSpec) -> m.Pack:
    row = await s.scalar(select(m.Pack).where(m.Pack.name == spec.name,
                                              m.Pack.version == spec.version))
    data = spec.model_dump(mode="json")
    if row is not None:
        if row.spec != data:
            raise ValidationFailed(
                f"pack {spec.name} {spec.version} already exists with different content; "
                "bump the version instead of editing in place")
        return row
    row = m.Pack(name=spec.name, version=spec.version, spec=data, active=True)
    s.add(row)
    await s.flush()
    for p in spec.problems:
        if await s.get(m.Problem, p.id) is None:
            s.add(m.Problem(id=p.id, pack_id=row.id, name=p.name, areas=p.areas, kind=p.kind,
                            half_life_days=p.half_life_days, spec=p.model_dump(mode="json")))
    return row


async def load_pack_from_disk(s: AsyncSession, name: str) -> m.Pack:
    from leadbeast.detectors.registry import signals_by_detector

    spec = load_pack(Path(get_settings().packs_dir) / name)
    problems = validate_pack(spec, signals_by_detector())
    if problems:
        raise ValidationFailed("; ".join(problems))
    return await upsert_pack(s, spec)


async def pack_for_campaign(s: AsyncSession, campaign_id: UUID) -> PackSpec:
    camp = await s.get(m.Campaign, campaign_id)
    if camp is None:
        raise NotFound(f"campaign {campaign_id}")
    row = await s.get(m.Pack, camp.pack_id)
    assert row is not None
    return PackSpec.model_validate(row.spec)


async def offer_for_campaign(s: AsyncSession, campaign_id: UUID) -> m.Offer:
    camp = await s.get(m.Campaign, campaign_id)
    if camp is None:
        raise NotFound(f"campaign {campaign_id}")
    offer = await s.get(m.Offer, camp.offer_id)
    assert offer is not None
    return offer


def offer_domain(o: m.Offer) -> Offer:
    return Offer(id=str(o.id), name=o.name, monthly_fee=float(o.monthly_fee),
                 customer_value_low=float(o.customer_value_low),
                 customer_value_high=float(o.customer_value_high),
                 service_fit_profile=o.service_fit_profile or {},
                 competes_with=list(o.competes_with or []), provisional=o.provisional)


async def create_offer(s: AsyncSession, offer: Offer) -> m.Offer:
    row = m.Offer(name=offer.name, monthly_fee=Decimal(str(offer.monthly_fee)),
                  customer_value_low=Decimal(str(offer.customer_value_low)),
                  customer_value_high=Decimal(str(offer.customer_value_high)),
                  service_fit_profile=offer.service_fit_profile,
                  competes_with=offer.competes_with, provisional=offer.provisional)
    s.add(row)
    await s.flush()
    return row


async def create_campaign(
    s: AsyncSession, *, name: str, offer_id: UUID, pack_name: str, area: str,
    size_min: int | None = None, size_max: int | None = None, budget_cap: int = 0,
    target_by: dict[str, Any] | None = None, partner: bool = False,
) -> m.Campaign:
    if await s.get(m.Offer, offer_id) is None:  # rule 1: offer first
        raise ValidationFailed("campaign needs an existing offer")
    pack = await s.scalar(select(m.Pack).where(m.Pack.name == pack_name, m.Pack.active.is_(True))
                          .order_by(m.Pack.created_at.desc()))
    if pack is None:
        pack = await load_pack_from_disk(s, pack_name)
    spec = PackSpec.model_validate(pack.spec)
    camp = m.Campaign(name=name, offer_id=offer_id, pack_id=pack.id, area=area,
                      size_min=size_min or spec.size_min, size_max=size_max or spec.size_max,
                      budget_cap=budget_cap, target_by=target_by or {}, partner=partner,
                      status="draft")
    s.add(camp)
    await s.flush()
    return camp
