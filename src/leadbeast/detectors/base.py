"""Detector SDK. A detector turns raw inputs into evidence events and does nothing else.

Rules: emit not_detected when you looked and found nothing; unknown on timeout, block or parse
failure; never emit nothing. Every event carries source_url, and a span for text-derived facts.
A detector may not call another detector; shared logic lives in detectors/helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from leadbeast.core.clock import now
from leadbeast.core.ids import uuid7
from leadbeast.domain.business import Business
from leadbeast.domain.evidence import EvidenceEvent, State
from leadbeast.evidence.facts import Facts


@dataclass
class DetectorInput:
    business: Business
    records: list[dict[str, Any]] = field(default_factory=list)  # source payloads (+ _record_id)
    facts: Facts | None = None  # current folded evidence, for derived detectors


class Detector(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]
    produces: ClassVar[tuple[str, ...]]
    needs_render: ClassVar[bool]
    cost_cents: ClassVar[int]
    window_days: ClassVar[int]

    async def run(self, inp: DetectorInput, fetch: Any) -> list[EvidenceEvent]: ...


class BaseDetector:
    name: ClassVar[str] = "base"
    version: ClassVar[str] = "0"
    produces: ClassVar[tuple[str, ...]] = ()
    needs_render: ClassVar[bool] = False
    cost_cents: ClassVar[int] = 0
    window_days: ClassVar[int] = 30
    source: ClassVar[str] = "web"

    def ev(
        self, inp: DetectorInput, signal: str, state: State, value: Any = None, *,
        url: str | None = None, span: str | None = None, source: str | None = None,
    ) -> EvidenceEvent:
        if signal not in self.produces:
            raise ValueError(f"{self.name} may not emit {signal}")
        return EvidenceEvent(
            id=uuid7(), business_id=inp.business.id, signal=signal, state=state, value=value,
            source=source or self.source, source_url=url, span=(span[:500] if span else None),
            observed_at=now(), detector=self.name, detector_version=self.version,
        )

    def unknown_all(self, inp: DetectorInput, reason: str, url: str | None = None
                    ) -> list[EvidenceEvent]:
        return [self.ev(inp, s, State.UNKNOWN, {"reason": reason}, url=url) for s in self.produces]

    def fill_unknown(self, inp: DetectorInput, events: list[EvidenceEvent], reason: str,
                     url: str | None = None) -> list[EvidenceEvent]:
        """Guarantee every declared signal gets an event."""
        have = {e.signal for e in events}
        return events + [
            self.ev(inp, s, State.UNKNOWN, {"reason": reason}, url=url)
            for s in self.produces if s not in have
        ]
