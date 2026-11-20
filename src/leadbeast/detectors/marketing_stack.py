"""Which marketing tools a business already uses. Feeds the audit; NEVER feeds operating style."""

from __future__ import annotations

import re

from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PageCache, find_span, homepage
from leadbeast.domain.evidence import EvidenceEvent, State

STACK: dict[str, dict[str, str]] = {
    "pixels": {
        "meta_pixel": r"fbq\(|connect\.facebook\.net/[^\"']*/fbevents\.js",
        "google_analytics": r"gtag\(|google-analytics\.com/analytics\.js|googletagmanager\.com/gtag",
        "google_tag_manager": r"googletagmanager\.com/gtm\.js",
        "tiktok_pixel": r"analytics\.tiktok\.com",
    },
    "cms": {
        "wordpress": r"wp-content/|wp-includes/",
        "wix": r"static\.wixstatic\.com|wix\.com",
        "squarespace": r"squarespace\.com|static1\.squarespace",
        "shopify": r"cdn\.shopify\.com",
        "godaddy": r"img1\.wsimg\.com|godaddy\.com/websites",
        "webflow": r"webflow\.(com|io)",
        "weebly": r"weebly\.com",
    },
    "email_platform": {
        "mailchimp": r"list-manage\.com|chimpstatic\.com|mailchimp",
        "klaviyo": r"klaviyo\.com",
        "constant_contact": r"constantcontact\.com|ctctcdn",
        "convertkit": r"convertkit\.com",
    },
    "chat_widget": {
        "intercom": r"widget\.intercom\.io", "drift": r"js\.driftt\.com", "tawk": r"tawk\.to",
        "livechat": r"livechatinc\.com", "tidio": r"tidio(chat)?\.co", "podium": r"podium\.com",
    },
    "booking_widget": {
        "calendly": r"calendly\.com", "housecall_pro": r"housecallpro\.com",
        "servicetitan": r"servicetitan\.com", "jobber": r"getjobber\.com|jobber\.com",
        "acuity": r"acuityscheduling\.com", "zocdoc": r"zocdoc\.com", "booksy": r"booksy\.com",
        "square_appointments": r"squareup\.com/appointments|square\.site/book",
    },
    "crm_forms": {
        "hubspot": r"js\.hs-scripts\.com|hsforms\.(net|com)", "salesforce": r"salesforce\.com/servlet",
        "zoho": r"zoho\.com/crm|zohopublic", "gohighlevel": r"leadconnectorhq|msgsndr\.com",
    },
}


class MarketingStack(BaseDetector):
    name = "marketing_stack"
    version = "1.0.0"
    produces = tuple(STACK)
    window_days = 30

    async def run(self, inp: DetectorInput, fetch: PageCache) -> list[EvidenceEvent]:
        url = homepage(inp.business.website_url)
        if url is None:
            return self.unknown_all(inp, "no_website")
        page = await fetch.get(url)
        if not page.ok:
            return self.unknown_all(inp, page.error or "fetch_failed", url)
        out: list[EvidenceEvent] = []
        for signal, tools in STACK.items():
            found = [t for t, pat in tools.items() if re.search(pat, page.html, re.I)]
            if found:
                span = find_span(page.html, tools[found[0]], 40)
                out.append(self.ev(inp, signal, State.OBSERVED_YES, found, url=url, span=span))
            else:
                out.append(self.ev(inp, signal, State.NOT_DETECTED, url=url))
        return out
