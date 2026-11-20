"""Reachable: businesses worth contacting now, from the Facebook activity check.

Live list (no state of its own): a business is listed when its latest Facebook verdict says it is
not actively marketing (page gone, inactive, irregular) AND it passes the same safety checks
outreach uses (looks open = vitality gate, can afford a service = ability >= 10).
"New" = it was actively posting at the previous check and is quiet now.
Listing here is a timing hint, not permission: contact rules still apply in Approvals.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

QUIET = ("unavailable", "inactive", "irregular")  # strongest reason first
MIN_ABILITY = 10  # same bar as the Panic Alert "can pay" gate
REASON = {"unavailable": "Facebook page unavailable", "inactive": "Not actively marketing",
          "irregular": "Posting irregularly"}

_SQL = """
with fb as (
  select ec.business_id, ec.value, ec.observed_at
  from evidence_current ec where ec.signal = 'facebook_activity'
), prev as (
  select business_id, value->>'verdict' as verdict from (
    select ee.business_id, ee.value,
           row_number() over (partition by ee.business_id order by ee.observed_at desc) as rn
    from evidence_event ee where ee.signal = 'facebook_activity') x
  where rn = 2
), contacts as (
  select business_id, bool_or(email is not null) as has_email,
         bool_or(phone_e164 is not null) as has_phone
  from contact where eligible group by business_id
)
select b.id, b.canonical_name, b.trade, b.address->>'city' as city, fb.value,
       ls.class, ls.total, ls.ability, coalesce(g.passed, false) as open_ok,
       prev.verdict as prev_verdict, coalesce(c.has_email, false), coalesce(c.has_phone, false),
       cam.id as campaign_id, cam.name as campaign
from fb
join business b on b.id = fb.business_id and b.status <> 'removed'
join latest_snapshot ls on ls.business_id = b.id
join campaign cam on cam.id = ls.campaign_id
left join gate_result g on g.snapshot_id = ls.id and g.gate = 'vitality'
left join prev on prev.business_id = b.id
left join contacts c on c.business_id = b.id
where (cast(:c as uuid) is null or ls.campaign_id = :c)
"""


def _sort_key(r: dict[str, Any]) -> tuple[int, int, float]:
    days = r["days_since_last_post"]
    return (QUIET.index(r["verdict"]), 0 if r["new"] else 1,
            -(days if days is not None else 1e9))  # no posts at all counts as oldest


async def reachable(s: AsyncSession, campaign_id: UUID | None = None) -> dict[str, Any]:
    rows = (await s.execute(text(_SQL), {"c": campaign_id})).all()
    listed: list[dict[str, Any]] = []
    held_back = {"cannot_afford": 0, "looks_closed": 0}
    active = checked = 0
    for (bid, name, trade, city, v, cls, total, ability, open_ok, prev, has_email, has_phone,
         camp_id, camp) in rows:
        d = v if isinstance(v, dict) else {}
        verdict = str(d.get("verdict") or "")
        checked += 1
        if verdict == "active":
            active += 1
        if verdict not in QUIET:
            continue
        if not open_ok:
            held_back["looks_closed"] += 1
            continue
        if float(ability or 0) < MIN_ABILITY:
            held_back["cannot_afford"] += 1
            continue
        listed.append({
            "business_id": bid, "name": name, "trade": trade, "city": city,
            "verdict": verdict, "reason": REASON[verdict], "summary": d.get("summary") or "",
            "last_post_date": d.get("last_post_date"),
            "days_since_last_post": d.get("days_since_last_post"),
            "avg_gap_days": d.get("avg_gap_days"), "posts_found": d.get("posts_found"),
            "note": d.get("note") or "", "page": d.get("page") or "",
            "checked_on": d.get("checked_on") or "",
            "class": cls, "score": float(total or 0),
            "has_email": bool(has_email), "has_phone": bool(has_phone),
            "new": prev == "active", "campaign_id": camp_id, "campaign": camp,
        })
    listed.sort(key=_sort_key)
    return {
        "rows": listed, "checked": checked, "active": active,
        "held_back": held_back, "held_back_total": sum(held_back.values()),
        "new": sum(1 for r in listed if r["new"]),
        "with_contact": sum(1 for r in listed if r["has_email"] or r["has_phone"]),
        "by_reason": {k: sum(1 for r in listed if r["verdict"] == k) for k in QUIET},
    }


async def reachable_count(s: AsyncSession) -> int:
    return len((await reachable(s))["rows"])
