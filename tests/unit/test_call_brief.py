from datetime import date
from uuid import uuid4

from leadbeast.calls.brief import build_brief, fmt_phone, owner_from

TODAY = date(2026, 11, 20)


def _info(**kw):
    base = {"id": uuid4(), "name": "Acme Plumbing", "trade": "plumbing",
            "address": {"line1": "1 Main St", "city": "Irvine", "state": "CA", "postcode": "92614"},
            "website": "https://acme.example", "phone": "+17145550100"}
    return base | kw


def _site_ev(**kw):
    ev = {
        "has_website": ("observed_yes", True, ""), "website_quality": ("observed_yes", 0.93, ""),
        "last_updated": ("observed_yes", 0, ""), "mobile_friendly": ("observed_yes", True, ""),
        "booking_widget": ("observed_yes", ["servicetitan"], ""), "chat_widget": ("not_detected", None, ""),
        "ads_seen": ("observed_yes", True, ""), "email_list_signup": ("observed_yes", True, ""),
        "fresh_content": ("observed_yes", True, ""),
        "social_profiles": ("observed_yes", {"facebook": "https://fb.com/acme", "instagram": None}, ""),
        "facebook_activity": ("manually_verified", {"verdict": "active", "last_post_date": "2026-11-10"}, ""),
    }
    ev.update(kw)
    return ev


def test_phone_and_owner_parsing():
    assert fmt_phone("+17143312349") == "(714) 331-2349"
    assert owner_from("RTJ Companies Inc; qualifier/owner Richard E. Jones; issued 2013") == "Richard E. Jones"
    assert owner_from("Crown (sole ownership); qualifier/owner Sole owner; issued 2005") == ""
    assert owner_from("C C Electrical; qualifier/owner Chris (owner, per reviews); issued") == "Chris"
    assert owner_from("Inc, issued 1981, exp 2028, owner Robert K. Jahn https://x") == "Robert K. Jahn"
    assert owner_from(None, "names owner Daryl Ross https://x") == "Daryl Ross"


def test_healthy_business_has_no_findings_and_pitches_growth():
    b = build_brief(_info(), _site_ev(), [], "Grow Co", TODAY)
    assert b.findings == [] and b.level == "Low"
    assert [s["key"] for s in b.services] == ["seo", "ads"]
    assert "Grow Co" in b.script["opener"] and "could I speak with the owner" in b.script["opener"]


def test_no_website_and_dead_facebook_are_strong_reasons():
    ev = {"has_website": ("not_detected", None, ""),
          "facebook_activity": ("manually_verified", {"verdict": "unavailable", "page": "https://fb.com/x"}, ""),
          "licence_active": ("manually_verified", True, "CSLB #1; qualifier/owner Jane Q. Doe; issued 2010"),
          "years_in_business": ("manually_verified", 16, "")}
    b = build_brief(_info(website=None), ev, [], today=TODAY)
    assert [f.key for f in b.findings] == ["website", "facebook"]
    assert b.level == "Medium" and b.owner == "Jane Q. Doe"
    assert b.script["opener"].startswith("Hi, is this Jane?")
    assert "16 years in business" in b.strengths and "Licence current and active" in b.strengths
    assert [s["key"] for s in b.services] == ["website", "social"]


def test_unknown_signals_never_become_findings():
    """A blocked website gives 'unknown' everywhere: say nothing about it, just tip the caller."""
    ev = {"has_website": ("observed_yes", True, ""), "website_quality": ("unknown", {"reason": "blocked"}, ""),
          "booking_widget": ("unknown", {"reason": "blocked"}, ""), "ads_seen": ("unknown", None, ""),
          "facebook_activity": ("manually_verified", {"verdict": "active"}, "")}
    b = build_brief(_info(), ev, [], today=TODAY)
    assert b.findings == []
    assert any("could not check their website" in t for t in b.tips)


def test_old_site_and_quiet_facebook():
    ev = _site_ev(last_updated=("observed_yes", 3, ""), booking_widget=("not_detected", None, ""),
                  ads_seen=("not_detected", None, ""), pixels=("not_detected", None, ""),
                  facebook_activity=("manually_verified", {"verdict": "inactive", "last_post_date": "2025-01-05",
                                                           "days_since_last_post": 684}, ""))
    b = build_brief(_info(), ev, [], today=TODAY)
    keys = [f.key for f in b.findings]
    assert keys[:2] == ["facebook", "refresh"] and "booking" in keys and "ads" in keys
    assert "2023" in next(f for f in b.findings if f.key == "refresh").detail
    assert "about 23 months ago" in b.findings[0].say
    assert b.level == "High" and len(b.script["points"]) == 3


def test_verified_url_beats_stale_osm_signal_and_social_link_is_not_a_website():
    stale = {"has_website": ("not_detected", {"looked_in": "osm"}, ""),
             "website_quality": ("unknown", {"reason": "no_website"}, "")}
    b = build_brief(_info(website="https://realcafe.com/"), stale, [], today=TODAY)
    assert "website" not in [f.key for f in b.findings] and b.website == "https://realcafe.com/"
    assert any("could not check their website" in t for t in b.tips)

    ig = build_brief(_info(website="https://www.instagram.com/cafe"), stale, [], today=TODAY)
    assert ig.findings[0].key == "website" and "an Instagram page" in ig.findings[0].detail
    assert ig.website == ""


def test_best_contact_first_and_ineligible_skipped():
    contacts = [{"phone": "+15105550000", "email": None, "confidence": "low", "eligible": True},
                {"phone": "+15105551111", "email": None, "confidence": "high", "eligible": True},
                {"phone": None, "email": "junk@x.com", "confidence": "high", "eligible": False}]
    b = build_brief(_info(), _site_ev(), contacts, today=TODAY)
    assert b.phone == "(510) 555-1111" and b.other_phones == ["(510) 555-0000"] and b.email == ""
