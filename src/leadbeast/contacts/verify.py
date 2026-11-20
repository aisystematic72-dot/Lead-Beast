"""Contact verification: syntax, MX, role address, webmail, phone type. No SMTP probing by default."""

from __future__ import annotations

import re

import dns.asyncresolver
import dns.exception

from leadbeast.core.clock import now
from leadbeast.core.ports import ContactCheckResult
from leadbeast.domain.business import Contact
from leadbeast.identity import normalise as N

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
JUNK = re.compile(r"(example\.com|\.example$|sentry\.io|wixpress\.com|\.png$|\.jpg$|\.gif$|\.webp$|"
                  r"^u00|noreply|no-reply|donotreply|"
                  # website-template placeholders and payment-processor addresses
                  r"@domain\.com$|@yourdomain\.|@email\.com$|^(user|name|email|yourname|your\.?email)@|"
                  r"acceptance\.visa\.com$|@[0-9a-f]{24,}\.)", re.I)


def syntax_ok(email: str) -> bool:
    return bool(EMAIL_RE.match(email)) and not JUNK.search(email)


async def has_mx(domain: str) -> bool | None:
    try:
        r = dns.asyncresolver.Resolver()
        r.lifetime = 5
        await r.resolve(domain, "MX")
        return True
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False
    except (dns.exception.DNSException, OSError):
        return None


async def verify(c: Contact) -> list[ContactCheckResult]:
    out: list[ContactCheckResult] = []
    t = now()
    if c.email:
        ok = syntax_ok(c.email)
        out.append(ContactCheckResult("syntax", "pass" if ok else "fail", t))
        if ok:
            mx = await has_mx(c.email.split("@", 1)[1])
            out.append(ContactCheckResult("mx", {True: "pass", False: "fail", None: "unknown"}[mx], t))
            out.append(ContactCheckResult("role", "role" if N.is_role_address(c.email) else "personal", t))
            out.append(ContactCheckResult("webmail", "webmail" if N.is_webmail(c.email) else "own_domain", t))
    if c.phone_e164:
        out.append(ContactCheckResult("phone_type", N.phone_type(c.phone_e164), t))
    return out


def eligible(checks: list[ContactCheckResult]) -> bool:
    """Eligible for outreach: email syntax pass and MX not failed (or a valid phone)."""
    by = {c.kind: c.result for c in checks}
    if by.get("syntax") == "pass" and by.get("mx") in ("pass", "unknown"):
        return True
    return by.get("phone_type") not in (None, "invalid") and "syntax" not in by


def confidence(c: Contact, checks: list[ContactCheckResult]) -> str:
    by = {x.kind: x.result for x in checks}
    if by.get("mx") == "pass" and by.get("webmail") == "own_domain" and c.name:
        return "high"
    if by.get("mx") == "pass":
        return "medium" if by.get("webmail") == "own_domain" else "low"
    return "low"
