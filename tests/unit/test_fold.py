from datetime import timedelta

import pytest
from tests.conftest import ev

from leadbeast.domain.evidence import State
from leadbeast.evidence.fold import fold_all, resolve

W = timedelta(days=30)


def test_empty_is_unknown_not_false():
    assert resolve([], W).state is State.UNKNOWN


def test_old_event_folds_to_stale():
    out = resolve([ev("has_website", State.OBSERVED_YES, days_ago=45)], W)
    assert out.state is State.STALE


def test_manual_beats_everything_except_newer_manual():
    a = ev("has_website", State.OBSERVED_YES, days_ago=1)
    m1 = ev("has_website", State.MANUALLY_VERIFIED, False, days_ago=10, source="op")
    m2 = ev("has_website", State.MANUALLY_VERIFIED, True, days_ago=5, source="op")
    assert resolve([a, m1], W).event_id == m1.id
    assert resolve([a, m1, m2], W).event_id == m2.id


def test_disagreeing_sources_conflict():
    a = ev("has_website", State.OBSERVED_YES, source="crawler")
    b = ev("has_website", State.OBSERVED_NO, source="directory")
    out = resolve([a, b], W)
    assert out.state is State.CONFLICTING
    assert set(out.conflict_ids) == {a.id, b.id}


def test_same_source_newer_value_wins_no_conflict():
    a = ev("open_status", State.OBSERVED_YES, True, days_ago=10, source="listing")
    b = ev("open_status", State.OBSERVED_NO, days_ago=2, source="listing")
    assert resolve([a, b], W).event_id == b.id


def test_supersede_removes_event():
    a = ev("has_website", State.OBSERVED_YES, source="crawler")
    b = ev("has_website", State.OBSERVED_NO, source="directory")
    fix = ev("has_website", State.OBSERVED_YES, source="directory", supersedes=b.id)
    assert resolve([a, b, fix], W).state is State.OBSERVED_YES


def test_rank_observed_over_inferred_over_not_detected_over_unknown():
    u = ev("cms", State.UNKNOWN, days_ago=1)
    nd = ev("cms", State.NOT_DETECTED, days_ago=2)
    inf = ev("cms", State.INFERRED, "wordpress", days_ago=3, span="Powered by WordPress")
    assert resolve([u, nd], W).event_id == nd.id
    assert resolve([u, nd, inf], W).event_id == inf.id


def test_inferred_without_span_rejected():
    with pytest.raises(ValueError):
        ev("team_size", State.INFERRED, 5)


def test_fold_all_uses_per_signal_windows():
    events = [ev("licence_active", State.OBSERVED_YES, True, days_ago=100)]
    out = fold_all(events, {"licence_active": timedelta(days=180)})
    assert out["licence_active"].state is State.OBSERVED_YES
