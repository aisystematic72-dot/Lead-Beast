"""Panic Alert matcher: lead-class filter and operator-verified evidence (general features)."""

import pytest

from leadbeast.alerts import matcher
from leadbeast.alerts.models import BusinessState, PressureState, WatchRule


def _biz(cls: str) -> BusinessState:
    return BusinessState(business_id="b", vitality_pass=True, can_pay=True, priority_class=cls,
                         pressure=PressureState(index=40, direction="rising",
                                                aspects={"activity": 40},
                                                trigger_ids=["activity"]))


def test_operator_verified_evidence_makes_alert_verified():
    w = WatchRule(min_pressure=25, min_class="D")
    assert matcher.decide(_biz("D"), None, w, {}).unverified is True
    d = matcher.decide(_biz("D"), None, w, {}, frozenset({"activity"}))
    assert d.matched and d.unverified is False


def test_automatic_signals_still_need_golden_precision():
    assert matcher.is_unverified("reviews", {"reviews": 0.85}, frozenset({"activity"}))
    assert not matcher.is_unverified("reviews", {"reviews": 0.95})


@pytest.mark.parametrize(("min_class", "cls", "matched"), [
    ("D", "D", True), ("D", "C", True), ("C", "D", False), ("C", "review", True),
    ("B", "C", False),
])
def test_watch_min_class_accepts_c_and_d(min_class, cls, matched):
    w = WatchRule(min_pressure=25, min_class=min_class)
    assert matcher.decide(_biz(cls), None, w, {}).matched is matched
