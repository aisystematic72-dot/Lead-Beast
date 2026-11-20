"""Property tests required by spec sections 6 and 16."""

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.conftest import ev

from leadbeast.domain.evidence import State
from leadbeast.evidence.fold import resolve

W = timedelta(days=30)
STATES = [s for s in State if s not in (State.CONFLICTING, State.STALE, State.INFERRED)]

event_st = st.builds(
    lambda state, days, src: ev("sig", state, days_ago=days, source=src),
    st.sampled_from(STATES),
    st.floats(min_value=0, max_value=60),
    st.sampled_from(["a", "b", "c"]),
)


@settings(max_examples=300)
@given(st.lists(event_st, min_size=1, max_size=8), st.sampled_from(STATES))
def test_adding_event_older_than_window_never_changes_result(events, state):
    before = resolve(events, W)
    old = ev("sig", state if state is not State.MANUALLY_VERIFIED else State.OBSERVED_YES,
             days_ago=400, source="z")
    after = resolve([*events, old], W)
    if before.state is State.STALE or before.state is State.UNKNOWN and not events:
        return  # stale result legitimately points at the latest event
    assert after == before


@settings(max_examples=300)
@given(st.lists(event_st, min_size=0, max_size=8))
def test_conflicting_fresh_event_from_new_source_yields_conflicting(events):
    events = [e for e in events if e.state is not State.MANUALLY_VERIFIED]
    a = ev("sig", State.OBSERVED_YES, days_ago=1, source="x")
    b = ev("sig", State.OBSERVED_NO, days_ago=1, source="y")
    assert resolve([*events, a, b], W).state is State.CONFLICTING


@settings(max_examples=200)
@given(st.lists(event_st, min_size=1, max_size=8))
def test_deterministic_and_order_independent(events):
    assert resolve(events, W) == resolve(list(reversed(events)), W)
