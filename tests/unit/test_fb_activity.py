from datetime import date, timedelta

import pytest

from leadbeast.social.fb_activity import Rules, Verdict, assess, fallback_summary
from leadbeast.social.fb_service import parse_dates

TODAY = date(2026, 11, 16)


def days_ago(*ns: int) -> list[date]:
    return [TODAY - timedelta(days=n) for n in ns]


def test_active_page_uses_newest_five_posts_only():
    a = assess(days_ago(30, 2, 9, 16, 23, 400, 900), TODAY)  # unsorted, 7 posts
    assert a.verdict is Verdict.ACTIVE
    assert a.posts_found == 7
    assert a.sample == tuple(days_ago(2, 9, 16, 23, 30))
    assert a.gaps_days == (7, 7, 7, 7)
    assert a.avg_gap_days == 7.0
    assert a.days_since_last == 2


def test_long_gaps_are_irregular():
    a = assess(days_ago(10, 40, 70, 100, 130), TODAY)
    assert a.verdict is Verdict.IRREGULAR
    assert a.avg_gap_days == 30.0


def test_burst_then_silence_is_irregular_not_active():
    a = assess(days_ago(44, 45, 46, 48, 56), TODAY)
    assert a.avg_gap_days == 3.0
    assert a.verdict is Verdict.IRREGULAR
    assert "44 days" in a.reason


def test_five_old_posts_are_not_active_marketing():
    a = assess(days_ago(200, 201, 202, 203, 204), TODAY)
    assert a.verdict is Verdict.INACTIVE
    assert "200 days ago" in a.reason
    assert not a.too_few_posts


@pytest.mark.parametrize("n", [0, 1, 4])
def test_fewer_than_five_posts_is_inactive(n):
    a = assess(days_ago(*range(1, n + 1)), TODAY)
    assert a.verdict is Verdict.INACTIVE and a.too_few_posts
    assert "not actively marketing" in fallback_summary("Acme", a)


def test_future_dates_are_ignored_and_flagged():
    a = assess(days_ago(-3, 1, 2, 3, 4, 5), TODAY)
    assert a.sample[0] == TODAY - timedelta(days=1)
    assert a.flags


def test_statuses_short_circuit():
    assert assess([], TODAY, page_status="pending").verdict is Verdict.PENDING
    assert assess(days_ago(1, 2), TODAY, page_status="unavailable").verdict is Verdict.UNAVAILABLE


def test_rules_are_configurable():
    a = assess(days_ago(1, 4, 7), TODAY, Rules(sample_posts=3, active_max_gap_days=3))
    assert a.verdict is Verdict.ACTIVE and a.avg_gap_days == 3.0


def test_parse_dates_accepts_common_separators_and_rejects_junk():
    assert parse_dates("2026-11-10; 2026-11-01,2026-10-20 | 2026-10-01T09:00") == [
        date(2026, 11, 10), date(2026, 11, 1), date(2026, 10, 20), date(2026, 10, 1)]
    with pytest.raises(ValueError):
        parse_dates("11/10/2026")
