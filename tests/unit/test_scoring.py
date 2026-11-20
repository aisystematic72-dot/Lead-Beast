from tests.conftest import CONTACT, OFFER_PROFILE, r, strong_lead_facts

from leadbeast.domain.evidence import State
from leadbeast.scoring.engine import score


def test_strong_lead_is_class_a(pack):
    res = score(strong_lead_facts(), pack, OFFER_PROFILE, CONTACT)
    assert res.cls == "A", (res.total, res.reason_codes, res.coverage)
    assert res.total >= 70
    assert "A" in res.gap_types  # invisible


def test_no_contact_drops_to_b(pack):
    res = score(strong_lead_facts(), pack, OFFER_PROFILE, None)
    assert res.cls == "B"
    assert "weaker_contact" in res.reason_codes


def test_closure_signal_is_d_vitality_fail(pack):
    res = score(strong_lead_facts(closure_signals=r(State.OBSERVED_YES, True)), pack,
                OFFER_PROFILE, CONTACT)
    assert res.cls == "D"
    assert "vitality_fail" in res.reason_codes


def test_unknown_vitality_goes_to_review_not_d(pack):
    f = strong_lead_facts(recent_activity=r(State.UNKNOWN), open_status=r(State.UNKNOWN))
    assert score(f, pack, OFFER_PROFILE, CONTACT).cls == "review"


def test_conflict_goes_to_review(pack):
    f = strong_lead_facts(has_website=r(State.CONFLICTING))
    res = score(f, pack, OFFER_PROFILE, CONTACT)
    assert res.cls == "review"
    assert "conflicting_evidence" in res.reason_codes


def test_stale_key_signal_goes_to_review(pack):
    f = strong_lead_facts(has_website=r(State.STALE))
    assert score(f, pack, OFFER_PROFILE, CONTACT).cls == "review"


def test_no_gap_is_d_not_marketing(pack):
    f = strong_lead_facts(
        has_website=r(State.OBSERVED_YES, True), domain_alive=r(State.OBSERVED_YES, True),
        website_quality=r(State.OBSERVED_YES, 0.9), social_active=r(State.OBSERVED_YES, True),
        in_map_pack=r(State.OBSERVED_YES, True),
    )
    res = score(f, pack, OFFER_PROFILE, CONTACT)
    assert res.cls == "D"
    assert res.reason_codes == ["not_marketing_problem"]


def test_offer_without_fit_is_d(pack):
    res = score(strong_lead_facts(), pack, {"A": 20}, CONTACT)
    assert res.cls == "D"
    assert "no_suitable_offer" in res.reason_codes


def test_reasons_cite_evidence(pack):
    res = score(strong_lead_facts(), pack, OFFER_PROFILE, CONTACT)
    assert res.reasons
    assert all(x.evidence_id for x in res.reasons if x.direction != "missing")


def test_part_maxima(pack):
    res = score(strong_lead_facts(), pack, OFFER_PROFILE, CONTACT)
    assert res.need <= 40 and res.ability <= 30 and res.timing <= 10 and res.reach <= 20
