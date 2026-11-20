from __future__ import annotations

import re
from typing import Any

from leadbeast.core.clock import now
from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PageCache, copyright_year, find_span, homepage
from leadbeast.domain.evidence import EvidenceEvent, State

CLOSED = re.compile(r"(permanently closed|closed permanently|out of business|we have closed|"
                    r"no longer in business|ceased trading)", re.I)
OPEN_STATUS = {"open", "operational", "active", "open now"}
CLOSED_STATUS = {"closed", "permanently closed", "closed_permanently", "inactive", "dissolved"}


class Vitality(BaseDetector):
    name = "vitality"
    version = "1.0.0"
    produces = ("open_status", "recent_activity", "closure_signals")
    window_days = 30

    async def run(self, inp: DetectorInput, fetch: PageCache | Any) -> list[EvidenceEvent]:
        out: list[EvidenceEvent] = []
        closure_found = False
        for rec in inp.records:
            tag = f"record:{rec.get('_record_id')}"
            src = f"listing:{rec.get('_source', 'import')}"
            status = str(rec.get("status") or "").strip().lower()
            if status in CLOSED_STATUS:
                closure_found = True
                out.append(self.ev(inp, "closure_signals", State.OBSERVED_YES, True, url=tag,
                                   source=src, span=f"status: {status}"))
                out.append(self.ev(inp, "open_status", State.OBSERVED_NO, False, url=tag,
                                   source=src))
            elif status in OPEN_STATUS:
                out.append(self.ev(inp, "open_status", State.OBSERVED_YES, True, url=tag,
                                   source=src))
            age = rec.get("last_review_age")
            try:
                if age not in (None, "") and float(age) <= 365:
                    out.append(self.ev(inp, "recent_activity", State.OBSERVED_YES, True,
                                       url=tag, source=src, span=f"last review {age} days ago"))
            except ValueError:
                pass

        url = homepage(inp.business.website_url)
        if url and hasattr(fetch, "get"):
            page = await fetch.get(url)
            if page.ok:
                m = CLOSED.search(page.html)
                if m:
                    closure_found = True
                    out.append(self.ev(inp, "closure_signals", State.OBSERVED_YES, True, url=url,
                                       span=find_span(page.html, re.escape(m.group(0)))))
                year = copyright_year(page.html)
                if year and now().year - year <= 1:
                    out.append(self.ev(inp, "recent_activity", State.OBSERVED_YES, True, url=url,
                                       source="web", span=find_span(page.html, str(year), 30)))
        if not closure_found:
            looked = bool(inp.records) or bool(url)
            out.append(self.ev(inp, "closure_signals",
                               State.NOT_DETECTED if looked else State.UNKNOWN,
                               None if looked else {"reason": "no_sources"}))
        if not any(e.signal == "recent_activity" for e in out):
            out.append(self.ev(inp, "recent_activity", State.NOT_DETECTED))
        return self.fill_unknown(inp, out, "no_status_data")
