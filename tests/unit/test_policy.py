from datetime import time

from leadbeast.policy.engine import (
    CitedSignal,
    PolicyContext,
    Verdict,
    body_hash,
    check,
    in_quiet_hours,
)

BODY = "Hi there,\nYour listing is unclaimed.\n--\nOp\n1 Main St\nunsubscribe: https://x/u"
GOOD = CitedSignal("listing_claimed", "observed_no", 0.95)


def ctx(**kw):
    base = dict(priority_class="B", body=BODY, cited=(GOOD,), approval_hash=body_hash(BODY),
                operator_identity_complete=True, has_unsubscribe=True,
                recipient_local_time=time(11, 0), distress_words=("desperate", "failing"))
    base.update(kw)
    return PolicyContext(**base)


def test_clean_send_allowed():
    assert check("send", ctx()).allowed


def test_no_approval_denied():
    d = check("send", ctx(approval_hash=None))
    assert d.verdict is Verdict.DENY and "no_approval" in d.reasons


def test_edited_body_after_approval_denied():
    d = check("send", ctx(body=BODY + " extra"))
    assert "approval_hash_mismatch" in d.reasons


def test_class_a_needs_mystery_check():
    assert "mystery_check_missing" in check("send", ctx(priority_class="A")).reasons
    assert check("send", ctx(priority_class="A", mystery_check_passed=True)).allowed


def test_signal_below_precision_blocks_draft():
    d = check("draft", ctx(cited=(CitedSignal("listing_claimed", "observed_no", 0.8),)))
    assert any(r.startswith("signal_below_precision") for r in d.reasons)
    d = check("draft", ctx(cited=(CitedSignal("listing_claimed", "observed_no", None),)))
    assert d.verdict is Verdict.DENY


def test_stale_or_conflicting_cited_denied():
    for st in ("stale", "conflicting"):
        d = check("draft", ctx(cited=(CitedSignal("has_website", st, 0.99),)))
        assert d.verdict is Verdict.DENY


def test_pressure_signal_never_in_draft():
    d = check("draft", ctx(cited=(CitedSignal("hiring_repost", "observed_yes", 0.99,
                                                is_pressure=True),)))
    assert any("restricted" in r for r in d.reasons)


def test_distress_word_denied():
    d = check("draft", ctx(body="You look desperate for customers"))
    assert any(r.startswith("distress_language") for r in d.reasons)


def test_suppressed_denied_everywhere():
    for action in ("draft", "send", "contact_lookup"):
        assert check(action, ctx(suppressed=("email",))).verdict is Verdict.DENY


def test_competitor_denied_unless_partner():
    assert check("draft", ctx(competitor_overlap=("web_design",))).verdict is Verdict.DENY
    assert check("draft", ctx(competitor_overlap=("web_design",), partner_campaign=True)).allowed


def test_daily_cap_and_quiet_hours():
    assert "daily_send_cap" in check("send", ctx(sends_today=20, daily_cap=20)).reasons
    assert "quiet_hours" in check("send", ctx(recipient_local_time=time(22, 30))).reasons


def test_footer_required_for_email():
    d = check("send", ctx(operator_identity_complete=False, has_unsubscribe=False))
    assert "footer_missing_sender_identity" in d.reasons
    assert "footer_missing_unsubscribe" in d.reasons


def test_review_class_needs_review_d_denied():
    assert check("draft", ctx(priority_class="review")).verdict is Verdict.NEEDS_REVIEW
    assert check("draft", ctx(priority_class="D")).verdict is Verdict.DENY


def test_collect_respects_robots():
    assert check("collect", ctx(robots_allowed=False)).verdict is Verdict.DENY


def test_quiet_hours_across_midnight():
    assert in_quiet_hours(time(23), time(20), time(8))
    assert in_quiet_hours(time(7, 59), time(20), time(8))
    assert not in_quiet_hours(time(8), time(20), time(8))
