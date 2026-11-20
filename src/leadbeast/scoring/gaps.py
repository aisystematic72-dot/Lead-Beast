"""Gap type (what kind of visibility gap). Separate from priority class; never mixed."""

from __future__ import annotations

from leadbeast.domain.scoring import GapType
from leadbeast.evidence.facts import Facts
from leadbeast.packs.schema import GapThresholds


def gap_types(f: Facts, t: GapThresholds) -> set[GapType]:
    gaps: set[GapType] = set()
    if f.is_no("has_website") or f.is_yes("parked") or f.is_no("domain_alive"):
        gaps.add(GapType.INVISIBLE)
    elif f.score_below("website_quality", t.website_quality_weak):
        gaps.add(GapType.WEAK_SITE)
    if (
        f.is_yes("has_website")
        and f.is_no("in_map_pack")
        and f.low("review_count", t.review_count_low)
    ):
        gaps.add(GapType.UNRANKED)
    if f.all_no("ads_seen", "social_active", "fresh_content", "email_list_signup"):
        gaps.add(GapType.MARKETING_INACTIVE)
    return gaps
