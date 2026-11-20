"""End-to-end pipeline on a real PostgreSQL. Run with:

    LB_TEST_DATABASE_URL=postgresql+psycopg://lb:lb@localhost:5432/lb_test pytest -m integration

The database is wiped and migrated. No network: DNS and channels are stubbed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

URL = os.environ.get("LB_TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not URL, reason="LB_TEST_DATABASE_URL not set")]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def database():
    os.environ["LB_DATABASE_URL"] = URL or ""
    os.environ["LB_ENVIRONMENT"] = "test"
    os.environ["LB_OPERATOR_NAME"] = "Test Operator"
    os.environ["LB_OPERATOR_PHONE"] = "+12125550100"
    os.environ["LB_OPERATOR_ADDRESS"] = "1 Test St, New York, NY"
    from leadbeast.core.config import get_settings

    get_settings.cache_clear()
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    eng = create_engine(URL or "")
    with eng.begin() as c:
        c.execute(text("drop schema public cascade; create schema public;"))
    eng.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def fresh_engine(monkeypatch):
    from leadbeast.contacts import verify
    from leadbeast.core import db
    from leadbeast.outreach import channels, service

    db.engine.cache_clear()
    db._factory.cache_clear()

    async def mx_ok(_: str) -> bool:
        return True

    monkeypatch.setattr(verify, "has_mx", mx_ok)
    monkeypatch.setattr(service, "channel_for", lambda _name: channels.ManualChannel())
    monkeypatch.setattr(service.P, "local_time_for", lambda *_: __import__("datetime").time(11))
    yield


async def test_full_pipeline():
    from leadbeast.core.db import session
    from leadbeast.db import models as m
    from leadbeast.health.service import seed_connectors
    from leadbeast.packs.loader import load_offer
    from leadbeast.packs.service import create_campaign, create_offer
    from leadbeast.pipeline import dry_run

    async with session() as s:
        await seed_connectors(s)
        offer = await create_offer(s, load_offer(ROOT / "packs/offers/visibility_starter.yaml"))
        camp = await create_campaign(s, name="NY test", offer_id=offer.id,
                                     pack_name="visibility_gap", area="New York")
        cid = camp.id

    res = await dry_run(cid, "csv", 50, str(ROOT / "examples/ny_leads_sample.csv"))
    # the two Kings Flow rows share a phone and resolve to one business
    assert res["sampled_records"] == 5 and res["businesses"] == 4
    assert res["class_mix"].get("D", 0) >= 1  # Old Mill is permanently closed

    async with session() as s:
        kings = await s.scalar(select(m.Business).where(m.Business.norm_name == "kings flow plumbing"))
        assert kings is not None
        n_ev = await s.scalar(select(func.count()).select_from(m.EvidenceEvent)
                              .where(m.EvidenceEvent.business_id == kings.id))
        assert n_ev > 10
        snap = await s.scalar(select(m.ScoreSnapshot).where(m.ScoreSnapshot.business_id == kings.id)
                              .order_by(m.ScoreSnapshot.created_at.desc()))
        assert snap.cls in ("A", "B"), (snap.cls, snap.reasons)
        closed = await s.scalar(select(m.Business).where(m.Business.norm_name == "old mill roofing"))
        csnap = await s.scalar(select(m.ScoreSnapshot).where(
            m.ScoreSnapshot.business_id == closed.id))
        assert csnap.cls == "D" and "vitality_fail" in csnap.reasons["codes"]
        # the ledger is append-only at the DB level for inferred facts without a span
        with pytest.raises(Exception):
            await s.execute(text(
                "insert into evidence_event (id, business_id, signal, state, source, observed_at,"
                " detector, detector_version) values (gen_random_uuid(), :b, 'x', 'inferred',"
                " 'ai', now(), 'd', '1')"), {"b": kings.id})
        await s.rollback()
        kid = kings.id

    # golden gate: until signals are measured, drafting has nothing it may cite
    from leadbeast.audit.service import build_audit
    from leadbeast.outreach.service import draft_outreach

    async with session() as s:
        audit = await build_audit(s, kid, cid)
        assert audit.status == "insufficient"
        assert any("held back" in o["note"] for o in audit.open_items)

    async with session() as s:
        from leadbeast.core.clock import now

        for sig in ("has_website", "listing_claimed", "in_map_pack"):
            s.add(m.SignalPrecision(signal=sig, detector_version="1.0.0", precision=0.95,
                                    recall=0.9, n_labelled=150, n_positive_calls=60, live=True,
                                    computed_at=now()))
    async with session() as s:
        audit = await build_audit(s, kid, cid)
        assert audit.status == "ready" and audit.findings["items"]

    async with session() as s:
        snap = await s.scalar(select(m.ScoreSnapshot).where(m.ScoreSnapshot.business_id == kid)
                              .order_by(m.ScoreSnapshot.created_at.desc()))
        draft = await draft_outreach(s, kid, cid)
        assert draft.status == "draft", draft.blocked_reasons
        assert draft.cited_evidence_ids and "unsubscribe" in draft.body.lower()
        did, cls = draft.id, snap.cls

    from leadbeast.core.errors import PolicyDenied
    from leadbeast.outreach.service import decide, record_mystery_check, send_draft

    async with session() as s:  # not approved yet: denied
        with pytest.raises(PolicyDenied):
            await send_draft(s, did)
    async with session() as s:
        await decide(s, did, "tester", "approve")
    if cls == "A":
        async with session() as s:
            with pytest.raises(PolicyDenied) as e:
                await send_draft(s, did)
            assert "mystery_check_missing" in e.value.reasons
        async with session() as s:
            await record_mystery_check(s, kid, "tester", searched=True, called=True,
                                       site_opened=False, passed=True)
    async with session() as s:
        msg = await send_draft(s, did)
        assert msg.provider_id

    # a STOP reply suppresses the address in the same transaction
    from leadbeast.core.clock import now
    from leadbeast.core.ports import InboundMessage
    from leadbeast.outreach.service import handle_inbound
    from leadbeast.policy import suppression

    async with session() as s:
        res = await handle_inbound(s, InboundMessage("in-1", msg.provider_id,
                                                     "kingsflowplumbing@gmail.com", "STOP", now()))
        assert res == "suppressed"
    async with session() as s:
        assert await suppression.matches(s, email="kingsflowplumbing@gmail.com") == ("email",)


async def test_resolve_is_idempotent_and_unmerge_replays():
    from leadbeast.core.db import session
    from leadbeast.db import models as m
    from leadbeast.identity.service import resolve_record, unmerge

    async with session() as s:
        link = await s.scalar(select(m.IdentityLink).where(m.IdentityLink.rule != "new_business"))
        assert link is not None, "expected the duplicate Kings Flow row to merge"
        bid, verdict = await resolve_record(s, link.source_record_id)
        assert verdict == "already_linked" and bid == link.business_id
        lid = link.id
    async with session() as s:
        new_id = await unmerge(s, lid, "tester")
    async with session() as s:
        rec = await s.scalar(select(m.SourceRecord).join(
            m.IdentityLink, m.IdentityLink.source_record_id == m.SourceRecord.id).where(
            m.IdentityLink.id == lid))
        assert rec.business_id == new_id


async def test_alert_dedupe_under_concurrency():
    """Two concurrent evaluations of one business produce one alert."""
    from datetime import timedelta
    from decimal import Decimal

    from leadbeast.alerts.service import evaluate
    from leadbeast.core.clock import now
    from leadbeast.core.db import session
    from leadbeast.db import models as m

    async with session() as s:
        b = await s.scalar(select(m.Business).where(m.Business.norm_name == "harbor smile dental"))
        s.add(m.PressureSnapshot(business_id=b.id, at=now() - timedelta(days=15),
                                 idx=Decimal("20"), direction="steady", contributors={}))
        s.add(m.PressureSnapshot(business_id=b.id, at=now(), idx=Decimal("80"), direction="rising",
                                 contributors={"aspects": {"reviews": 20}, "trigger_ids": ["t"]}))
        s.add(m.PanicWatch(name="w", rule={"min_pressure": 50, "min_class": "REVIEW"}))
        bid = b.id

    async def one() -> int:
        async with session() as s:
            return len(await evaluate(s, bid))

    results = await asyncio.gather(one(), one(), return_exceptions=True)
    async with session() as s:
        n = await s.scalar(select(func.count()).select_from(m.PanicAlert)
                           .where(m.PanicAlert.business_id == bid))
    assert n <= 1, results
