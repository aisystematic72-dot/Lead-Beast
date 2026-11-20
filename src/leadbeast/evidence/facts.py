"""`Facts`: read-only view over folded evidence that scoring, gaps and drafts query."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from leadbeast.domain.evidence import OBSERVED_OR_MANUAL, Resolved, State

USABLE = frozenset({
    State.OBSERVED_YES, State.OBSERVED_NO, State.INFERRED, State.MANUALLY_VERIFIED,
    State.NOT_DETECTED,
})


class Facts:
    def __init__(self, resolved: Mapping[str, Resolved], meta: Mapping[str, Any] | None = None):
        self._r = dict(resolved)
        self.meta: dict[str, Any] = dict(meta or {})

    def get(self, signal: str) -> Resolved:
        return self._r.get(signal) or Resolved.missing(signal)

    def state(self, signal: str) -> State:
        return self.get(signal).state

    def signals(self) -> list[str]:
        return sorted(self._r)

    def items(self) -> list[tuple[str, Resolved]]:
        return sorted(self._r.items())

    def is_yes(self, signal: str) -> bool:
        return self.get(signal).is_yes

    def is_no(self, signal: str) -> bool:
        r = self.get(signal)
        return r.is_no or r.state is State.NOT_DETECTED

    def all_no(self, *signals: str) -> bool:
        return all(self.is_no(s) for s in signals)

    def number(self, signal: str) -> float | None:
        v = self.get(signal).value
        if isinstance(v, bool):
            return float(v)
        if isinstance(v, int | float):
            return float(v)
        if isinstance(v, dict) and isinstance(v.get("value"), int | float):
            return float(v["value"])
        return None

    def score_below(self, signal: str, threshold: float) -> bool:
        n = self.number(signal)
        return n is not None and self.get(signal).state in USABLE and n < threshold

    def low(self, signal: str, threshold: float) -> bool:
        n = self.number(signal)
        return n is not None and n < threshold

    def usable(self, signal: str) -> bool:
        return self.state(signal) in USABLE

    def observed(self, signal: str) -> bool:
        return self.state(signal) in OBSERVED_OR_MANUAL

    def conflicts(self) -> list[str]:
        return [s for s, r in self._r.items() if r.state is State.CONFLICTING]

    def stale(self) -> list[str]:
        return [s for s, r in self._r.items() if r.state is State.STALE]

    def evidence_id(self, signal: str) -> UUID | None:
        return self.get(signal).event_id

    def with_signal(self, signal: str, r: Resolved) -> Facts:
        d = dict(self._r)
        d[signal] = r
        return Facts(d, self.meta)
