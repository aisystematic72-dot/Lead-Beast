"""Default freshness windows by signal group (spec section 6). Packs override per signal."""

from __future__ import annotations

from datetime import timedelta

GROUP_WINDOWS_DAYS: dict[str, int] = {
    "website": 30,
    "listing": 30,
    "marketing": 30,
    "registry": 180,
    "contact": 90,
    "size": 365,
}

SIGNAL_GROUP: dict[str, str] = {
    **{s: "website" for s in (
        "has_website", "domain_alive", "parked", "mobile_friendly", "load_time", "https",
        "last_updated", "contact_path", "booking_or_call_capture", "website_quality",
        "site_down", "cert_expired", "coming_soon_page", "phone_only_contact",
    )},
    **{s: "listing" for s in (
        "listing_claimed", "photo_count", "hours_present", "review_count", "review_velocity",
        "last_review_age", "map_pack_rank", "organic_rank", "in_map_pack", "open_status",
        "recent_activity", "closure_signals",
    )},
    **{s: "marketing" for s in (
        "ads_seen", "social_active", "fresh_content", "email_list_signup", "pixels", "cms",
        "email_platform", "chat_widget", "booking_widget", "crm_forms",
    )},
    **{s: "registry" for s in ("licence_active", "licence_type", "issued_date")},
    **{s: "contact" for s in ("contact_email_valid", "contact_phone_valid", "own_domain_email")},
    **{s: "size" for s in ("size_band", "size_source", "years_in_business", "team_size")},
}


def window_for(signal: str, overrides: dict[str, int] | None = None) -> timedelta:
    if overrides and signal in overrides:
        return timedelta(days=overrides[signal])
    group = SIGNAL_GROUP.get(signal)
    return timedelta(days=GROUP_WINDOWS_DAYS.get(group, 30) if group else 30)


def windows(signals: list[str], overrides: dict[str, int] | None = None) -> dict[str, timedelta]:
    return {s: window_for(s, overrides) for s in signals}
