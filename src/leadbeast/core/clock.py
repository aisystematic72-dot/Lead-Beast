"""Single source of time so tests can freeze it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

_frozen: datetime | None = None


def now() -> datetime:
    return _frozen if _frozen is not None else datetime.now(UTC)


@contextmanager
def frozen(at: datetime) -> Iterator[None]:
    global _frozen
    prev, _frozen = _frozen, at
    try:
        yield
    finally:
        _frozen = prev
