"""Turn facts into plain-language findings. Pure. Only golden-gated signals reach report text."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from leadbeast.domain.evidence import State
from leadbeast.evidence.facts import Facts

# signal -> (statement when yes, statement when no/not detected, fix, impact rank)
PHRASES: dict[str, tuple[str | None, str | None, str, int]] = {
    "has_website": (None, "We could not find a website listed for the business.",
                    "Launch a simple, fast site with a clear call button.", 1),
    "domain_alive": (None, "The website address listed does not load.",
                     "Fix or renew the domain so the listed link works.", 1),
    "parked": ("The listed domain shows a parked or for-sale page.", None,
               "Point the domain at a real site.", 1),
    "listing_claimed": (None, "The map listing does not appear to be claimed by the owner.",
                        "Claim and verify the listing, add hours and photos.", 2),
    "in_map_pack": (None, "The business does not appear in the top local map results.",
                    "Build reviews and listing completeness to enter the map pack.", 3),
    "mobile_friendly": (None, "The website is not set up for phones.",
                        "Make the site mobile friendly.", 3),
    "https": (None, "The website is not served over a secure (https) connection.",
              "Add a free TLS certificate.", 4),
    "contact_path": (None, "We could not find a clear way to contact the business on its site.",
                     "Add a visible phone button and contact form.", 2),
    "booking_or_call_capture": (None, "There is no online booking or quote request.",
                                "Add online booking or a quote form.", 3),
    "fresh_content": (None, "The website has no recent updates or posts.",
                      "Post seasonal updates or project photos monthly.", 5),
    "email_list_signup": (None, "There is no way for customers to join an email list.",
                          "Add a simple email sign-up.", 6),
    "cert_expired": ("The website's security certificate has a problem.", None,
                     "Renew the TLS certificate.", 2),
    "site_down": ("The website was down when we checked.", None,
                  "Restore hosting.", 1),
}


@dataclass(frozen=True)
class Finding:
    signal: str
    statement: str
    fix: str
    impact: int
    evidence_id: str | None
    source_url: str | None
    observed_at: str | None


def build_findings(f: Facts, live: set[str]) -> tuple[list[Finding], list[dict[str, Any]]]:
    findings: list[Finding] = []
    open_items: list[dict[str, Any]] = []
    for sig, (yes_txt, no_txt, fix, impact) in PHRASES.items():
        r = f.get(sig)
        if r.state in (State.CONFLICTING, State.STALE):
            open_items.append({"signal": sig, "state": r.state.value,
                               "note": "left out of the report; verify by hand"})
            continue
        text = yes_txt if r.is_yes else (no_txt if f.is_no(sig) else None)
        if not text:
            continue
        if sig not in live:
            open_items.append({"signal": sig, "state": r.state.value,
                               "note": "held back: below golden-set precision"})
            continue
        findings.append(Finding(
            signal=sig, statement=text, fix=fix, impact=impact,
            evidence_id=str(r.event_id) if r.event_id else None, source_url=r.source_url,
            observed_at=r.observed_at.isoformat() if r.observed_at else None,
        ))
    findings.sort(key=lambda x: (x.impact, x.signal))
    return findings, open_items


def as_dicts(fs: list[Finding]) -> list[dict[str, Any]]:
    return [asdict(x) for x in fs]
