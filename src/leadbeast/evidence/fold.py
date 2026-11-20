"""Fold a business's evidence events into the current picture. Pure; property-tested.

Rules (spec section 6):
1. Append only; corrections supersede.
4. manually_verified beats everything except a newer manually_verified.
5. Fresh events from different sources that disagree -> conflicting. Never pick silently.
6. Older than the freshness window -> stale (missing coverage, not a value).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import timedelta

from leadbeast.core.clock import now
from leadbeast.domain.evidence import DECISIVE, EvidenceEvent, Resolved, State


def _live(events: list[EvidenceEvent]) -> list[EvidenceEvent]:
    superseded = {e.supersedes_id for e in events if e.supersedes_id is not None}
    return [e for e in events if e.id not in superseded]


def resolve(events: list[EvidenceEvent], window: timedelta) -> Resolved:
    live = _live(events)
    if not live:
        return Resolved(state=State.UNKNOWN)

    manual = [e for e in live if e.state is State.MANUALLY_VERIFIED]
    if manual:
        return Resolved.from_event(max(manual, key=lambda e: (e.observed_at, str(e.id))))

    t = now()
    fresh = [e for e in live if t - e.observed_at <= window]
    if not fresh:
        return Resolved.stale(latest=max(live, key=lambda e: (e.observed_at, str(e.id))))

    # A source's newest decisive observation replaces its older ones (values change over time);
    # disagreement *between* sources is what counts as a conflict.
    latest_by_source: dict[str, EvidenceEvent] = {}
    for e in fresh:
        if e.state in DECISIVE:
            cur = latest_by_source.get(e.source)
            if cur is None or (e.observed_at, str(e.id)) > (cur.observed_at, str(cur.id)):
                latest_by_source[e.source] = e
    decisive = list(latest_by_source.values())
    if len({e.value_key() for e in decisive}) > 1:
        return Resolved.conflicting(decisive)

    pick = max(decisive or fresh, key=lambda e: (e.rank(), e.observed_at, str(e.id)))
    return Resolved.from_event(pick)


def fold_all(
    events: Iterable[EvidenceEvent],
    windows: Mapping[str, timedelta],
    default_window: timedelta = timedelta(days=30),
) -> dict[str, Resolved]:
    by_signal: dict[str, list[EvidenceEvent]] = defaultdict(list)
    for e in events:
        by_signal[e.signal].append(e)
    return {
        sig: resolve(evs, windows.get(sig, default_window)) for sig, evs in by_signal.items()
    }
