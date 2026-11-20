"""AI detector: services, years in business, team size, booking method from site text."""

from __future__ import annotations

import re
from typing import Any

from leadbeast.ai.extract import PROMPT_VERSION, extract
from leadbeast.ai.ollama import OllamaClient
from leadbeast.core.ports import ModelClient
from leadbeast.detectors.base import BaseDetector, DetectorInput
from leadbeast.detectors.helpers import PageCache, homepage, text_of
from leadbeast.domain.evidence import EvidenceEvent, State

FIELDS = ["years_in_business", "team_size", "booking_method", "services"]
SIGNAL_FOR = {"years_in_business": "years_in_business", "team_size": "staff_count",
              "booking_method": "booking_method", "services": "services"}


def _number(v: str) -> float | None:
    m = re.search(r"\d+(\.\d+)?", v)
    return float(m.group(0)) if m else None


def _direct(field: str, value: str, span: str) -> bool:
    """The span states the fact directly (e.g. contains the number) -> observed, else inferred."""
    if field in ("years_in_business", "team_size"):
        n = _number(value)
        return n is not None and str(int(n)) in span
    return value.lower() in span.lower()


class AiSiteFacts(BaseDetector):
    name = "ai_site_facts"
    version = f"1.0.0+{PROMPT_VERSION}"
    produces = ("years_in_business", "staff_count", "booking_method", "services")
    source = "ai"
    window_days = 180

    def __init__(self, model: ModelClient | None = None):
        self.model = model

    async def run(self, inp: DetectorInput, fetch: PageCache | Any) -> list[EvidenceEvent]:
        url = homepage(inp.business.website_url)
        if url is None:
            return self.unknown_all(inp, "no_website")
        page = await fetch.get(url)
        if not page.ok:
            return self.unknown_all(inp, page.error or "fetch_failed", url)
        text = text_of(page.tree())
        model = self.model or OllamaClient()
        try:
            got = await extract(model, text, FIELDS)
        except Exception as e:  # model offline: unknown, never a guess
            return self.unknown_all(inp, f"model_error:{type(e).__name__}", url)
        if got is None:
            return self.unknown_all(inp, "model_invalid_output", url)
        out: list[EvidenceEvent] = []
        src = f"ai:{getattr(model, 'name', 'model')}:{getattr(model, 'version', '?')}"
        for e in got:
            sig = SIGNAL_FOR[e.field]
            if any(x.signal == sig for x in out):
                continue
            val: Any = _number(e.value) if sig in ("years_in_business", "staff_count") else e.value
            if val is None:
                continue
            state = State.OBSERVED_YES if _direct(e.field, e.value, e.span) else State.INFERRED
            out.append(self.ev(inp, sig, state, val, url=url, span=e.span, source=src))
        have = {x.signal for x in out}
        out += [self.ev(inp, s, State.NOT_DETECTED, url=url) for s in self.produces
                if s not in have]
        return out
