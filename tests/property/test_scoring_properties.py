"""Property tests that must exist (spec section 10)."""

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.conftest import CONTACT, NOW, OFFER_PROFILE, r, strong_lead_facts

from leadbeast.domain.evidence import State
from leadbeast.scoring.engine import score
from leadbeast.scoring.problems import Trigger, pressure_index

USABLE_STATES = [State.OBSERVED_YES, State.OBSERVED_NO, State.NOT_DETECTED,
                 State.MANUALLY_VERIFIED]


def _random_facts(data, pack):
    sigs = pack.all_signals()
    chosen = data.draw(st.lists(st.sampled_from(sigs), unique=True, max_size=len(sigs)))
    over = {}
    for s in chosen:
        state = data.draw(st.sampled_from(USABLE_STATES + [State.UNKNOWN]))
        value = data.draw(st.one_of(st.booleans(), st.integers(0, 400), st.none()))
        over[s] = r(state, value)
    return strong_lead_facts(**over)


@settings(max_examples=200, deadline=None)
@given(st.data())
def test_adding_unknown_or_irrelevant_signal_never_raises_score(pack, data):
    f = _random_facts(data, pack)
    base = score(f, pack, OFFER_PROFILE, CONTACT).total
    sig = data.draw(st.sampled_from([*pack.all_signals(), "irrelevant_signal_xyz"]))
    if f.usable(sig):
        return
    f2 = f.with_signal(sig, r(State.UNKNOWN))
    assert score(f2, pack, OFFER_PROFILE, CONTACT).total <= base


@settings(max_examples=200, deadline=None)
@given(st.data())
def test_lowering_coverage_never_raises_score(pack, data):
    f = _random_facts(data, pack)
    # coverage is about scoring-part signals; closure evidence is a gate input, not coverage
    part_signals = {s.signal for p in pack.parts.values() for s in p.signals}
    usable = [s for s in sorted(part_signals) if f.usable(s)]
    if not usable:
        return
    sig = data.draw(st.sampled_from(usable))
    base = score(f, pack, OFFER_PROFILE, CONTACT).total
    lowered = f.with_signal(sig, r(State.UNKNOWN))
    assert score(lowered, pack, OFFER_PROFILE, CONTACT).total <= base


@settings(max_examples=200, deadline=None)
@given(st.data())
def test_failed_gate_never_produces_class_a(pack, data):
    f = _random_facts(data, pack)
    res = score(f, pack, OFFER_PROFILE, CONTACT)
    if any(not g.passed for g in res.gates):
        assert res.cls != "A"


@settings(max_examples=200, deadline=None)
@given(st.data())
def test_open_conflict_always_produces_review(pack, data):
    f = _random_facts(data, pack)
    if not f.usable("open_status") or f.is_yes("closure_signals"):
        pass
    sig = data.draw(st.sampled_from(pack.all_signals()))
    f2 = f.with_signal(sig, r(State.CONFLICTING))
    res = score(f2, pack, OFFER_PROFILE, CONTACT)
    # a hard vitality failure still ends scoring as D first (gates run before class)
    if "vitality_fail" not in res.reason_codes:
        assert res.cls == "review"


@settings(max_examples=100, deadline=None)
@given(st.data())
def test_same_inputs_same_output(pack, data):
    f = _random_facts(data, pack)
    assert score(f, pack, OFFER_PROFILE, CONTACT) == score(f, pack, OFFER_PROFILE, CONTACT)


@given(st.floats(1, 100), st.integers(1, 90))
def test_pressure_halves_after_one_half_life(weight, hl):
    from datetime import timedelta

    t = Trigger("x", weight, hl, NOW - timedelta(days=hl))
    assert abs(pressure_index([t], NOW) - min(100.0, round(weight / 2, 2))) < 0.02
