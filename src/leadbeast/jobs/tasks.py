"""Pipeline stages as Procrastinate tasks. Each takes (business_id, campaign_id, run_id) only;
everything else is read from the database, so a replay is deterministic. Next stages are
enqueued by the outbox dispatcher, never called directly."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from procrastinate import RetryStrategy
from sqlalchemy import select, update

from leadbeast.core.clock import now
from leadbeast.core.db import session
from leadbeast.core.errors import CircuitOpen, PolicyDenied, ValidationFailed
from leadbeast.core.ids import new_run_id
from leadbeast.core.logging import bind_run, get_logger
from leadbeast.core.ports import SourceQuery
from leadbeast.db import models as m
from leadbeast.evidence.ledger import pending_outbox
from leadbeast.jobs.app import app

log = get_logger(__name__)


# ------------------------------------------------------------------ stage 1: collect
@app.task(name="collect_source", queue="collect", retry=RetryStrategy(max_attempts=3,
                                                                      exponential_wait=5))
async def collect_source(campaign_id: str, source: str, run_id: str, path: str | None = None,
                         limit: int = 500) -> int:
    from leadbeast.identity.service import resolve_record
    from leadbeast.packs.service import pack_for_campaign
    from leadbeast.sources import get_adapter
    from leadbeast.sources.service import collect

    bind_run(run_id, stage="collect", campaign_id=campaign_id)
    async with session() as s:
        camp = await s.get(m.Campaign, UUID(campaign_id))
        assert camp is not None
        pack = await pack_for_campaign(s, camp.id)
        kw = {"path": Path(path)} if path else {}
        adapter = get_adapter(source, **kw)
        try:
            ids = await collect(s, adapter, SourceQuery(area=camp.area, trades=pack.trades,
                                                        limit=limit), camp.id, run_id)
        except CircuitOpen:
            log.warning("circuit_open", source=source)
            return 0
        camp.status = "running"
    # stage 2 runs per record, in its own transactions
    for rid in ids:
        async with session() as s:
            await resolve_record(s, rid, UUID(campaign_id))
    log.info("collected", n=len(ids))
    return len(ids)


# ------------------------------------------------------------------ stage 2: resolve
@app.task(name="resolve_identity", queue="resolve", retry=2)
async def resolve_identity(source_record_id: str, campaign_id: str, run_id: str) -> None:
    from leadbeast.identity.service import resolve_record

    async with session() as s:
        await resolve_record(s, UUID(source_record_id), UUID(campaign_id))


# ------------------------------------------------------------------ stage 3+4: detect
@app.task(name="run_detectors", queue="detect", retry=2)
async def run_detectors(business_id: str, campaign_id: str, run_id: str,
                        only: list[str] | None = None) -> int:
    from leadbeast.contacts.service import find_contacts
    from leadbeast.detectors.runner import run_detectors as run
    from leadbeast.packs.service import pack_for_campaign

    bind_run(run_id, stage="detect", business_id=business_id)
    async with session() as s:
        pack = await pack_for_campaign(s, UUID(campaign_id))
        n = await run(s, UUID(business_id), pack, run_id, only=only)
    async with session() as s:  # free contact finding: site + records
        pack = await pack_for_campaign(s, UUID(campaign_id))
        await find_contacts(s, UUID(business_id), pack, run_id)
    return n


# ------------------------------------------------------------------ stage 6: score
@app.task(name="score_business", queue="score", retry=2)
async def score_business(business_id: str, campaign_id: str, run_id: str) -> str:
    from leadbeast.scoring.service import score_business as do_score

    async with session() as s:
        snap, _ = await do_score(s, UUID(business_id), UUID(campaign_id))
        return snap.cls


# ------------------------------------------------------------------ stage 5: audit
@app.task(name="build_audit", queue="audit", retry=2)
async def build_audit(business_id: str, campaign_id: str, run_id: str) -> str:
    from leadbeast.audit.report import HtmlPdfRenderer, save
    from leadbeast.audit.service import build_audit as do_audit
    from leadbeast.web.render import audit_context

    async with session() as s:
        audit = await do_audit(s, UUID(business_id), UUID(campaign_id))
        ctx = await audit_context(s, audit)
        audit.report_path = str(save(str(audit.id), HtmlPdfRenderer().html(ctx)))
        return audit.status


# ------------------------------------------------------------------ stage 8: draft
@app.task(name="draft_outreach", queue="draft", retry=1)
async def draft_outreach(business_id: str, campaign_id: str, run_id: str) -> str | None:
    from leadbeast.outreach.service import draft_outreach as do_draft

    try:
        async with session() as s:
            d = await do_draft(s, UUID(business_id), UUID(campaign_id))
            return d.status
    except ValidationFailed as e:
        log.info("draft_skipped", business_id=business_id, reason=str(e))
        return None


# ------------------------------------------------------------------ stage 10: send and learn
@app.task(name="send_message", queue="send", retry=0)
async def send_message(draft_id: str, run_id: str) -> str:
    from leadbeast.outreach.service import send_draft

    try:
        async with session() as s:
            msg = await send_draft(s, UUID(draft_id))
            return msg.provider_id or ""
    except PolicyDenied as e:
        log.warning("send_denied", draft_id=draft_id, reasons=e.reasons)
        return "denied"


@app.task(name="crm_sync", queue="crm", retry=RetryStrategy(max_attempts=5, exponential_wait=10))
async def crm_sync(business_id: str, campaign_id: str, run_id: str) -> str:
    from leadbeast.crm.service import sync_business

    async with session() as s:
        return await sync_business(s, UUID(business_id))


# ------------------------------------------------------------------ alerts (M24)
@app.task(name="evaluate_watches", queue="alerts",
          retry=RetryStrategy(max_attempts=5, exponential_wait=2))
async def evaluate_watches(business_id: str) -> int:
    from leadbeast.alerts.service import evaluate

    async with session() as s:
        return len(await evaluate(s, UUID(business_id)))


@app.task(name="deliver_alert", queue="alerts",
          retry=RetryStrategy(max_attempts=5, exponential_wait=2))
async def deliver_alert(alert_id: str) -> bool:
    from leadbeast.alerts.service import deliver

    async with session() as s:
        return await deliver(s, UUID(alert_id))


@app.periodic(cron="*/10 * * * *")
@app.task(name="release_held_alerts", queue="alerts", queueing_lock="release_held_alerts")
async def release_held_alerts(timestamp: int) -> int:
    from leadbeast.alerts import quiet

    n = 0
    async with session() as s:
        rows = (await s.execute(select(m.PanicAlert, m.PanicWatch).join(
            m.PanicWatch, m.PanicWatch.id == m.PanicAlert.watch_id).where(
            m.PanicAlert.status == "held"))).all()
        for a, w in rows:
            if not quiet.in_quiet(now(), w.quiet_start, w.quiet_end, w.tz):
                await deliver_alert.defer_async(alert_id=str(a.id))
                n += 1
    return n


# ------------------------------------------------------------------ outbox dispatcher
async def _campaigns_of(s, business_id: UUID) -> list[UUID]:  # type: ignore[no-untyped-def]
    return list((await s.scalars(select(m.CampaignBusiness.campaign_id).where(
        m.CampaignBusiness.business_id == business_id))).all())


@app.periodic(cron="* * * * *")
@app.task(name="dispatch_outbox", queue="outbox", queueing_lock="dispatch_outbox")
async def dispatch_outbox(timestamp: int) -> int:
    """Turn domain events into next-stage jobs. Ordered, retry-safe, no broker."""
    handled = 0
    for _ in range(20):
        async with session() as s:
            batch = await pending_outbox(s, 100)
            if not batch:
                break
            for ev in batch:
                try:
                    await _route(s, ev)
                    ev.processed_at = now()
                except Exception as e:  # leave for retry, but count attempts
                    ev.attempts += 1
                    log.error("outbox_route_failed", kind=ev.kind, error=repr(e))
                    if ev.attempts >= 5:
                        ev.processed_at = now()
                handled += 1
    return handled


async def _route(s, ev: m.EventOutbox) -> None:  # type: ignore[no-untyped-def]
    run_id = ev.payload.get("run_id") or new_run_id()
    bid = ev.business_id
    if ev.kind == "BusinessResolved" and bid:
        for cid in await _campaigns_of(s, bid):
            await run_detectors.defer_async(business_id=str(bid), campaign_id=str(cid),
                                            run_id=run_id)
    elif ev.kind == "EvidenceAdded" and bid:
        for cid in await _campaigns_of(s, bid):
            await score_business.configure(
                queueing_lock=f"score:{bid}:{cid}").defer_async(
                business_id=str(bid), campaign_id=str(cid), run_id=run_id)
    elif ev.kind == "ClassChanged" and bid:
        to, cid = ev.payload.get("to"), ev.payload.get("campaign_id")
        if to in ("A", "B") and cid:
            await build_audit.defer_async(business_id=str(bid), campaign_id=cid, run_id=run_id)
    elif ev.kind == "AuditReady" and bid:
        cid = ev.payload.get("campaign_id")
        if cid:
            await draft_outreach.defer_async(business_id=str(bid), campaign_id=cid, run_id=run_id)
    elif ev.kind == "DraftApproved":
        await send_message.defer_async(draft_id=ev.payload["draft_id"], run_id=run_id)
    elif ev.kind in ("OutcomeLogged",) and bid:
        await crm_sync.defer_async(business_id=str(bid), campaign_id="", run_id=run_id)
    elif ev.kind == "PressureChanged" and bid:
        await evaluate_watches.defer_async(business_id=str(bid))
    # ScoreChanged, ConflictRaised, BudgetWarning, CircuitChanged: read by UI / alerts


# ------------------------------------------------------------------ schedules
@app.periodic(cron="0 3 * * *")
@app.task(name="refresh_stale", queue="detect", queueing_lock="refresh_stale")
async def refresh_stale(timestamp: int) -> int:
    """Re-run detectors for Class A/B businesses whose key evidence goes stale within 7 days."""
    from sqlalchemy import text

    async with session() as s:
        rows = (await s.execute(text("""
            select distinct ls.business_id, ls.campaign_id, e.detector
            from latest_snapshot ls
            join evidence_current e on e.business_id = ls.business_id
            where ls.class in ('A','B')
              and e.detector not in ('manual','unmerge')
              and e.observed_at < now() - interval '23 days'
        """))).all()
    by: dict[tuple[str, str], list[str]] = {}
    for b, c, d in rows:
        by.setdefault((str(b), str(c)), []).append(d)
    for (b, c), dets in by.items():
        await run_detectors.defer_async(business_id=b, campaign_id=c, run_id=new_run_id(),
                                        only=sorted(set(dets)))
    return len(by)


@app.periodic(cron="*/15 * * * *")
@app.task(name="probe_connectors", queue="health", queueing_lock="probe_connectors")
async def probe_connectors(timestamp: int) -> int:
    from leadbeast.health.service import probe_all

    async with session() as s:
        return len(await probe_all(s))


@app.periodic(cron="0 6 * * 1")
@app.task(name="check_sending_domains", queue="health")
async def check_sending_domains(timestamp: int) -> int:
    from leadbeast.policy.deliverability import check_auth

    async with session() as s:
        n = 0
        for d in (await s.scalars(select(m.SendingDomain))).all():
            res = await check_auth(d.domain)
            drift = [k for k, v in res.items() if not v and getattr(d, k)]
            await s.execute(update(m.SendingDomain).where(m.SendingDomain.id == d.id).values(**res))
            if drift:
                from leadbeast.evidence.ledger import emit

                await emit(s, "ConnectorHealthAlert", None, {"domain": d.domain, "drift": drift})
            n += 1
        return n


@app.periodic(cron="5 0 * * *")
@app.task(name="advance_warmup", queue="health")
async def advance_warmup(timestamp: int) -> int:
    from sqlalchemy import func

    from leadbeast.policy.deliverability import DomainHealth, next_cap

    async with session() as s:
        for d in (await s.scalars(select(m.SendingDomain))).all():
            from datetime import timedelta

            since = now() - timedelta(days=7)
            sent = int(await s.scalar(select(func.count()).select_from(m.Message).where(
                m.Message.direction == "out", m.Message.at >= since)) or 0)
            bounced = int(await s.scalar(select(func.count()).select_from(m.Suppression).where(
                m.Suppression.reason == "bounce_hard", m.Suppression.added_at >= since)) or 0)
            complaints = int(await s.scalar(select(func.count()).select_from(m.Suppression).where(
                m.Suppression.reason == "complaint", m.Suppression.added_at >= since)) or 0)
            h = DomainHealth(d.daily_cap, bounced / sent if sent else 0.0,
                             complaints / sent if sent else 0.0)
            d.daily_cap = next_cap(h, d.day_index + 1)
            d.day_index += 1
        return 1


@app.periodic(cron="0 4 * * *")
@app.task(name="purge_raw_payloads", queue="maintenance")
async def purge_raw_payloads(timestamp: int, days: int = 365) -> int:
    """Retention: drop raw payloads of old source records; keep the normalised fields."""
    from sqlalchemy import text

    async with session() as s:
        res = await s.execute(text(
            "update source_record set payload = payload - 'raw' "
            "where fetched_at < now() - make_interval(days => :d) and payload ? 'raw'"),
            {"d": days})
        return res.rowcount or 0  # type: ignore[attr-defined]
