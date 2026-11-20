from __future__ import annotations

import re

from leadbeast.core.clock import now
from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PageCache, copyright_year, find_span, homepage, years_since
from leadbeast.domain.evidence import EvidenceEvent, State

BOOKING = r"(book (now|online|an appointment)|schedule (now|online|service)|request (a )?quote|calendly|housecallpro|servicetitan|getjobber|acuityscheduling|zocdoc|booksy|square\.site/book)"
CONTACT_LINK = r'href=["\'][^"\']*(contact|get-in-touch|request|quote)[^"\']*["\']'


class WebsiteQuality(BaseDetector):
    name = "website_quality"
    version = "1.0.0"
    produces = ("mobile_friendly", "load_time", "https", "last_updated", "contact_path",
                "booking_or_call_capture", "phone_only_contact", "website_quality")
    window_days = 30

    async def run(self, inp: DetectorInput, fetch: PageCache) -> list[EvidenceEvent]:
        url = homepage(inp.business.website_url) or next(
            (homepage(r.get("website")) for r in inp.records if r.get("website")), None)
        if url is None:
            return [self.ev(inp, s, State.UNKNOWN, {"reason": "no_website"}) for s in self.produces]
        page = await fetch.get(url)
        if not page.ok or page.result is None:
            return self.unknown_all(inp, page.error or "fetch_failed", url)
        r = page.result
        html = page.html
        final = r.final_url
        out: list[EvidenceEvent] = []
        components: list[float] = []

        viewport = find_span(html, r'<meta[^>]+name=["\']viewport["\'][^>]*>', 0)
        out.append(self.ev(inp, "mobile_friendly",
                           State.OBSERVED_YES if viewport else State.OBSERVED_NO,
                           bool(viewport), url=final, span=viewport))
        components.append(1.0 if viewport else 0.0)

        out.append(self.ev(inp, "load_time", State.OBSERVED_YES, r.elapsed_ms, url=final))
        components.append(1.0 if r.elapsed_ms < 1500 else 0.5 if r.elapsed_ms < 4000 else 0.0)

        is_https = final.startswith("https://")
        out.append(self.ev(inp, "https", State.OBSERVED_YES if is_https else State.OBSERVED_NO,
                           is_https, url=final))
        components.append(1.0 if is_https else 0.0)

        year = copyright_year(html)
        if year:
            age = years_since(year, now())
            out.append(self.ev(inp, "last_updated", State.OBSERVED_YES, age, url=final,
                               span=find_span(html, str(year), 30)))
            components.append(1.0 if age <= 1 else 0.5 if age <= 3 else 0.0)
        else:
            out.append(self.ev(inp, "last_updated", State.NOT_DETECTED, url=final))

        tel = find_span(html, r'href=["\']tel:[^"\']+["\']', 10)
        form = find_span(html, r"<form[^>]*>", 0)
        contact = find_span(html, CONTACT_LINK, 10)
        has_path = bool(tel or form or contact)
        out.append(self.ev(inp, "contact_path",
                           State.OBSERVED_YES if has_path else State.NOT_DETECTED,
                           has_path or None, url=final, span=tel or form or contact))
        components.append(1.0 if has_path else 0.0)

        booking = find_span(html, BOOKING, 20)
        capture = bool(booking or tel)
        out.append(self.ev(inp, "booking_or_call_capture",
                           State.OBSERVED_YES if capture else State.NOT_DETECTED,
                           capture or None, url=final, span=booking or tel))
        phone_only = bool(tel) and not (booking or form)
        out.append(self.ev(inp, "phone_only_contact",
                           State.OBSERVED_YES if phone_only else State.OBSERVED_NO,
                           phone_only, url=final, span=tel if phone_only else None))
        components.append(1.0 if booking else 0.5 if tel else 0.0)

        words = len(re.sub(r"<[^>]+>", " ", html).split())
        components.append(1.0 if words > 300 else 0.5 if words > 100 else 0.0)

        q = round(sum(components) / len(components), 3)
        out.append(self.ev(inp, "website_quality", State.OBSERVED_YES, q, url=final))
        return out
