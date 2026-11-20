"""Free contact provider: source records plus the business's own homepage and contact page."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from leadbeast.contacts import verify as V
from leadbeast.core.ports import ContactCheckResult, Status
from leadbeast.detectors.helpers import PageCache, homepage
from leadbeast.domain.business import Business, Contact
from leadbeast.identity import normalise as N

MAILTO = re.compile(r'mailto:([^"\'?\s>]+)', re.I)
EMAIL_TEXT = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
TEL = re.compile(r'tel:([+\d\-\s().]{7,})', re.I)
CONTACT_HREF = re.compile(r'href=["\']([^"\']*contact[^"\']*)["\']', re.I)
OWNER = re.compile(r"\b(owner|founder|president|proprietor)\b[^.<]{0,40}?\b([A-Z][a-z]+ [A-Z][a-z]+)")


class SiteContactProvider:
    name = "site_contacts"

    def __init__(self, cache: PageCache, records: list[dict[str, Any]] | None = None):
        self.cache = cache
        self.records = records or []

    async def probe(self) -> Status:
        return Status("ok")

    async def find(self, biz: Business) -> list[Contact]:
        emails: dict[str, str | None] = {}
        phones: dict[str, str | None] = {}
        owner: str | None = None
        for r in self.records:
            if r.get("email"):
                emails.setdefault(str(r["email"]).strip().lower(), f"record:{r.get('_record_id')}")
            if r.get("phone") and (p := N.phone(str(r["phone"]))):
                phones.setdefault(p, f"record:{r.get('_record_id')}")
        if biz.phone_e164:
            phones.setdefault(biz.phone_e164, None)

        url = homepage(biz.website_url)
        if url:
            pages = [url]
            home = await self.cache.get(url)
            if home.ok:
                m = CONTACT_HREF.search(home.html)
                if m:
                    pages.append(urljoin(url, m.group(1)))
            for pu in pages:
                pg = await self.cache.get(pu)
                if not pg.ok:
                    continue
                for e in MAILTO.findall(pg.html) + EMAIL_TEXT.findall(pg.html):
                    e = e.strip().lower()
                    if V.syntax_ok(e):
                        emails.setdefault(e, pu)
                for t in TEL.findall(pg.html):
                    if p := N.phone(t):
                        phones.setdefault(p, pu)
                if owner is None and (om := OWNER.search(pg.html)):
                    owner = om.group(2)

        out: list[Contact] = []
        site_domain = biz.domain
        # prefer own-domain addresses, then role addresses, then webmail
        def rank(e: str) -> tuple[int, int]:
            own = 0 if site_domain and N.domain(e) == site_domain else 1
            return (own, 1 if N.is_webmail(e) else 0)

        for e in sorted(emails, key=rank)[:3]:
            out.append(Contact(
                business_id=biz.id, email=e, name=owner if not N.is_role_address(e) else None,
                role="owner" if owner and not N.is_role_address(e) else "general",
                channel_pref="email", source_url=emails[e], is_webmail=N.is_webmail(e),
                is_role_address=N.is_role_address(e),
            ))
        for p in list(phones)[:2]:
            out.append(Contact(business_id=biz.id, phone_e164=p, channel_pref="phone",
                               role="main_line", source_url=phones[p]))
        return out

    async def verify(self, contact: Contact) -> list[ContactCheckResult]:
        return await V.verify(contact)
