"""Normalise names, phones, domains and addresses. Pure."""

from __future__ import annotations

import re
from functools import lru_cache

import phonenumbers
import tldextract

from leadbeast.domain.business import Address

LEGAL_SUFFIXES = {
    "llc", "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "pllc", "pc", "lp", "llp", "dba", "the",
}
ABBREVIATIONS = {
    "&": "and", "svc": "service", "svcs": "services", "srv": "service", "bros": "brothers",
    "intl": "international", "mgmt": "management", "htg": "heating", "plmbg": "plumbing",
    "elec": "electric", "st": "street", "ave": "avenue", "rd": "road", "dr": "drive",
}
WEBMAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com",
    "live.com", "msn.com", "me.com", "protonmail.com", "ymail.com", "comcast.net",
}
ROLE_LOCALPARTS = {
    "info", "contact", "office", "admin", "sales", "support", "hello", "service", "help",
    "billing", "team", "mail", "enquiries", "inquiries", "booking", "bookings",
}

_extract = tldextract.TLDExtract(suffix_list_urls=())  # offline: bundled snapshot


def name(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [ABBREVIATIONS.get(t, t) for t in s.split()]
    tokens = [t for t in tokens if t not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def first_token(n: str) -> str:
    return n.split()[0] if n else ""


def phone(raw: str | None, region: str = "US") -> str | None:
    if not raw:
        return None
    try:
        p = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(p):
        return None
    return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)


def phone_type(e164: str) -> str:
    try:
        t = phonenumbers.number_type(phonenumbers.parse(e164))
    except phonenumbers.NumberParseException:
        return "invalid"
    return {
        phonenumbers.PhoneNumberType.MOBILE: "mobile",
        phonenumbers.PhoneNumberType.FIXED_LINE: "landline",
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline_or_mobile",
        phonenumbers.PhoneNumberType.VOIP: "voip",
        phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
    }.get(t, "other")


@lru_cache(maxsize=4096)
def domain(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    if "@" in s:
        s = s.split("@", 1)[1]
    ext = _extract(s)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def website_url(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if not re.match(r"^https?://", s, re.I):
        s = "http://" + s
    return s if domain(s) else None


def address(raw: str | dict[str, str] | None) -> Address:
    if raw is None:
        return Address()
    if isinstance(raw, dict):
        return Address(
            line1=raw.get("line1") or raw.get("street"), city=raw.get("city"),
            state=raw.get("state"), postcode=(raw.get("postcode") or raw.get("zip") or None),
        )
    try:
        import usaddress

        tagged, _ = usaddress.tag(raw)
        line1 = " ".join(
            v for k, v in tagged.items()
            if k.startswith(("AddressNumber", "StreetName", "StreetNamePost", "StreetNamePre"))
        )
        return Address(
            line1=line1 or None, city=tagged.get("PlaceName"), state=tagged.get("StateName"),
            postcode=(tagged.get("ZipCode") or "")[:5] or None,
        )
    except Exception:
        m = re.search(r"\b(\d{5})(?:-\d{4})?\b", raw)
        return Address(line1=raw, postcode=m.group(1) if m else None)


def address_key(a: Address) -> str:
    return name(f"{a.line1 or ''} {a.postcode or ''}")


def is_webmail(email: str) -> bool:
    return (domain(email) or "") in WEBMAIL


def is_role_address(email: str) -> bool:
    return email.split("@", 1)[0].lower() in ROLE_LOCALPARTS
