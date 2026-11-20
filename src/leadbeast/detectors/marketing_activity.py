"""Marketing activity visible on the business's own site.

Social platforms need logins to read activity, which this system never does, so a social link
alone yields `unknown` for activity (we could not look), not a guess.
"""

from __future__ import annotations

import re
from datetime import datetime

from leadbeast.core.clock import now
from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PageCache, find_span, homepage
from leadbeast.domain.evidence import EvidenceEvent, State

SOCIAL = r"(facebook\.com|instagram\.com|linkedin\.com/company|tiktok\.com/@|x\.com|twitter\.com|youtube\.com)/[A-Za-z0-9_.\-/]+"
NEWSLETTER = r"(newsletter|subscribe|join our (mailing )?list|sign up for (our )?(emails|updates))"
ADS_TAG = r"(AW-\d{6,}|googleadservices\.com/pagead/conversion|google_conversion_id)"
DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(20\d{2})\b"
)
BLOG_LINK = r'href=["\'][^"\']*/(blog|news|articles|updates)[^"\']*["\']'


def newest_date(html: str) -> tuple[datetime | None, str | None]:
    best: tuple[datetime | None, str | None] = (None, None)
    for m in DATE_RE.finditer(html):
        try:
            d = datetime.strptime(f"{m.group(1)[:3]} {m.group(2)} {m.group(3)}", "%b %d %Y")
        except ValueError:
            continue
        if best[0] is None or d > best[0]:
            best = (d, m.group(0))
    return best


class MarketingActivity(BaseDetector):
    name = "marketing_activity"
    version = "1.0.0"
    produces = ("social_active", "fresh_content", "email_list_signup", "ads_seen")
    window_days = 30

    async def run(self, inp: DetectorInput, fetch: PageCache) -> list[EvidenceEvent]:
        url = homepage(inp.business.website_url)
        if url is None:
            # No site: nothing on-site to observe. Social/ads cannot be checked without logins.
            return [
                self.ev(inp, "social_active", State.UNKNOWN, {"reason": "no_website"}),
                self.ev(inp, "fresh_content", State.NOT_DETECTED, {"reason": "no_website"}),
                self.ev(inp, "email_list_signup", State.NOT_DETECTED, {"reason": "no_website"}),
                self.ev(inp, "ads_seen", State.UNKNOWN, {"reason": "no_website"}),
            ]
        page = await fetch.get(url)
        if not page.ok:
            return self.unknown_all(inp, page.error or "fetch_failed", url)
        html = page.html
        out: list[EvidenceEvent] = []

        social = find_span(html, SOCIAL, 10)
        out.append(self.ev(inp, "social_active",
                           State.UNKNOWN if social else State.NOT_DETECTED,
                           {"links_found": bool(social), "reason": "activity_needs_login"}
                           if social else None, url=url, span=social))

        blog = find_span(html, BLOG_LINK, 10)
        d, raw = newest_date(html)
        if d is not None:
            fresh = (now().replace(tzinfo=None) - d).days <= 90
            out.append(self.ev(inp, "fresh_content",
                               State.OBSERVED_YES if fresh else State.OBSERVED_NO, fresh,
                               url=url, span=raw))
        else:
            out.append(self.ev(inp, "fresh_content", State.NOT_DETECTED,
                               {"blog_link": bool(blog)}, url=url, span=blog))

        nl = find_span(html, NEWSLETTER, 30)
        out.append(self.ev(inp, "email_list_signup",
                           State.OBSERVED_YES if nl else State.NOT_DETECTED,
                           True if nl else None, url=url, span=nl))

        ads = find_span(html, ADS_TAG, 20)
        out.append(self.ev(inp, "ads_seen", State.OBSERVED_YES if ads else State.NOT_DETECTED,
                           True if ads else None, url=url, span=ads))
        return out
