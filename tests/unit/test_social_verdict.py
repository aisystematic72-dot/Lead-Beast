from leadbeast.detectors.listing_claimed import social_activity
from leadbeast.domain.evidence import State
from leadbeast.sources.csv_adapter import _map_headers


def test_checklist_headers_map_to_facebook_fields():
    cols = _map_headers(["name", "facebook", "last post date", "facebook activity", "facebook summary"])
    assert cols["facebook_activity"] == "facebook activity"
    assert cols["facebook_summary"] == "facebook summary"


def test_verdict_wins_over_last_post_date_and_summary_becomes_note():
    rec = {"facebook_activity": "irregular", "facebook_summary": "Posts every 20 days.",
           "last_post_date": "2099-01-01"}
    assert social_activity(rec) == (State.OBSERVED_NO, False, "Posts every 20 days.")


def test_active_verdict_is_observed_yes():
    state, val, note = social_activity({"facebook_activity": "Active"})
    assert (state, val, note) == (State.OBSERVED_YES, True, "Facebook: active")


def test_unknown_verdict_falls_back_to_date_rule():
    assert social_activity({"facebook_activity": "pending", "last_post_date": ""}) is None
    state, *_ = social_activity({"facebook_activity": "", "last_post_date": "2001-01-01"})
    assert state is State.OBSERVED_NO
