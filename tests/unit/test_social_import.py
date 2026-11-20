from datetime import timedelta

from leadbeast.core.clock import now
from leadbeast.detectors.listing_claimed import social_activity
from leadbeast.domain.evidence import State


def _d(days: int) -> str:
    return (now() - timedelta(days=days)).strftime("%Y-%m-%d")


def test_recent_post_is_active() -> None:
    state, val, note = social_activity({"last_post_date": _d(10), "followers": "850"})
    assert state is State.OBSERVED_YES and val is True and "850" in note


def test_old_post_is_quiet() -> None:
    state, val, _ = social_activity({"last_post_date": _d(200)})
    assert state is State.OBSERVED_NO and val is False


def test_us_date_format_parses() -> None:
    d = (now() - timedelta(days=5)).strftime("%m/%d/%Y")
    assert social_activity({"last_post_date": d})[0] is State.OBSERVED_YES


def test_none_means_no_activity_and_says_why() -> None:
    assert social_activity({"last_post_date": "none"})[2] == "no social pages"
    assert social_activity({"last_post_date": "none", "instagram": "https://instagram.com/x"})[2] \
        == "pages exist but no posts"


def test_blank_or_unreadable_says_nothing() -> None:
    assert social_activity({"last_post_date": ""}) is None
    assert social_activity({"last_post_date": "sometime last year"}) is None
