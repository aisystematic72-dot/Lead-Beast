"""Detector contract tests against recorded fixtures. Never hits the network."""

import pytest
from tests.fakes import FixtureCache, alive, biz, dead

from leadbeast.detectors.listing_claimed import ListingClaimed
from leadbeast.detectors.marketing_activity import MarketingActivity
from leadbeast.detectors.marketing_stack import MarketingStack
from leadbeast.detectors.operating_style import FORBIDDEN_INPUTS, OperatingStyle
from leadbeast.detectors.registry import available
from leadbeast.detectors.site_health import SiteHealth
from leadbeast.detectors.vitality import Vitality
from leadbeast.detectors.website_presence import WebsitePresence
from leadbeast.detectors.website_quality import WebsiteQuality
from leadbeast.domain.evidence import State

GOOD = "http://acmeplumbing.com"


def by_signal(events):
    return {e.signal: e for e in events}


@pytest.mark.parametrize("cls", list(available().values()))
def test_every_detector_declares_contract(cls):
    assert cls.name and cls.version and cls.produces
    assert isinstance(cls.cost_cents, int)


async def test_presence_no_website_is_not_detected_not_false():
    ev = by_signal(await WebsitePresence(resolver=alive).run(biz(None), FixtureCache({})))
    assert ev["has_website"].state is State.NOT_DETECTED
    assert ev["domain_alive"].state is State.UNKNOWN


async def test_presence_dead_domain():
    ev = by_signal(await WebsitePresence(resolver=dead).run(biz(GOOD), FixtureCache({})))
    assert ev["has_website"].state is State.OBSERVED_YES
    assert ev["domain_alive"].state is State.OBSERVED_NO


async def test_presence_parked_has_span():
    ev = by_signal(await WebsitePresence(resolver=alive).run(
        biz("http://bestroofs.com"), FixtureCache({"http://bestroofs.com": "parked.html"})))
    assert ev["parked"].state is State.OBSERVED_YES
    assert "for sale" in ev["parked"].span.lower()


async def test_presence_blocked_is_unknown():
    ev = by_signal(await WebsitePresence(resolver=alive).run(
        biz(GOOD), FixtureCache({GOOD: "error:robots"})))
    assert ev["parked"].state is State.UNKNOWN
    assert ev["domain_alive"].state is State.UNKNOWN


async def test_quality_good_site():
    ev = by_signal(await WebsiteQuality().run(biz(GOOD), FixtureCache({GOOD: "plumber_good.html"})))
    assert ev["mobile_friendly"].state is State.OBSERVED_YES
    assert ev["contact_path"].state is State.OBSERVED_YES
    assert ev["booking_or_call_capture"].state is State.OBSERVED_YES
    assert ev["last_updated"].value == 0
    assert ev["website_quality"].value >= 0.6


async def test_quality_weak_site():
    url = "http://joeshvac.com"
    ev = by_signal(await WebsiteQuality().run(biz(url), FixtureCache({url: "weak_site.html"})))
    assert ev["mobile_friendly"].state is State.OBSERVED_NO
    assert ev["phone_only_contact"].state is State.OBSERVED_YES
    assert ev["website_quality"].value < 0.5


async def test_quality_timeout_all_unknown():
    out = await WebsiteQuality().run(biz(GOOD), FixtureCache({GOOD: "error:timeout"}))
    assert {e.signal for e in out} == set(WebsiteQuality.produces)
    assert all(e.state is State.UNKNOWN for e in out)


async def test_stack_detects_tools_with_spans():
    ev = by_signal(await MarketingStack().run(biz(GOOD), FixtureCache({GOOD: "plumber_good.html"})))
    assert "wordpress" in ev["cms"].value
    assert "calendly" in ev["booking_widget"].value
    assert ev["chat_widget"].state is State.NOT_DETECTED
    assert ev["cms"].span


async def test_activity_social_link_is_unknown_not_guess():
    ev = by_signal(await MarketingActivity().run(biz(GOOD),
                                                 FixtureCache({GOOD: "plumber_good.html"})))
    assert ev["social_active"].state is State.UNKNOWN
    assert ev["fresh_content"].state is State.OBSERVED_YES
    assert ev["email_list_signup"].state is State.OBSERVED_YES
    assert ev["ads_seen"].state is State.OBSERVED_YES


async def test_listing_from_import_and_unknown_without():
    recs = [{"_record_id": "r1", "_source": "csv", "claimed": "no", "review_count": "4",
             "photo_count": "2", "hours": "Mo-Fr 8-5", "licence_status": "active"}]
    ev = by_signal(await ListingClaimed().run(biz(None, records=recs), None))
    assert ev["listing_claimed"].state is State.OBSERVED_NO
    assert ev["review_count"].value == 4
    assert ev["licence_active"].state is State.OBSERVED_YES
    assert ev["in_map_pack"].state is State.UNKNOWN
    empty = await ListingClaimed().run(biz(None), None)
    assert all(e.state is State.UNKNOWN for e in empty)


async def test_vitality_closure_from_status():
    recs = [{"_record_id": "r1", "_source": "csv", "status": "Permanently closed"}]
    ev = by_signal(await Vitality().run(biz(None, records=recs), FixtureCache({})))
    assert ev["closure_signals"].state is State.OBSERVED_YES
    assert ev["open_status"].state is State.OBSERVED_NO


async def test_vitality_recent_copyright_counts_as_activity():
    ev = by_signal(await Vitality().run(biz(GOOD), FixtureCache({GOOD: "plumber_good.html"})))
    assert ev["recent_activity"].state is State.OBSERVED_YES
    assert ev["closure_signals"].state is State.NOT_DETECTED


async def test_site_health_cert_problem():
    ev = by_signal(await SiteHealth().run(biz(GOOD), FixtureCache({
        "https://acmeplumbing.com": "error:ssl", GOOD: "plumber_good.html"})))
    assert ev["cert_expired"].state is State.OBSERVED_YES
    assert ev["site_down"].state is State.OBSERVED_NO


def test_operating_style_never_reads_marketing_signals():
    assert not set(OperatingStyle.INPUTS) & FORBIDDEN_INPUTS
    import inspect

    import leadbeast.detectors.operating_style as mod

    src = inspect.getsource(mod.OperatingStyle.run)
    for sig in FORBIDDEN_INPUTS:
        assert f'"{sig}"' not in src, sig


async def test_operating_style_uses_operational_markers():
    ev = by_signal(await OperatingStyle().run(biz(GOOD),
                                              FixtureCache({GOOD: "plumber_good.html"})))
    assert ev["online_payments"].state is State.OBSERVED_YES
    assert ev["style"].value["style"] in ("tech", "mixed")
    assert "online_payments" in ev["style"].value["signals_used"]


async def test_detector_may_only_emit_declared_signals():
    d = WebsiteQuality()
    with pytest.raises(ValueError):
        d.ev(biz(GOOD), "has_website", State.OBSERVED_YES)
