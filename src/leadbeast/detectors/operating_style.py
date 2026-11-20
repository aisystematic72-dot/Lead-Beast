"""Operating style (tech, non_tech, mixed, unknown) from NON-marketing signals only (rule 7).

It reads how the business runs: field-service / invoicing software, online payments, own-domain
email, years in trade, licence type. It must never read has_website, website_quality,
marketing_activity or marketing_stack signals; a unit test checks INPUTS.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PageCache, find_span, homepage
from leadbeast.domain.evidence import EvidenceEvent, State

FIELD_SERVICE = r"(servicetitan|getjobber|housecallpro|fieldedge|workiz|servicem8|kickserv)"
PAYMENTS = r"(pay (your )?(bill|invoice) online|stripe\.com|squareup\.com/pay|paypal\.me|invoice\.)"

FORBIDDEN_INPUTS = frozenset({
    "has_website", "website_quality", "domain_alive", "parked", "mobile_friendly",
    "social_active", "fresh_content", "ads_seen", "email_list_signup", "pixels", "cms",
    "email_platform", "chat_widget", "booking_widget", "crm_forms",
})


class OperatingStyle(BaseDetector):
    name = "operating_style"
    version = "1.0.0"
    produces = ("style", "field_service_software", "online_payments")
    INPUTS: ClassVar[tuple[str, ...]] = ("own_domain_email", "years_in_business", "licence_type",
                                         "booking_method")
    window_days = 180

    async def run(self, inp: DetectorInput, fetch: PageCache | Any) -> list[EvidenceEvent]:
        assert not (set(self.INPUTS) & FORBIDDEN_INPUTS)
        out: list[EvidenceEvent] = []
        tech_points = 0
        non_tech_points = 0
        used: list[str] = []

        url = homepage(inp.business.website_url)
        if url and hasattr(fetch, "get"):
            page = await fetch.get(url)
            if page.ok:
                fs = find_span(page.html, FIELD_SERVICE, 30)
                pay = find_span(page.html, PAYMENTS, 30)
                out.append(self.ev(inp, "field_service_software",
                                   State.OBSERVED_YES if fs else State.NOT_DETECTED,
                                   bool(fs) or None, url=url, span=fs))
                out.append(self.ev(inp, "online_payments",
                                   State.OBSERVED_YES if pay else State.NOT_DETECTED,
                                   bool(pay) or None, url=url, span=pay))
                if fs:
                    tech_points += 2
                    used.append("field_service_software")
                if pay:
                    tech_points += 1
                    used.append("online_payments")

        f = inp.facts
        if f is not None:
            if f.is_yes("own_domain_email"):
                tech_points += 1
                used.append("own_domain_email")
            elif f.state("own_domain_email") is State.OBSERVED_NO:
                non_tech_points += 1
                used.append("own_domain_email")
            yrs = f.number("years_in_business")
            if yrs is not None and yrs >= 20:
                non_tech_points += 1
                used.append("years_in_business")
            bm = f.get("booking_method").value
            if isinstance(bm, str):
                used.append("booking_method")
                if re.search(r"phone|call", bm, re.I):
                    non_tech_points += 1
                elif re.search(r"online|app|portal", bm, re.I):
                    tech_points += 1

        if not used:
            style, state = "unknown", State.UNKNOWN
        elif tech_points >= 2 and non_tech_points == 0:
            style, state = "tech", State.INFERRED
        elif non_tech_points >= 2 and tech_points == 0:
            style, state = "non_tech", State.INFERRED
        else:
            style, state = "mixed", State.INFERRED
        conf = "medium" if len(used) >= 3 else "low"
        out.append(self.ev(inp, "style", state,
                           {"style": style, "confidence": conf, "signals_used": used},
                           source="rule"))
        return self.fill_unknown(inp, out, "no_site")
