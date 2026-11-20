from __future__ import annotations

from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import COMING_SOON, PageCache, find_span, homepage
from leadbeast.domain.evidence import EvidenceEvent, State


class SiteHealth(BaseDetector):
    name = "site_health"
    version = "1.0.0"
    produces = ("site_down", "cert_expired", "coming_soon_page")
    window_days = 21

    async def run(self, inp: DetectorInput, fetch: PageCache) -> list[EvidenceEvent]:
        url = homepage(inp.business.website_url)
        if url is None:
            return self.unknown_all(inp, "no_website")
        https_url = url.replace("http://", "https://", 1)
        page = await fetch.get(https_url)
        out: list[EvidenceEvent] = []
        if page.error == "ssl":
            out.append(self.ev(inp, "cert_expired", State.OBSERVED_YES, True, url=https_url,
                               span="TLS certificate verification failed"))
            page = await fetch.get(url)
        elif page.ok:
            out.append(self.ev(inp, "cert_expired", State.OBSERVED_NO, False, url=https_url))
        if page.error in ("robots", "blocked", "timeout"):
            return self.fill_unknown(inp, out, page.error or "", url)
        down = not page.ok and (page.error in ("transport", "dns") or
                                (page.error or "").startswith("http_5"))
        if down:
            out.append(self.ev(inp, "site_down", State.OBSERVED_YES, True, url=url, span=page.error))
            return self.fill_unknown(inp, out, "site_down", url)
        if page.ok:
            out.append(self.ev(inp, "site_down", State.OBSERVED_NO, False, url=url))
            span = next((s for p in COMING_SOON if (s := find_span(page.html, p))), None)
            out.append(self.ev(inp, "coming_soon_page",
                               State.OBSERVED_YES if span else State.OBSERVED_NO, bool(span),
                               url=url, span=span))
        return self.fill_unknown(inp, out, page.error or "fetch_failed", url)
