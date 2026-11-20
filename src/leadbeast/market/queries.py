"""Read models for the UI and API. Every number on every screen comes from here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.cost.budget import Budget
from leadbeast.db import models as m
from leadbeast.evidence.ledger import events_for

CLASS_ORDER = {"A": 0, "B": 1, "review": 2, "C": 3, "D": 4}
DEFAULT_PART_MAX = {"need": 40, "ability": 30, "timing": 10, "reach": 20}


@dataclass
class OpportunityFilter:
    campaign_id: UUID | None = None
    cls: str | None = None
    gap: str | None = None
    status: str | None = None
    q: str | None = None
    limit: int = 50
    cursor: str | None = None  # "<total>|<id>" of the last row seen


async def opportunities(s: AsyncSession, f: OpportunityFilter) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: dict[str, Any] = {"limit": f.limit}
    if f.campaign_id:
        where.append("ls.campaign_id = :cid")
        params["cid"] = f.campaign_id
    if f.cls:
        where.append("ls.class = :cls")
        params["cls"] = f.cls
    if f.gap:
        where.append("ls.gap_types ? :gap")
        params["gap"] = f.gap
    if f.status:
        where.append("b.status = :status")
        params["status"] = f.status
    else:
        where.append("b.status <> 'removed'")  # removed businesses keep their history but leave the queue
    if f.q:
        where.append("b.norm_name ilike :q")
        params["q"] = f"%{f.q.lower()}%"
    if f.cursor:
        tot, _, last = f.cursor.partition("|")
        where.append("(ls.total, b.id) < (:ctot, :cid2)")
        params["ctot"], params["cid2"] = float(tot), UUID(last)
    sql = f"""
      select b.id, b.canonical_name, b.trade, b.address->>'city' as city,
             b.address->>'line1' as line1, b.status,
             ls.class, ls.total, ls.confidence, ls.gap_types, ls.coverage, ls.created_at,
             ls.campaign_id, ls.need, ls.ability, ls.timing, ls.reach,
             ps.idx as pressure, ps.direction as pressure_dir,
             {SOCIAL_SQL}
      from latest_snapshot ls
      join business b on b.id = ls.business_id
      left join lateral (select idx, direction from pressure_snapshot p
                         where p.business_id = b.id order by at desc limit 1) ps on true
      where {' and '.join(where)}
      order by ls.total desc, b.id desc
      limit :limit
    """
    rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def business_view(s: AsyncSession, business_id: UUID) -> dict[str, Any] | None:
    b = await s.get(m.Business, business_id)
    if b is None:
        return None
    events = await events_for(s, business_id)
    current = {r[0]: r for r in (await s.execute(text(
        "select signal, id, state, value, source, source_url, span, observed_at "
        "from evidence_current where business_id = :b order by signal"), {"b": business_id})).all()}
    snap = await s.scalar(select(m.ScoreSnapshot).where(m.ScoreSnapshot.business_id == business_id)
                          .order_by(m.ScoreSnapshot.created_at.desc()).limit(1))
    gates = []
    if snap:
        gates = list((await s.scalars(select(m.GateResultRow).where(
            m.GateResultRow.snapshot_id == snap.id))).all())
    audit = await s.scalar(select(m.Audit).where(m.Audit.business_id == business_id)
                           .order_by(m.Audit.created_at.desc()).limit(1))
    contacts = list((await s.scalars(select(m.Contact).where(
        m.Contact.business_id == business_id))).all())
    drafts = list((await s.scalars(select(m.OutreachDraft).where(
        m.OutreachDraft.business_id == business_id).order_by(m.OutreachDraft.created_at.desc())
    )).all())
    links = list((await s.scalars(select(m.IdentityLink).where(
        m.IdentityLink.business_id == business_id))).all())
    mystery = await s.scalar(select(m.MysteryCheck).where(
        m.MysteryCheck.business_id == business_id).order_by(m.MysteryCheck.created_at.desc()))
    reviews = list((await s.scalars(select(m.ReviewItem).where(
        m.ReviewItem.business_id == business_id, m.ReviewItem.resolved_at.is_(None)))).all())
    problems = list((await s.scalars(select(m.BusinessProblem).where(
        m.BusinessProblem.business_id == business_id)
        .order_by(m.BusinessProblem.strength.desc()))).all())
    problem_info = {p.id: p for p in (await s.scalars(select(m.Problem).where(
        m.Problem.id.in_([bp.problem_id for bp in problems])))).all()} if problems else {}
    part_max = dict(DEFAULT_PART_MAX)
    if snap:
        pk = await s.scalar(select(m.Pack).where(m.Pack.version == snap.pack_version).limit(1))
        for name, part in ((pk.spec or {}).get("parts", {}) if pk else {}).items():
            if isinstance(part, dict) and "max_points" in part:
                part_max[name] = int(part["max_points"])
    t = now()
    return {
        "business": b, "snapshot": snap, "gates": gates, "audit": audit, "contacts": contacts,
        "drafts": drafts, "links": links, "mystery": mystery, "reviews": reviews,
        "problems": problems, "problem_info": problem_info, "part_max": part_max,
        "evidence": [
            {"signal": sig, "id": r[1], "state": r[2], "value": r[3], "source": r[4],
             "source_url": r[5], "span": r[6], "observed_at": r[7],
             "age_days": (t - r[7]).days}
            for sig, r in current.items()
        ],
        "history_count": len(events),
    }


async def approval_queue(s: AsyncSession) -> list[dict[str, Any]]:
    rows = (await s.execute(
        select(m.OutreachDraft, m.Business.canonical_name)
        .join(m.Business, m.Business.id == m.OutreachDraft.business_id)
        .where(m.OutreachDraft.status.in_(("draft", "blocked")))
        .order_by(m.OutreachDraft.created_at)
    )).all()
    return [{"draft": d, "business_name": n} for d, n in rows]


async def market_summary(s: AsyncSession, campaign_id: UUID | None = None) -> dict[str, Any]:
    p = {"c": campaign_id}
    cflt = "where campaign_id = :c" if campaign_id else ""
    class_mix = (await s.execute(text(
        f"select class, sum(n) from mv_class_mix {cflt} group by class"), p)).all()
    gap_mix = (await s.execute(text(
        f"select gap, sum(n) from mv_gap_mix {cflt} group by gap"), p)).all()
    funnel = (await s.execute(text(
        "select coalesce(sum(collected),0), coalesce(sum(scored),0), coalesce(sum(qualified),0),"
        " coalesce(sum(drafted),0), coalesce(sum(sent),0), coalesce(sum(replied),0),"
        f" coalesce(sum(won),0) from mv_funnel {'where campaign_id = :c' if campaign_id else ''}"),
        p)).one()
    precision = list((await s.scalars(select(m.SignalPrecision)
                                      .order_by(m.SignalPrecision.signal))).all())
    outcomes = int(await s.scalar(select(func.count()).select_from(m.Outcome)) or 0)
    wins = int(await s.scalar(select(func.count()).select_from(m.Outcome)
                              .where(m.Outcome.result == "won")) or 0)
    style = (await s.execute(text(
        "select coalesce(value->>'style','unknown') as style, count(*) from evidence_current "
        "where signal = 'style' group by 1"))).all()
    return {
        "class_mix": {k: int(v) for k, v in class_mix},
        "gap_mix": {k: int(v) for k, v in gap_mix},
        "funnel": dict(zip(["collected", "scored", "qualified", "drafted", "sent", "replied",
                            "won"], [int(x) for x in funnel], strict=True)),
        "precision": precision,
        "threshold": get_settings().golden_precision_threshold,
        "learning": {"outcomes": outcomes, "wins": wins,
                     "challenger_ready": outcomes >= 100 and wins >= 20},
        "operating_style": {k: int(v) for k, v in style},
    }


EXPORT_COLUMNS = [
    "Name", "Trade", "Street", "City", "State", "ZIP", "Phone", "Other phones", "Email", "Website",
    "Facebook", "Instagram", "Other social", "Social activity",
    "Contact level", "Class", "Score", "Confidence", "Licence / verification", "Campaign", "Business ID",
]
SOCIAL_SQL = """
  (select ec.value from evidence_current ec where ec.business_id = b.id
     and ec.signal = 'social_profiles') as social,
  (select ec.state || '|' || coalesce(ec.span, '') from evidence_current ec where ec.business_id = b.id
     and ec.signal = 'social_active') as social_active"""


def social_activity_text(raw: str | None) -> str:
    """'observed_yes|last post ...' -> readable label for screens and the CSV."""
    if not raw:
        return "Not checked"
    state, _, note = raw.partition("|")
    label = {"observed_yes": "Active", "observed_no": "Quiet", "manually_verified": "Checked"}.get(state)
    if label is None:
        return "No social links on website" if state == "not_detected" \
            else "Not checked yet (add last post date via checklist)"
    return f"{label}: {note}" if note else label
CONTACT_LEVEL = {"high": "Verified", "medium": "Corroborated", "low": "Single source"}


async def export_rows(s: AsyncSession, campaign_id: UUID | None = None) -> list[list[Any]]:
    """One row per visible business with its best contacts, for the dashboard CSV download."""
    rows = (await s.execute(text("""
      select b.id, b.canonical_name, b.trade, b.address, b.website_url, ls.class, ls.total, ls.confidence,
             c.name as campaign,
             (select ec.span from evidence_current ec where ec.business_id = b.id
                and ec.signal = 'licence_active' and ec.state = 'manually_verified') as licence,
             """ + SOCIAL_SQL + """
      from latest_snapshot ls
      join business b on b.id = ls.business_id
      join campaign c on c.id = ls.campaign_id
      where b.status <> 'removed' and (cast(:c as uuid) is null or ls.campaign_id = :c)
      order by ls.total desc, b.canonical_name"""), {"c": campaign_id})).all()
    rank = {"high": 0, "medium": 1, "low": 2}
    out: list[list[Any]] = []
    for bid, name, trade, addr, web, cls, total, conf, camp, licence, social, sactive in rows:
        so = social if isinstance(social, dict) else {}
        contacts = sorted((await s.scalars(select(m.Contact).where(
            m.Contact.business_id == bid, m.Contact.eligible.is_(True)))).all(),
            key=lambda x: rank.get(x.confidence, 3))
        phones = list(dict.fromkeys(x.phone_e164 for x in contacts if x.phone_e164))
        emails = list(dict.fromkeys(x.email for x in contacts if x.email))
        a = addr or {}
        out.append([
            name, trade or "", a.get("line1") or "", a.get("city") or "", a.get("state") or "",
            a.get("postcode") or "", phones[0] if phones else "", "; ".join(phones[1:]),
            "; ".join(emails), web or "",
            so.get("facebook") or "", so.get("instagram") or "", "; ".join(so.get("other") or []),
            social_activity_text(sactive),
            CONTACT_LEVEL.get(contacts[0].confidence, "") if contacts else "No contact",
            cls, float(total), conf, (licence or "")[:200], camp, str(bid),
        ])
    return out


async def removed_businesses(s: AsyncSession) -> list[dict[str, Any]]:
    rows = (await s.execute(text("""
      select b.id, b.canonical_name, b.trade, b.address->>'city' as city, b.updated_at
      from business b where b.status = 'removed' order by b.updated_at desc, b.canonical_name"""))).all()
    return [{"id": r[0], "name": r[1], "trade": r[2], "city": r[3], "removed_at": r[4]} for r in rows]


@dataclass
class SourcesView:
    connectors: list[dict[str, Any]] = field(default_factory=list)
    cost: list[dict[str, Any]] = field(default_factory=list)
    global_cap: int = 0
    conflicts: list[m.ReviewItem] = field(default_factory=list)


async def sources_view(s: AsyncSession) -> SourcesView:
    v = SourcesView(global_cap=get_settings().monthly_cap_cents)
    for c in (await s.scalars(select(m.Connector).order_by(m.Connector.name))).all():
        h = await s.scalar(select(m.ConnectorHealth).where(m.ConnectorHealth.connector_id == c.id)
                           .order_by(m.ConnectorHealth.checked_at.desc()).limit(1))
        badge = "Off" if c.circuit == "open" or not c.enabled else (
            "Slow" if (h and h.status == "slow") or c.circuit == "half_open" else "OK")
        v.connectors.append({"c": c, "health": h, "badge": badge})
    v.cost = await Budget(s).month_summary()
    v.conflicts = list((await s.scalars(select(m.ReviewItem).where(
        m.ReviewItem.resolved_at.is_(None)).order_by(m.ReviewItem.created_at).limit(100))).all())
    return v
