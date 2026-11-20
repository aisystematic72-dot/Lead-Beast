"""Peer set: same trade, within the pack's radius, similar size band."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.db import models as m
from leadbeast.identity.match import haversine_km

PEER_SIGNALS = ("has_website", "listing_claimed", "review_count", "website_quality",
                "social_active", "in_map_pack")


async def peers(s: AsyncSession, business_id: UUID, radius_km: float, limit: int = 25
                ) -> list[m.Business]:
    b = await s.get(m.Business, business_id)
    if b is None or b.trade is None:
        return []
    q = select(m.Business).where(and_(m.Business.trade == b.trade, m.Business.id != b.id,
                                      m.Business.status == "active"))
    if b.lat is not None and b.lon is not None:
        deg = radius_km / 111.0
        q = q.where(m.Business.lat.between(b.lat - deg, b.lat + deg),
                    m.Business.lon.between(b.lon - deg * 1.5, b.lon + deg * 1.5))
    elif b.postcode:
        q = q.where(m.Business.postcode.like(b.postcode[:3] + "%"))
    rows = list((await s.scalars(q.limit(limit * 4))).all())
    if b.lat is not None and b.lon is not None:
        rows = [r for r in rows if r.lat is not None and r.lon is not None
                and haversine_km((b.lat, b.lon), (r.lat, r.lon)) <= radius_km]
    if b.size_band:
        same = [r for r in rows if r.size_band in (None, b.size_band)]
        rows = same or rows
    return rows[:limit]


def peer_summary(values: dict[str, list[Any]]) -> dict[str, dict[str, float | int]]:
    """Share of peers with a yes, or the median for numbers. Pure."""
    out: dict[str, dict[str, float | int]] = {}
    for sig, vals in values.items():
        nums = sorted(v for v in vals if isinstance(v, int | float) and not isinstance(v, bool))
        bools = [v for v in vals if isinstance(v, bool)]
        if nums:
            out[sig] = {"median": nums[len(nums) // 2], "n": len(nums)}
        elif bools:
            out[sig] = {"share_yes": round(sum(bools) / len(bools), 2), "n": len(bools)}
    return out
