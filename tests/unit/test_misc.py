from datetime import UTC, datetime, time, timedelta

import pytest
from tests.conftest import r

from leadbeast.ai.extract import Extraction, cited_only, extract
from leadbeast.alerts import matcher, quiet, render
from leadbeast.alerts.models import BusinessState, PressureState, WatchRule
from leadbeast.audit.findings import build_findings
from leadbeast.cost.budget import CapCheck, evaluate
from leadbeast.domain.evidence import State
from leadbeast.evidence.facts import Facts
from leadbeast.golden.service import compute
from leadbeast.health.service import next_circuit
from leadbeast.intel.payback import payback
from leadbeast.outreach.compose import compose
from leadbeast.outreach.lint import lint
from leadbeast.packs.loader import validate_pack
from leadbeast.packs.schema import Offer, ProblemSpec
from leadbeast.policy.deliverability import DomainHealth, next_cap


# ---------- cited extraction (rule 12)
def test_uncited_claims_dropped():
    text = "Family owned since 2004 with 8 technicians."
    raw = [Extraction(field="team_size", value="8", span="8 technicians"),
           Extraction(field="years_in_business", value="30", span="since 1990")]
    assert [e.field for e in cited_only(raw, text)] == ["team_size"]


class BadModel:
    name, version, calls = "fake", "1", 0

    async def json(self, prompt, schema):
        BadModel.calls += 1
        raise ValueError("not json")


async def test_invalid_model_output_retried_once_then_none():
    assert await extract(BadModel(), "text", ["services"]) is None
    assert BadModel.calls == 2


# ---------- payback rule (rule 9)
def test_payback_range_and_verdict():
    o = Offer(name="x", monthly_fee=300, customer_value_low=600, customer_value_high=1500)
    pb = payback(o)
    assert pb.verdict == "pass" and pb.multiple_low == 2.0 and pb.provisional
    assert payback(Offer(name="x", monthly_fee=300, customer_value_low=300,
                         customer_value_high=900)).verdict == "marginal"
    assert payback(Offer(name="x", monthly_fee=300, customer_value_low=100,
                         customer_value_high=400)).verdict == "fail"


# ---------- cost guard
def test_cap_refuses_before_spend_and_warns_at_80():
    checks = [CapCheck("global", 1000, 790), CapCheck("connector:apollo", 0, 0)]
    exceeded, warn = evaluate(checks, 20)
    assert not exceeded and warn == ["global"]
    exceeded, _ = evaluate(checks, 211)
    assert exceeded == ["global"]


# ---------- golden precision
def test_precision_on_positive_calls():
    pairs = [(True, True)] * 9 + [(True, False)] + [(None, True), (False, False)]
    p, rec, calls = compute(pairs, 0.9)
    assert p == 0.9 and calls == 10 and rec == 0.9


# ---------- circuit breaker
def test_circuit_transitions():
    assert next_circuit("closed", 0, False) == ("closed", 1)
    assert next_circuit("closed", 2, False) == ("open", 3)
    assert next_circuit("open", 3, True) == ("half_open", 0)
    assert next_circuit("half_open", 0, False)[0] == "open"
    assert next_circuit("half_open", 0, True) == ("closed", 0)


# ---------- deliverability warm-up
@pytest.mark.parametrize("day", range(0, 15))
def test_warmup_never_raises_after_breach(day):
    breached = DomainHealth(daily_cap=50, bounce_rate_7d=0.05, complaint_rate_7d=0)
    assert next_cap(breached, day) == 50
    ok = DomainHealth(daily_cap=20, bounce_rate_7d=0, complaint_rate_7d=0)
    assert next_cap(ok, day) >= 20


# ---------- drafts
def test_lint_catches_distress_and_placeholders():
    res = lint("You seem to be failing. {findings}", ["failing"], ["hiring_repost"])
    assert not res.ok
    assert {"distress_word:failing", "unfilled_placeholder", "restricted_signal:hiring_repost"} \
        <= set(res.problems)


def test_compose_only_uses_cited_findings():
    c = compose("Hi {contact_greeting},\n{findings}\n{operator_name}", "About {business_name}",
                business_name="Acme", trade="plumbing", city="Brooklyn", contact_name="Maria Lopez",
                findings=[{"signal": "listing_claimed", "statement": "Listing unclaimed.",
                           "evidence_id": "e1"},
                          {"signal": "x", "statement": "Uncited claim.", "evidence_id": None}],
                operator_name="Op", operator_phone="1")
    assert "Uncited" not in c.body and "Listing unclaimed." in c.body
    assert c.cited_evidence_ids == ["e1"] and c.body.startswith("Hi Maria")


# ---------- audit findings respect the golden gate
def test_findings_hold_back_unqualified_and_list_conflicts():
    f = Facts({"listing_claimed": r(State.OBSERVED_NO), "has_website": r(State.CONFLICTING),
               "mobile_friendly": r(State.OBSERVED_NO)})
    findings, open_items = build_findings(f, live={"listing_claimed"})
    assert [x.signal for x in findings] == ["listing_claimed"]
    notes = {o["signal"]: o["note"] for o in open_items}
    assert "verify" in notes["has_website"] and "held back" in notes["mobile_friendly"]


# ---------- pack validation
def test_pack_validates_against_detectors(pack):
    from leadbeast.detectors.registry import signals_by_detector

    assert validate_pack(pack, signals_by_detector()) == []


def test_acute_problem_needs_half_life():
    with pytest.raises(ValueError):
        ProblemSpec(id="x", name="x", areas=[], kind="acute", symptoms=[])


# ---------- alerts (M24)
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)


def state(**kw):
    p = PressureState(index=kw.pop("index", 75), direction=kw.pop("direction", "rising"),
                      aspects={"reviews": 20.0, "hiring": 10.0})
    base = dict(business_id="b1", vitality_pass=True, can_pay=True, priority_class="B",
                industry="roofing", city="Tampa", pressure=p)
    base.update(kw)
    return BusinessState(**base)


def test_decide_never_matches_when_gate_fails():
    w = WatchRule(min_pressure=10)
    assert matcher.decide(state(vitality_pass=False), None, w, {}).reason == "gate"
    assert matcher.decide(state(can_pay=False), None, w, {}).reason == "gate"


def test_decide_new_match_and_unverified():
    d = matcher.decide(state(), None, WatchRule(industries=["roofing"]), {"reviews": 0.95})
    assert d.matched and d.event == "new_match" and d.aspect == "reviews" and not d.unverified
    d2 = matcher.decide(state(), None, WatchRule(), {})
    assert d2.unverified


def test_classify_event_rules():
    prev = PressureState(index=50, direction="steady")
    assert matcher.classify_event(prev, PressureState(index=66, direction="steady")) == "escalation"
    assert matcher.classify_event(PressureState(index=70, direction="fading"),
                                  PressureState(index=70, direction="steady")) == "reopen"
    assert matcher.classify_event(prev, PressureState(index=52, direction="steady")) is None


def test_dedupe_key_stable_inside_bucket():
    k1 = matcher.dedupe_key("b", "reviews", "new_match", NOW, 7)
    k2 = matcher.dedupe_key("b", "reviews", "new_match", NOW + timedelta(hours=5), 7)
    assert k1 == k2


def test_quiet_hours_dst_and_breakthrough():
    # 2026-11-01 is the US DST change; 01:30 local New York is inside 22:00-07:00
    at = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    assert quiet.in_quiet(at, time(22), time(7), "America/New_York")
    assert not quiet.should_hold(at, time(22), time(7), "America/New_York", 95, 90)
    assert quiet.should_hold(at, time(22), time(7), "America/New_York", 50, 90)


def test_phrase_table_has_no_banned_words_or_contact_details():
    for p in render.PHRASES.values():
        assert not any(b in p.lower() for b in render.BANNED)
        assert not render.CONTACT_RE.search(p)
    c = render.card(alert_id="1", business_name="Acme 212-555-0187", industry="roofing",
                    area="Tampa", aspect="reviews", pressure=80, direction="rising", klass="B",
                    evidence_count=2, confidence="medium", created_at=NOW, now=NOW,
                    base_url="http://x")
    assert "555" not in c.title


def test_alerts_never_import_outreach():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "leadbeast" / "alerts"
    for f in root.glob("*.py"):
        assert "leadbeast.outreach" not in f.read_text(encoding="utf-8"), f.name
