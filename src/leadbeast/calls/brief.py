"""Caller dashboard: a one-page brief a caller reads before phoning a business.

This is NOT Business 360 (the analyst view with scores and evidence). It turns the stored
evidence into plain words for a caller:
  * who the business is (owner, years in business, contacts),
  * what we noticed: only things we actually observed, never guesses from missing data,
  * which of our services fixes each one,
  * a short call script.

Read only: nothing here writes to the database. Edit SERVICES / TRADE_WORDS to change wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now

# What the agency sells. {service} and {city} are filled per business.
SERVICES: dict[str, tuple[str, str]] = {
    "website": ("Website design",
                "A fast, mobile-friendly website that turns people searching for {service} into calls."),
    "refresh": ("Website refresh",
                "Modern look, up-to-date content and clear call buttons so visitors trust you and call."),
    "social": ("Social media management",
               "Regular Facebook and Instagram posts so people in {city} see you are open and busy."),
    "booking": ("Online booking and chat",
                "Book online or send a message any time, so after-hours customers are not lost."),
    "ads": ("Google and Facebook ads",
            "Ads shown to people in {city} who are looking for {service} right now."),
    "email": ("Email marketing",
              "Simple monthly emails that bring past customers back."),
    "seo": ("Local SEO and Google profile",
            "Show up higher on Google Maps and search results in {city}."),
}

# trade -> (what people search for, what the business calls its customers, unit of work)
TRADE_WORDS: dict[str, tuple[str, str, str]] = {
    "plumbing": ("a plumber", "customers", "jobs"),
    "hvac": ("heating and AC repair", "customers", "jobs"),
    "electrician": ("an electrician", "customers", "jobs"),
    "roofing": ("a roofer", "homeowners", "jobs"),
    "dental": ("a dentist", "patients", "appointments"),
    "cafe": ("a good cafe", "customers", "visits"),
}
DEFAULT_WORDS = ("your services", "customers", "jobs")
BOOKABLE = {"plumbing", "hvac", "electrician", "roofing", "dental"}  # trades that take appointments

FB_TEXT = {"active": "Active", "irregular": "Irregular", "inactive": "Inactive",
           "unavailable": "Page gone", "none": "No page"}

_OWNER = re.compile(r"\b(?:qualifier/owner|owner|qualifier)\s+([A-Z][A-Za-z.'\- ]+?)\s*(?:[;,(]|https?:|$)")


@dataclass
class Finding:
    key: str
    title: str
    detail: str  # what we saw, for the caller
    say: str  # how to bring it up on the call
    service: str  # key into SERVICES
    weight: int = 1  # 2 = strong reason to call


@dataclass
class Brief:
    id: UUID
    name: str
    trade: str
    city: str
    address: str
    phone: str  # formatted for display
    phone_tel: str  # E.164 for tel: links
    other_phones: list[str]
    email: str
    website: str
    facebook: str
    instagram: str
    owner: str
    years: int | None
    staff: int | None
    verified: str  # one short line on how we know it is open
    fb_status: str  # key of FB_TEXT
    fb_summary: str
    fb_last_post: str
    findings: list[Finding] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    services: list[dict[str, str]] = field(default_factory=list)
    script: dict[str, Any] = field(default_factory=dict)
    tips: list[str] = field(default_factory=list)
    level: str = "Low"  # High / Medium / Low opportunity

    @property
    def owner_first(self) -> str:
        return self.owner.split()[0] if self.owner else ""


def fmt_phone(e164: str | None) -> str:
    d = re.sub(r"\D", "", e164 or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else (e164 or "")


def owner_from(*notes: str | None) -> str:
    for n in notes:
        mt = _OWNER.search(n or "")
        if mt:
            name = mt.group(1).strip(" .")
            if name and name.lower() not in {"sole owner", "sole"}:
                return name
    return ""


_ORDER = "an ordering page"
_NOT_SITES = {"instagram.com": "an Instagram page", "facebook.com": "a Facebook page",
              "yelp.com": "a Yelp listing", "linktr.ee": "a Linktree page", "order.online": _ORDER,
              "doordash.com": _ORDER, "ubereats.com": _ORDER, "grubhub.com": _ORDER, "toasttab.com": _ORDER}


def _not_a_site(url: str) -> str:
    """'' for a real website, else what the link really is ('an Instagram page')."""
    host = re.sub(r"^https?://(www\.)?", "", url.lower()).split("/")[0]
    return next((v for k, v in _NOT_SITES.items() if host == k or host.endswith("." + k)), "")


def _ev(ev: dict[str, tuple[str, Any, str]], sig: str) -> tuple[str, Any, str]:
    return ev.get(sig, ("unknown", None, ""))


def _seen(ev: dict[str, tuple[str, Any, str]], sig: str) -> bool:
    return _ev(ev, sig)[0] in ("observed_yes", "manually_verified")


def _absent(ev: dict[str, tuple[str, Any, str]], sig: str) -> bool:
    """Looked for and not there. 'unknown' (blocked site, no data) never counts as absent."""
    return _ev(ev, sig)[0] in ("not_detected", "observed_no")


def _days_ago(d: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(d)[:10])).days
    except ValueError:
        return None


def _when(days: int | None) -> str:
    if days is None:
        return "a long time ago"
    if days < 60:
        return f"{days} days ago"
    if days < 730:
        return f"about {round(days / 30)} months ago"
    return f"over {days // 365} years ago"


def build_brief(info: dict[str, Any], ev: dict[str, tuple[str, Any, str]],
                contacts: list[dict[str, Any]], agency: str = "", today: date | None = None) -> Brief:
    """Pure: business row + current evidence (signal -> (state, value, span)) + contacts -> Brief."""
    today = today or now().date()
    trade = (info.get("trade") or "").lower()
    service, customers, jobs = TRADE_WORDS.get(trade, DEFAULT_WORDS)
    addr = info.get("address") or {}
    city = addr.get("city") or ""

    rank = {"high": 0, "medium": 1, "low": 2}
    good = sorted((c for c in contacts if c.get("eligible", True)),
                  key=lambda c: rank.get(c.get("confidence") or "", 3))
    phones = list(dict.fromkeys(c["phone"] for c in good if c.get("phone")))
    if not phones and info.get("phone"):
        phones = [info["phone"]]
    emails = list(dict.fromkeys(c["email"] for c in good if c.get("email")))

    social = _ev(ev, "social_profiles")[1]
    social = social if isinstance(social, dict) else {}
    fb_raw = _ev(ev, "facebook_activity")[1]
    fb = fb_raw if isinstance(fb_raw, dict) else {}
    fb_url = social.get("facebook") or fb.get("page") or ""
    fb_status = str(fb.get("verdict") or ("none" if not fb_url else "unknown"))

    def _num(sig: str) -> int | None:
        v = _ev(ev, sig)[1]
        return int(v) if _seen(ev, sig) and isinstance(v, int | float) else None

    years, staff = _num("years_in_business"), _num("staff_count")
    licence_note = _ev(ev, "licence_active")[2] if _seen(ev, "licence_active") else ""
    open_note = _ev(ev, "open_status")[2] if _seen(ev, "open_status") else ""
    owner = owner_from(licence_note, open_note)
    verified = ("State licence active" if licence_note else
                "Checked open" if open_note else "Not confirmed")

    # The verified website_url wins over the first-pass has_website signal (OSM often lacks it).
    url = info.get("website") or ""
    not_a_site = _not_a_site(url)
    has_site = bool(url) and not not_a_site
    site_checked = has_site and _ev(ev, "website_quality")[0] == "observed_yes"

    b = Brief(
        id=info["id"], name=info["name"], trade=trade, city=city,
        address=", ".join(x for x in (addr.get("line1"), city,
                                      " ".join(x for x in (addr.get("state"), addr.get("postcode")) if x))
                          if x),
        phone=fmt_phone(phones[0]) if phones else "", phone_tel=phones[0] if phones else "",
        other_phones=[fmt_phone(p) for p in phones[1:]], email=emails[0] if emails else "",
        website=url if has_site else "", facebook=fb_url, instagram=social.get("instagram") or "",
        owner=owner, years=years, staff=staff, verified=verified, fb_status=fb_status,
        fb_summary=str(fb.get("summary") or ""), fb_last_post=str(fb.get("last_post_date") or ""),
    )
    f = b.findings

    # ---- what we noticed (strongest first) -------------------------------------------------
    if not has_site:
        f.append(Finding("website", "No website",
                         f"Their only web link is {not_a_site}: {url}" if not_a_site else
                         "We could not find a website for this business.",
                         f"When people in {city} search for {service}, there is no website to find, "
                         "so those calls go to a competitor.", "website", 2))

    days = fb.get("days_since_last_post")
    days = int(days) if isinstance(days, int | float) else _days_ago(fb.get("last_post_date"), today)
    if fb_status == "unavailable":
        f.append(Finding("facebook", "Facebook page is gone",
                         "Their Facebook page no longer loads (removed or unpublished).",
                         "People who look you up on Facebook hit a dead page, which can make them "
                         "think you have closed.", "social", 2))
    elif fb_status == "inactive":
        last = f"Last post {b.fb_last_post} ({_when(days)})." if b.fb_last_post else "Almost no posts."
        f.append(Finding("facebook", "Facebook has gone quiet", last,
                         f"Your last Facebook post was {_when(days)}. {customers.capitalize()} who check "
                         "you out there may wonder if you are still open.", "social", 2))
    elif fb_status == "irregular":
        gap = fb.get("avg_gap_days")
        extra = f", about one post every {round(gap)} days" if isinstance(gap, int | float) else ""
        f.append(Finding("facebook", "Facebook posting is on and off",
                         f"Last post {b.fb_last_post or 'unknown'}{extra}.",
                         "Your Facebook posts come and go. Steady posting keeps you in front of "
                         f"local {customers}.", "social", 1))
    elif fb_status == "none":
        f.append(Finding("facebook", "No Facebook page",
                         "We could not find a Facebook page for this business.",
                         f"We could not find you on Facebook, where a lot of {city} {customers} look "
                         "for recommendations.", "social", 1))

    if site_checked:
        age_v = _ev(ev, "last_updated")
        age = age_v[1] if age_v[0] == "observed_yes" and isinstance(age_v[1], int | float) else None
        quality = _ev(ev, "website_quality")[1]
        quality = float(quality) if isinstance(quality, int | float) else 1.0
        not_mobile = _ev(ev, "mobile_friendly")[0] == "observed_no"
        if (age is not None and age >= 2) or quality < 0.8 or not_mobile:
            bits = []
            if age is not None and age >= 2:
                bits.append(f"the copyright year is {today.year - int(age)}")
            if not_mobile:
                bits.append("it is not mobile friendly")
            if quality < 0.8:
                bits.append(f"it passed {round(quality * 100)}% of our basic website checks")
            f.append(Finding("refresh", "Website looks out of date",
                             "On their website " + ", ".join(bits) + ".",
                             "Your website looks a bit dated. A fresh site builds trust and gets "
                             "more people to call.", "refresh", 2 if (age or 0) >= 3 else 1))

        if trade in BOOKABLE and _absent(ev, "booking_widget") and _absent(ev, "chat_widget"):
            f.append(Finding("booking", "No online booking or chat",
                             "No booking tool or chat on the website; customers can only call.",
                             f"{customers.capitalize()} can only reach you by phone. Online booking "
                             f"catches the ones who search at night or on weekends.", "booking", 1))

        if _absent(ev, "ads_seen"):
            px = _ev(ev, "pixels")
            detail = ("No ad tracking (Google Ads or Facebook pixel) on the website"
                      + ("; no visitor tracking at all." if _absent(ev, "pixels") else "."))
            if px[0] == "observed_yes" and "meta_pixel" in (px[1] or []):
                detail = "Facebook pixel installed but no sign of active ads."
            f.append(Finding("ads", "Not advertising online", detail,
                             f"We see no online ads for you, so people searching for {service} in "
                             f"{city} see your competitors first.", "ads", 1))

        if _absent(ev, "email_list_signup"):
            f.append(Finding("email", "No email list",
                             "No newsletter or email sign-up on the website.",
                             f"There is no way for past {customers} to hear from you again, and repeat "
                             "business is the cheapest business.", "email", 1))

        if _ev(ev, "fresh_content")[0] in ("not_detected", "observed_no"):
            f.append(Finding("seo", "No fresh content",
                             "No recent news or blog posts on the website.",
                             "Google favours sites with fresh content, so you may be ranking lower "
                             "than you could.", "seo", 1))
    elif has_site:
        b.tips.append("We could not check their website automatically. Take a quick look before you call.")

    f.sort(key=lambda x: -x.weight)  # stable: keeps the order above within a weight

    # ---- good things to mention (builds rapport) ------------------------------------------
    s = b.strengths
    if years:
        s.append(f"{years} years in business")
    if licence_note:
        s.append("Licence current and active")
    if fb_status == "active":
        s.append("Posts regularly on Facebook")
    bw = _ev(ev, "booking_widget")
    if bw[0] == "observed_yes":
        s.append("Already takes online bookings")
    if _seen(ev, "ads_seen"):
        s.append("Already runs some online ads")
    if b.instagram:
        s.append("Has an Instagram account")

    # ---- how we help -------------------------------------------------------------------------
    keys = list(dict.fromkeys(x.service for x in f)) or ["seo", "ads"]
    b.services = [{"key": k, "name": SERVICES[k][0],
                   "pitch": SERVICES[k][1].format(service=service, city=city or "your area")}
                  for k in keys]

    points = sum(x.weight for x in f)
    b.level = "High" if points >= 5 else "Medium" if points >= 3 else "Low"

    # ---- call script -------------------------------------------------------------------------
    hello = f"Hi, is this {b.owner_first}?" if b.owner_first else "Hi, could I speak with the owner?"
    praise = f" {years} years in {city}, that is impressive." if years and years >= 10 and city else ""
    if agency.strip().lower() in ("", "your name", "your company", "agency name"):  # unset placeholder
        agency = ""
    n = min(len(f), 3)
    hook = (f" I noticed {'a couple of things' if n > 1 else 'one thing'} that may be costing you "
            f"{customers}. Have you got two minutes?") if n else \
        f" I had a couple of ideas to help you get more {jobs}. Have you got two minutes?"
    b.script = {
        "opener": f"{hello} This is [your name] from {agency or '[agency name]'}. "
                  f"I was looking at {b.name} online.{praise}{hook}",
        "points": [x.say for x in f[:3]],
        "questions": [f"How do most new {customers} find you right now?",
                      "Who looks after your website and social media today?",
                      f"Are there slow months when you would like more {jobs}?"],
        "close": "Can I send you a free one-page report showing what we found, and book 15 minutes "
                 "to walk you through it?",
    }
    if not b.phone:
        b.tips.insert(0, "No phone number on file. Use email instead.")
    return b


# ---------------------------------------------------------------------------- queries
_BIZ_SQL = """
select b.id, b.canonical_name, b.trade, b.address, b.website_url, b.phone_e164
from business b
where b.status <> 'removed' and (cast(:b as uuid) is null or b.id = :b)
  and exists (select 1 from latest_snapshot ls where ls.business_id = b.id)
"""


async def _load(s: AsyncSession, bid: UUID | None, agency: str) -> list[Brief]:
    biz = (await s.execute(text(_BIZ_SQL), {"b": bid})).all()
    if not biz:
        return []
    ids = [r[0] for r in biz]
    ev: dict[UUID, dict[str, tuple[str, Any, str]]] = {i: {} for i in ids}
    for b_id, sig, state, value, span in (await s.execute(text(
            "select business_id, signal, state, value, coalesce(span, '') from evidence_current "
            "where business_id = any(:ids)"), {"ids": ids})).all():
        ev[b_id][sig] = (state, value, span)
    contacts: dict[UUID, list[dict[str, Any]]] = {i: [] for i in ids}
    for b_id, phone, email, conf, eligible in (await s.execute(text(
            "select business_id, phone_e164, email, confidence, eligible from contact "
            "where business_id = any(:ids)"), {"ids": ids})).all():
        contacts[b_id].append({"phone": phone, "email": email, "confidence": conf,
                               "eligible": eligible})
    today = now().date()
    return [build_brief({"id": i, "name": n, "trade": t, "address": a, "website": w, "phone": p},
                        ev[i], contacts[i], agency, today)
            for i, n, t, a, w, p in biz]


LEVEL_ORDER = {"High": 0, "Medium": 1, "Low": 2}


async def call_list(s: AsyncSession, agency: str = "") -> list[Brief]:
    """Every visible business, best opportunity first."""
    rows = await _load(s, None, agency)
    rows.sort(key=lambda b: (LEVEL_ORDER[b.level], -sum(x.weight for x in b.findings), b.name.lower()))
    return rows


async def call_brief(s: AsyncSession, bid: UUID, agency: str = "") -> Brief | None:
    rows = await _load(s, bid, agency)
    return rows[0] if rows else None
