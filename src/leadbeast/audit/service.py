"""VisibilityAudit assembler (M16): evidence + peers + payback + ranked fixes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.audit.findings import as_dicts, build_findings
from leadbeast.db import models as m
from leadbeast.evidence.ledger import emit, load_facts
from leadbeast.golden.service import live_signals
from leadbeast.intel.payback import payback
from leadbeast.intel.peers import PEER_SIGNALS, peer_summary, peers
from leadbeast.packs.service import offer_domain, offer_for_campaign, pack_for_campaign
from leadbeast.scoring.gaps import gap_types
from leadbeast.scoring.service import latest_snapshot

MIN_FINDINGS = 1


async def build_audit(s: AsyncSession, business_id: UUID, campaign_id: UUID) -> m.Audit:
    pack = await pack_for_campaign(s, campaign_id)
    offer = offer_domain(await offer_for_campaign(s, campaign_id))
    facts = await load_facts(s, business_id, pack)
    live = await live_signals(s)
    findings, open_items = build_findings(facts, live)
    gaps = sorted(g.value for g in gap_types(facts, pack.gap_thresholds))

    peer_rows = await peers(s, business_id, pack.peer_radius_km)
    values: dict[str, list[Any]] = {sig: [] for sig in PEER_SIGNALS}
    for p in peer_rows:
        pf = await load_facts(s, p.id, pack)
        for sig in PEER_SIGNALS:
            r = pf.get(sig)
            if r.is_yes or pf.is_no(sig):
                v = r.value if isinstance(r.value, int | float) and not isinstance(
                    r.value, bool) else r.is_yes
                values[sig].append(v)
    snap = await latest_snapshot(s, business_id, campaign_id)
    pb = payback(offer)
    status = "ready" if len(findings) >= MIN_FINDINGS else "insufficient"
    audit = m.Audit(
        business_id=business_id, snapshot_id=snap.id if snap else None, status=status,
        findings={"items": as_dicts(findings), "gap_types": gaps},
        peer_set={"ids": [str(p.id) for p in peer_rows], "summary": peer_summary(values),
                  "radius_km": pack.peer_radius_km},
        payback={**pb.as_dict(), "label": "Estimate"}, open_items=open_items,
    )
    s.add(audit)
    await s.flush()
    if status == "ready":
        await emit(s, "AuditReady", business_id,
                   {"audit_id": str(audit.id), "campaign_id": str(campaign_id)})
    return audit
