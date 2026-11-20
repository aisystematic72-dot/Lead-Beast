"""Run stages inline (no queue) for one business. Used by Launch Lab and the CLI."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

from leadbeast.contacts.service import find_contacts
from leadbeast.core.db import session
from leadbeast.core.http import Fetcher
from leadbeast.core.ids import new_run_id
from leadbeast.core.ports import SourceQuery
from leadbeast.db import models as m
from leadbeast.detectors.registry import enabled
from leadbeast.detectors.runner import run_detectors
from leadbeast.identity.service import resolve_record
from leadbeast.packs.service import pack_for_campaign
from leadbeast.scoring.service import score_business
from leadbeast.sources import get_adapter
from leadbeast.sources.service import collect


async def process_business(business_id: UUID, campaign_id: UUID, run_id: str,
                           fetcher: Fetcher | None = None) -> str:
    async with session() as s:
        pack = await pack_for_campaign(s, campaign_id)
        await run_detectors(s, business_id, pack, run_id, fetcher=fetcher)
    async with session() as s:
        pack = await pack_for_campaign(s, campaign_id)
        await find_contacts(s, business_id, pack, run_id, fetcher=fetcher)
    async with session() as s:
        snap, _ = await score_business(s, business_id, campaign_id)
        return snap.cls


async def dry_run(campaign_id: UUID, source: str, sample: int = 50,
                  path: str | None = None, full_size_estimate: int | None = None
                  ) -> dict[str, Any]:
    """Launch Lab: stages 1-6 on a small sample; report class mix and projected full cost."""
    run_id = new_run_id()
    async with session() as s:
        camp = await s.get(m.Campaign, campaign_id)
        assert camp is not None
        pack = await pack_for_campaign(s, campaign_id)
        adapter = get_adapter(source, **({"path": Path(path)} if path else {}))
        ids = await collect(s, adapter, SourceQuery(area=camp.area, trades=pack.trades,
                                                    limit=sample), campaign_id, run_id)
        camp.status = "dry_run"
    biz_ids: list[UUID] = []
    for rid in ids:
        async with session() as s:
            bid, _ = await resolve_record(s, rid, campaign_id)
            if bid and bid not in biz_ids:
                biz_ids.append(bid)
    classes: Counter[str] = Counter()
    async with Fetcher() as f:
        for bid in biz_ids:
            classes[await process_business(bid, campaign_id, run_id, fetcher=f)] += 1
    per_biz_cents = sum(d.cost_cents for d in enabled(pack.detectors)) + adapter.cost_per_unit_cents
    n = len(biz_ids) or 1
    full = full_size_estimate or n
    ab_share = (classes["A"] + classes["B"]) / n
    return {
        "run_id": run_id, "sampled_records": len(ids), "businesses": len(biz_ids),
        "class_mix": dict(classes),
        "projected": {"businesses": full, "detector_cents": round(per_biz_cents * full, 2),
                      "expected_a_or_b": round(ab_share * full),
                      "note": "free sources only; paid contact lookups run for A/B and are capped"},
    }
