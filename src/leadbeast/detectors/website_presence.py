from __future__ import annotations

import re
from typing import Any

import dns.asyncresolver
import dns.exception

from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PARKED_PATTERNS, PageCache, find_span, homepage
from leadbeast.domain.evidence import EvidenceEvent, State
from leadbeast.identity import normalise as N


async def domain_resolves(domain: str) -> bool | None:
    try:
        r = dns.asyncresolver.Resolver()
        r.lifetime = 5
        await r.resolve(domain, "A")
        return True
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False
    except (dns.exception.DNSException, OSError):
        return None


class WebsitePresence(BaseDetector):
    name = "website_presence"
    version = "1.0.0"
    produces = ("has_website", "domain_alive", "parked")
    window_days = 30

    def __init__(self, resolver: Any = None):
        self._resolve = resolver or domain_resolves

    async def run(self, inp: DetectorInput, fetch: PageCache) -> list[EvidenceEvent]:
        urls = [inp.business.website_url] + [r.get("website") for r in inp.records]
        url = next((homepage(u) for u in urls if u and N.domain(u)), None)
        if url is None:
            src = ",".join(sorted({r.get("_source", "?") for r in inp.records})) or "none"
            return [
                self.ev(inp, "has_website", State.NOT_DETECTED, {"looked_in": src},
                        source="sources"),
                self.ev(inp, "domain_alive", State.UNKNOWN, {"reason": "no_domain"}),
                self.ev(inp, "parked", State.UNKNOWN, {"reason": "no_domain"}),
            ]
        out = [self.ev(inp, "has_website", State.OBSERVED_YES, True, url=url, source="listing")]
        domain = N.domain(url) or ""
        resolves = await self._resolve(domain)
        if resolves is False:
            out.append(self.ev(inp, "domain_alive", State.OBSERVED_NO, False, url=url,
                               source="dns"))
            out.append(self.ev(inp, "parked", State.UNKNOWN, {"reason": "dns_fail"}, url=url))
            return out
        page = await fetch.get(url)
        if page.error in ("robots", "blocked", "timeout"):
            return self.fill_unknown(inp, out, page.error or "", url)
        if not page.ok:
            if resolves and page.error in ("dns", "transport", "ssl") or (
                page.error or "").startswith("http_5"):
                out.append(self.ev(inp, "domain_alive", State.OBSERVED_NO, False, url=url,
                                   span=page.error))
            return self.fill_unknown(inp, out, page.error or "fetch_failed", url)
        final = page.result.final_url if page.result else url
        out.append(self.ev(inp, "domain_alive", State.OBSERVED_YES, True, url=final))
        span = next((s for p in PARKED_PATTERNS if (s := find_span(page.html, p))), None)
        if span:
            out.append(self.ev(inp, "parked", State.OBSERVED_YES, True, url=final, span=span))
        else:
            tiny = len(re.sub(r"<[^>]+>", "", page.html).strip()) < 200
            out.append(self.ev(inp, "parked", State.INFERRED if tiny else State.OBSERVED_NO,
                               tiny, url=final,
                               span=page.html[:200] if tiny else None))
        return out
