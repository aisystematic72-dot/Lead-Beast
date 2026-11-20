"""Deliverability (section 21.1): warm-up schedule and SPF/DKIM/DMARC checks."""

from __future__ import annotations

from dataclasses import dataclass

import dns.asyncresolver
import dns.exception

from leadbeast.core.config import get_settings
from leadbeast.core.ports import Status

WARMUP_SCHEDULE = [20, 20, 50, 50, 100, 150, 250, 400, 600, 1000]  # daily cap by day-on-list
MAX_BOUNCE = 0.02
MAX_COMPLAINT = 0.001


@dataclass(frozen=True)
class DomainHealth:
    daily_cap: int
    bounce_rate_7d: float
    complaint_rate_7d: float


def next_cap(domain: DomainHealth, day_index: int) -> int:
    if domain.bounce_rate_7d > MAX_BOUNCE or domain.complaint_rate_7d > MAX_COMPLAINT:
        return domain.daily_cap  # freeze the ramp, do not raise it
    return max(domain.daily_cap,
               WARMUP_SCHEDULE[min(max(day_index, 0), len(WARMUP_SCHEDULE) - 1)])


async def _txt(name: str) -> list[str]:
    try:
        r = dns.asyncresolver.Resolver()
        r.lifetime = 5
        ans = await r.resolve(name, "TXT")
        return [b"".join(x.strings).decode("utf-8", "replace") for x in ans]
    except (dns.exception.DNSException, OSError):
        return []


async def check_auth(domain: str, dkim_selector: str = "default") -> dict[str, bool]:
    spf = any(t.lower().startswith("v=spf1") for t in await _txt(domain))
    dmarc = any(t.lower().startswith("v=dmarc1") for t in await _txt(f"_dmarc.{domain}"))
    dkim = any("p=" in t for t in await _txt(f"{dkim_selector}._domainkey.{domain}"))
    return {"spf_ok": spf, "dkim_ok": dkim, "dmarc_ok": dmarc}


class DnsAuthProbe:
    async def probe(self) -> Status:
        sender = get_settings().sender_email
        if not sender:
            return Status("off", detail="LB_SENDER_EMAIL not set")
        res = await check_auth(sender.split("@")[-1])
        bad = [k for k, v in res.items() if not v]
        return Status("ok" if not bad else "slow", detail="missing: " + ",".join(bad) if bad else "")
