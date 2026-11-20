"""Evidence events and their resolved (folded) form. Rule 4: a missing fact is never false."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class State(StrEnum):
    OBSERVED_YES = "observed_yes"
    OBSERVED_NO = "observed_no"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"
    INFERRED = "inferred"
    MANUALLY_VERIFIED = "manually_verified"
    STALE = "stale"


DECISIVE = frozenset({State.OBSERVED_YES, State.OBSERVED_NO})
OBSERVED_OR_MANUAL = frozenset({State.OBSERVED_YES, State.OBSERVED_NO, State.MANUALLY_VERIFIED})

_RANK = {
    State.MANUALLY_VERIFIED: 5,
    State.OBSERVED_YES: 4,
    State.OBSERVED_NO: 4,
    State.INFERRED: 3,
    State.NOT_DETECTED: 2,
    State.UNKNOWN: 1,
    State.CONFLICTING: 0,
    State.STALE: 0,
}


class EvidenceEvent(BaseModel):
    """One signal, one state, one source. Immutable."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    business_id: UUID
    signal: str
    state: State
    value: Any = None
    source: str
    source_url: str | None = None
    span: str | None = None
    observed_at: datetime
    detector: str
    detector_version: str
    supersedes_id: UUID | None = None

    @model_validator(mode="after")
    def _cited(self) -> EvidenceEvent:
        # Rule 12 (mirrors the DB check): an inferred fact needs a quoted span.
        if self.state is State.INFERRED and not self.span and self.source != "rule":
            raise ValueError("inferred evidence requires a quoted span")
        return self

    def rank(self) -> int:
        return _RANK[self.state]

    def value_key(self) -> str:
        """What a decisive event claims. Two different keys among fresh events = conflict."""
        if self.state is State.OBSERVED_NO:
            return "no"
        if self.value is None or isinstance(self.value, bool):
            return "yes" if self.value in (None, True) else "no"
        if isinstance(self.value, int | float):
            return "yes"  # numeric measurements differ by source noise, not by claim
        return "yes:" + json.dumps(self.value, sort_keys=True, default=str)


class Resolved(BaseModel):
    """The current picture of one signal after folding its events."""

    model_config = ConfigDict(frozen=True)

    signal: str | None = None
    state: State
    value: Any = None
    event_id: UUID | None = None
    source: str | None = None
    source_url: str | None = None
    span: str | None = None
    observed_at: datetime | None = None
    conflict_ids: tuple[UUID, ...] = Field(default_factory=tuple)

    @classmethod
    def from_event(cls, e: EvidenceEvent) -> Resolved:
        return cls(
            signal=e.signal, state=e.state, value=e.value, event_id=e.id, source=e.source,
            source_url=e.source_url, span=e.span, observed_at=e.observed_at,
        )

    @classmethod
    def stale(cls, latest: EvidenceEvent | None) -> Resolved:
        if latest is None:
            return cls(state=State.UNKNOWN)
        return cls(
            signal=latest.signal, state=State.STALE, value=latest.value, event_id=latest.id,
            source=latest.source, source_url=latest.source_url, observed_at=latest.observed_at,
        )

    @classmethod
    def conflicting(cls, events: list[EvidenceEvent]) -> Resolved:
        return cls(
            signal=events[0].signal, state=State.CONFLICTING,
            conflict_ids=tuple(e.id for e in sorted(events, key=lambda e: str(e.id))),
        )

    @classmethod
    def missing(cls, signal: str) -> Resolved:
        return cls(signal=signal, state=State.UNKNOWN)

    @property
    def is_yes(self) -> bool:
        if self.state is State.OBSERVED_YES:
            return self.value is None or self.value is not False
        if self.state in (State.MANUALLY_VERIFIED, State.INFERRED):
            return self.value is True or (self.value not in (None, False, 0, "no"))
        return False

    @property
    def is_no(self) -> bool:
        if self.state is State.OBSERVED_NO:
            return True
        if self.state in (State.OBSERVED_YES, State.MANUALLY_VERIFIED, State.INFERRED):
            return self.value is False or self.value == "no"
        return False
