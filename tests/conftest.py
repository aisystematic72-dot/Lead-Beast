from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from leadbeast.core import clock
from leadbeast.domain.evidence import EvidenceEvent, Resolved, State
from leadbeast.evidence.facts import Facts
from leadbeast.packs.loader import load_pack
from leadbeast.packs.schema import PackSpec

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
BIZ = UUID("00000000-0000-7000-8000-000000000001")


@pytest.fixture(autouse=True)
def _freeze():
    with clock.frozen(NOW):
        yield


@pytest.fixture(scope="session")
def pack() -> PackSpec:
    return load_pack(ROOT / "packs" / "visibility_gap")


def ev(
    signal: str,
    state: State = State.OBSERVED_YES,
    value=None,
    *,
    days_ago: float = 1,
    source: str = "test",
    span: str | None = None,
    supersedes: UUID | None = None,
) -> EvidenceEvent:
    return EvidenceEvent(
        id=uuid4(), business_id=BIZ, signal=signal, state=state, value=value, source=source,
        source_url="https://example.com", span=span,
        observed_at=NOW - timedelta(days=days_ago), detector="test", detector_version="1",
        supersedes_id=supersedes,
    )


def r(state: State, value=None) -> Resolved:
    return Resolved(state=state, value=value, event_id=uuid4(), observed_at=NOW)


def strong_lead_facts(**overrides: Resolved) -> Facts:
    """A business that should land in Class A with a usable contact."""
    base = {
        "has_website": r(State.OBSERVED_NO),
        "domain_alive": r(State.OBSERVED_NO),
        "parked": r(State.OBSERVED_NO),
        "listing_claimed": r(State.OBSERVED_NO),
        "review_count": r(State.OBSERVED_YES, 3),
        "in_map_pack": r(State.OBSERVED_NO),
        "social_active": r(State.OBSERVED_NO),
        "fresh_content": r(State.OBSERVED_NO),
        "ads_seen": r(State.NOT_DETECTED),
        "email_list_signup": r(State.NOT_DETECTED),
        "licence_active": r(State.OBSERVED_YES, True),
        "staff_count": r(State.OBSERVED_YES, 12),
        "years_in_business": r(State.OBSERVED_YES, 14),
        "photo_count": r(State.OBSERVED_YES, 18),
        "hours_present": r(State.OBSERVED_YES, True),
        "own_domain_email": r(State.OBSERVED_YES, True),
        "last_review_age": r(State.OBSERVED_YES, 10),
        "recent_activity": r(State.OBSERVED_YES, True),
        "new_location": r(State.OBSERVED_YES, True),
        "open_status": r(State.OBSERVED_YES, True),
        "contact_email_valid": r(State.OBSERVED_YES, True),
        "contact_phone_valid": r(State.OBSERVED_YES, True),
        "contact_path": r(State.OBSERVED_YES, True),
        "pixels": r(State.NOT_DETECTED),
        "crm_forms": r(State.NOT_DETECTED),
        "booking_widget": r(State.NOT_DETECTED),
    }
    base.update(overrides)
    return Facts(base)


OFFER_PROFILE = {"A": 90, "B": 80, "C": 65, "D": 55}
CONTACT = {"usable": True, "channel": "email"}
