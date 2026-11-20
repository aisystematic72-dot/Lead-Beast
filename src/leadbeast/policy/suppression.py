"""Do-not-contact list, stored as salted hashes and kept indefinitely."""

from __future__ import annotations

import hashlib
from uuid import UUID

import tldextract
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.db import models as m

KINDS = ("email", "phone", "domain", "business")


def normalise(kind: str, value: str) -> str:
    v = value.strip().lower()
    if kind == "domain":
        ext = tldextract.extract(v)
        return ".".join(p for p in (ext.domain, ext.suffix) if p)
    if kind == "phone":
        return "".join(ch for ch in v if ch.isdigit() or ch == "+")
    return v


def hash_value(kind: str, value: str) -> str:
    salt = get_settings().secret_key.get_secret_value()
    return hashlib.sha256(f"{salt}:{kind}:{normalise(kind, value)}".encode()).hexdigest()


async def add(s: AsyncSession, kind: str, value: str, reason: str) -> None:
    if kind not in KINDS:
        raise ValueError(kind)
    stmt = insert(m.Suppression).values(
        kind=kind, value_hash=hash_value(kind, value), reason=reason, added_at=now(),
    ).on_conflict_do_nothing(index_elements=["kind", "value_hash"])
    await s.execute(stmt)


async def matches(
    s: AsyncSession, *, email: str | None = None, phone: str | None = None,
    domain: str | None = None, business_id: UUID | None = None,
) -> tuple[str, ...]:
    pairs = [(k, v) for k, v in (
        ("email", email), ("phone", phone), ("domain", domain),
        ("business", str(business_id) if business_id else None),
    ) if v]
    if email and "@" in email and not domain:
        pairs.append(("domain", email.split("@", 1)[1]))
    if not pairs:
        return ()
    hashes = {hash_value(k, v): k for k, v in pairs}
    rows = (await s.scalars(
        select(m.Suppression.kind).where(m.Suppression.value_hash.in_(list(hashes)))
    )).all()
    return tuple(sorted(set(rows)))
