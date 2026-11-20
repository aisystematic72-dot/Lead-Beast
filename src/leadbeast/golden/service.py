"""Golden set: hand labels, precision per signal, and the release gate (rule 8).

A signal below the threshold may score but must never appear in a draft or report.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.db import models as m
from leadbeast.domain.evidence import State
from leadbeast.evidence.fold import resolve
from leadbeast.evidence.freshness import window_for
from leadbeast.evidence.ledger import events_for

TRUE = {"1", "true", "yes", "y"}


@dataclass(frozen=True)
class PrecisionRow:
    signal: str
    detector_version: str
    precision: float | None
    recall: float | None
    n_labelled: int
    n_positive_calls: int
    live: bool


def compute(pairs: list[tuple[bool | None, bool]], threshold: float) -> tuple[
        float | None, float | None, int]:
    """pairs = (predicted, truth); predicted None = no decisive call. Pure."""
    tp = sum(1 for p, t in pairs if p is True and t)
    fp = sum(1 for p, t in pairs if p is True and not t)
    fn = sum(1 for p, t in pairs if p is not True and t)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return precision, recall, tp + fp


async def import_labels(s: AsyncSession, path: Path, user: str) -> int:
    """CSV columns: business_id, signal, truth."""
    n = 0
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            stmt = insert(m.GoldenLabel).values(
                business_id=UUID(row["business_id"]), signal=row["signal"].strip(),
                truth=row["truth"].strip().lower() in TRUE, labelled_by=user, labelled_at=now(),
            ).on_conflict_do_update(index_elements=["business_id", "signal"],
                                    set_={"truth": row["truth"].strip().lower() in TRUE,
                                          "labelled_by": user, "labelled_at": now()})
            await s.execute(stmt)
            n += 1
    return n


async def report(s: AsyncSession) -> list[PrecisionRow]:
    th = get_settings().golden_precision_threshold
    labels = (await s.scalars(select(m.GoldenLabel))).all()
    by_signal: dict[str, list[tuple[UUID, bool]]] = {}
    for lab in labels:
        by_signal.setdefault(lab.signal, []).append((lab.business_id, lab.truth))
    out: list[PrecisionRow] = []
    for signal, items in sorted(by_signal.items()):
        pairs: list[tuple[bool | None, bool]] = []
        versions: set[str] = set()
        for bid, truth in items:
            evs = [e for e in await events_for(s, bid)
                   if e.signal == signal and e.state is not State.MANUALLY_VERIFIED]
            r = resolve(evs, window_for(signal))
            versions |= {e.detector_version for e in evs}
            pred: bool | None = True if r.is_yes else (False if r.is_no or
                                                      r.state is State.NOT_DETECTED else None)
            pairs.append((pred, truth))
        p, rec, calls = compute(pairs, th)
        version = max(versions) if versions else "n/a"
        live = p is not None and p >= th
        row = PrecisionRow(signal, version, p, rec, len(items), calls, live)
        out.append(row)
        stmt = insert(m.SignalPrecision).values(
            signal=signal, detector_version=version, precision=p, recall=rec,
            n_labelled=len(items), n_positive_calls=calls, live=live, computed_at=now(),
        ).on_conflict_do_update(index_elements=["signal", "detector_version"], set_={
            "precision": p, "recall": rec, "n_labelled": len(items), "n_positive_calls": calls,
            "live": live, "computed_at": now()})
        await s.execute(stmt)
    return out


async def precision_map(s: AsyncSession) -> dict[str, float]:
    """Latest precision per signal (any detector version, newest computation)."""
    rows = (await s.scalars(select(m.SignalPrecision).order_by(m.SignalPrecision.computed_at))).all()
    out: dict[str, float] = {}
    for r in rows:
        if r.precision is not None:
            out[r.signal] = r.precision
    return out


async def live_signals(s: AsyncSession) -> set[str]:
    th = get_settings().golden_precision_threshold
    return {k for k, v in (await precision_map(s)).items() if v >= th}
