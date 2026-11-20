"""policy.check(action, context) -> Decision. Pure: all inputs arrive in the context.

Called at draft time and again at send time, because state can change between the two.
No adapter sends without a fresh `allow`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Literal

Action = Literal["draft", "send", "contact_lookup", "collect"]


class Verdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reasons: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


@dataclass(frozen=True)
class CitedSignal:
    signal: str
    state: str
    precision: float | None  # golden-set precision, None = never measured
    is_pressure: bool = False
    use_in_message: bool = True


@dataclass(frozen=True)
class PolicyContext:
    # identity of the target
    priority_class: str | None = None
    channel: str = "email"
    suppressed: tuple[str, ...] = ()  # kinds that matched: email/phone/domain/business

    # drafts
    body: str = ""
    cited: tuple[CitedSignal, ...] = ()
    distress_words: tuple[str, ...] = ()
    competitor_overlap: tuple[str, ...] = ()
    partner_campaign: bool = False
    open_conflict: bool = False

    # sends
    approval_hash: str | None = None
    mystery_check_passed: bool = False
    sends_today: int = 0
    daily_cap: int = 20
    sender_paused: bool = False
    recipient_local_time: time | None = None
    quiet_start: time = time(20, 0)
    quiet_end: time = time(8, 0)
    operator_identity_complete: bool = False
    has_unsubscribe: bool = False
    allowed_channels: tuple[str, ...] = ("email",)

    # collection
    robots_allowed: bool = True
    source_terms_ok: bool = True

    threshold: float = 0.90
    extra: dict[str, object] = field(default_factory=dict)


def body_hash(body: str) -> str:
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def in_quiet_hours(t: time, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end  # window crosses midnight


def _distress_hits(body: str, words: tuple[str, ...]) -> list[str]:
    low = body.lower()
    return [w for w in words if re.search(r"\b" + re.escape(w.lower()) + r"\b", low)]


def _draft_rules(c: PolicyContext) -> list[str]:
    deny: list[str] = []
    for cs in c.cited:
        if cs.is_pressure or not cs.use_in_message:
            deny.append(f"cites_restricted_signal:{cs.signal}")
        elif cs.state in ("stale", "conflicting", "unknown"):
            deny.append(f"cites_{cs.state}_signal:{cs.signal}")
        elif cs.precision is None or cs.precision < c.threshold:
            deny.append(f"signal_below_precision:{cs.signal}")
    hits = _distress_hits(c.body, c.distress_words)
    if hits:
        deny.append("distress_language:" + ",".join(hits))
    if c.competitor_overlap and not c.partner_campaign:
        deny.append("competitor:" + ",".join(c.competitor_overlap))
    if c.suppressed:
        deny.append("suppressed:" + ",".join(c.suppressed))
    if c.channel not in c.allowed_channels:
        deny.append(f"channel_not_allowed:{c.channel}")
    return deny


def check(action: Action, c: PolicyContext) -> Decision:
    deny: list[str] = []
    review: list[str] = []

    if action == "collect":
        if not c.robots_allowed:
            deny.append("robots_disallowed")
        if not c.source_terms_ok:
            deny.append("source_terms")
        return Decision(Verdict.DENY if deny else Verdict.ALLOW, tuple(deny))

    if action == "contact_lookup":
        if c.suppressed:
            deny.append("suppressed:" + ",".join(c.suppressed))
        if c.priority_class not in ("A", "B", "review_cleared"):
            deny.append("class_not_eligible_for_lookup")
        return Decision(Verdict.DENY if deny else Verdict.ALLOW, tuple(deny))

    if c.priority_class in (None, "D"):
        deny.append(f"class_not_eligible:{c.priority_class}")
    if c.priority_class == "review" or c.open_conflict:
        review.append("open_review")

    deny.extend(_draft_rules(c))

    if action == "send":
        if not c.approval_hash:
            deny.append("no_approval")
        elif c.approval_hash != body_hash(c.body):
            deny.append("approval_hash_mismatch")
        if c.priority_class == "A" and not c.mystery_check_passed:
            deny.append("mystery_check_missing")
        if c.sender_paused:
            deny.append("sender_paused")
        if c.sends_today >= c.daily_cap:
            deny.append("daily_send_cap")
        if c.recipient_local_time is not None and in_quiet_hours(
            c.recipient_local_time, c.quiet_start, c.quiet_end
        ):
            deny.append("quiet_hours")
        if c.channel == "email":
            if not c.operator_identity_complete:
                deny.append("footer_missing_sender_identity")
            if not c.has_unsubscribe:
                deny.append("footer_missing_unsubscribe")

    if deny:
        return Decision(Verdict.DENY, tuple(deny + review))
    if review:
        return Decision(Verdict.NEEDS_REVIEW, tuple(review))
    return Decision(Verdict.ALLOW)


def local_time_for(at: datetime, tz: str) -> time:
    from zoneinfo import ZoneInfo

    return at.astimezone(ZoneInfo(tz)).time()
