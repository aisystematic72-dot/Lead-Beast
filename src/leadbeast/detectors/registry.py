"""Detectors are registered by entry point so a pack can enable or disable them by name."""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import entry_points

from leadbeast.detectors.base import BaseDetector

_BUILTIN = {
    "website_presence": "leadbeast.detectors.website_presence:WebsitePresence",
    "website_quality": "leadbeast.detectors.website_quality:WebsiteQuality",
    "site_health": "leadbeast.detectors.site_health:SiteHealth",
    "marketing_stack": "leadbeast.detectors.marketing_stack:MarketingStack",
    "marketing_activity": "leadbeast.detectors.marketing_activity:MarketingActivity",
    "listing_claimed": "leadbeast.detectors.listing_claimed:ListingClaimed",
    "vitality": "leadbeast.detectors.vitality:Vitality",
    "operating_style": "leadbeast.detectors.operating_style:OperatingStyle",
    "ai_site_facts": "leadbeast.ai.site_facts:AiSiteFacts",
}


def _load(path: str) -> type[BaseDetector]:
    mod, _, attr = path.partition(":")
    module = __import__(mod, fromlist=[attr])
    cls: type[BaseDetector] = getattr(module, attr)
    return cls


@lru_cache
def available() -> dict[str, type[BaseDetector]]:
    found = {name: _load(p) for name, p in _BUILTIN.items()}
    for ep in entry_points(group="leadbeast.detectors"):
        found[ep.name] = ep.load()
    return found


def signals_by_detector() -> dict[str, tuple[str, ...]]:
    return {n: c.produces for n, c in available().items()}


def enabled(names: list[str]) -> list[BaseDetector]:
    reg = available()
    return [reg[n]() for n in names if n in reg]
