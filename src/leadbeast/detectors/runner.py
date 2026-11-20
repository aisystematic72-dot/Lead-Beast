"""Detect stage: run a pack's enabled detectors for one business. Free detectors first.

Per-detector timeout; a failure writes `unknown` for every declared signal, never nothing.
"""

from __future__ import annotations

import asyncio
import traceback
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.http import Fetcher
from leadbeast.core.logging import get_logger
from leadbeast.db import models as m
from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PageCache
from leadbeast.detectors.registry import enabled
from leadbeast.domain.business import Address, Business
from leadbeast.domain.evidence import EvidenceEvent
from leadbeast.evidence.ledger import append, emit, load_facts
from leadbeast.packs.schema import PackSpec

log = get_logger(__name__)
DETECTOR_TIMEOUT_S = 60


def business_from_row(b: m.Business) -> Business:
    return Business(
        id=b.id, canonical_name=b.canonical_name, phone_e164=b.phone_e164, domain=b.domain,
        website_url=b.website_url, address=Address(**(b.address or {})), lat=b.lat, lon=b.lon,
        trade=b.trade, size_band=b.size_band, status=b.status,
    )


async def build_input(s: AsyncSession, business_id: UUID, pack: PackSpec) -> DetectorInput:
    b = await s.get(m.Business, business_id)
    assert b is not None
    recs = (await s.scalars(select(m.SourceRecord).where(
        m.SourceRecord.business_id == business_id))).all()
    payloads = [{**r.payload, "_record_id": str(r.id), "_source": r.source} for r in recs]
    return DetectorInput(business=business_from_row(b), records=payloads,
                         facts=await load_facts(s, business_id, pack))


async def run_one(det: BaseDetector, inp: DetectorInput, cache: PageCache
                  ) -> tuple[list[EvidenceEvent], str | None]:
    try:
        events = await asyncio.wait_for(det.run(inp, cache), timeout=DETECTOR_TIMEOUT_S)
        return det.fill_unknown(inp, list(events), "not_emitted"), None
    except TimeoutError:
        return det.unknown_all(inp, "timeout"), "timeout"
    except Exception as e:
        log.warning("detector_failed", detector=det.name, error=repr(e))
        return det.unknown_all(inp, f"error:{type(e).__name__}"), traceback.format_exc(limit=3)


async def run_detectors(
    s: AsyncSession, business_id: UUID, pack: PackSpec, run_id: str,
    only: list[str] | None = None, fetcher: Fetcher | None = None,
) -> int:
    names = [d for d in pack.detectors if not only or d in only]
    dets = sorted(enabled(names), key=lambda d: (d.cost_cents, d.name == "operating_style"))
    inp = await build_input(s, business_id, pack)
    own = fetcher is None
    fetcher = fetcher or Fetcher()
    cache = PageCache(fetcher)
    total = 0
    try:
        for det in dets:
            started = now()
            events, err = await run_one(det, inp, cache)
            await append(s, events)
            s.add(m.DetectorRun(business_id=business_id, run_id=run_id, detector=det.name,
                                version=det.version, started_at=started, ok=err is None,
                                error=err, cost_cents=det.cost_cents))
            total += len(events)
    finally:
        if own:
            await fetcher.aclose()
    await emit(s, "EvidenceAdded", business_id, {"run_id": run_id, "events": total})
    return total
