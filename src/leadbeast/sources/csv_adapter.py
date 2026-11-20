"""CSV import (M2) and client seed import (M19).

Column names are matched loosely (e.g. "Business Name", "name", "title"). Listing columns
(rating, reviews, claimed, photos, hours) are kept in the payload so the listing detector can
emit evidence from the operator's own data instead of scraping map sites.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

from leadbeast.core.clock import now
from leadbeast.core.ports import SourceQuery, Status
from leadbeast.domain.business import SourceRecord

ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "business name", "business_name", "company", "title", "business"),
    "phone": ("phone", "phone number", "telephone", "phone_number", "tel"),
    "website": ("website", "url", "site", "web", "domain"),
    "address": ("address", "full address", "full_address", "street address", "street"),
    "city": ("city", "town", "locality"),
    "state": ("state", "region", "province"),
    "postcode": ("zip", "postcode", "postal code", "zip code", "zipcode", "postal_code"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "longitude"),
    "trade": ("trade", "category", "industry", "type", "main category"),
    "rating": ("rating", "stars", "google rating"),
    "review_count": ("reviews", "review count", "review_count", "number of reviews"),
    "claimed": ("claimed", "is claimed", "listing claimed", "verified"),
    "photo_count": ("photos", "photo count", "photo_count", "images"),
    "hours": ("hours", "opening hours", "working hours"),
    "email": ("email", "e-mail", "email address"),
    "status": ("status", "business status", "business_status"),
    "staff_count": ("employees", "staff", "staff count", "headcount"),
    "years_in_business": ("years in business", "years_in_business"),
    "listing_url": ("listing url", "maps url", "google maps url", "place url"),
    "licence_status": ("licence status", "license status", "licence", "license"),
    "last_review_age": ("last review age", "days since last review", "last_review_days"),
    "map_pack_rank": ("map pack rank", "map rank", "local rank"),
    # social activity, noted by hand from the business's own pages (never scraped)
    "facebook": ("facebook", "facebook url", "facebook page"),
    "instagram": ("instagram", "instagram url", "instagram page"),
    "last_post_date": ("last post date", "last social post", "last_post_date", "last post"),
    "followers": ("followers", "follower count", "followers count"),
    # written by `leadbeast social fb-analyze` (see leadbeast.social.fb_activity)
    "facebook_activity": ("facebook activity", "facebook_activity", "fb activity"),
    "facebook_summary": ("facebook summary", "facebook_summary", "fb summary"),
}


def _map_headers(headers: list[str]) -> dict[str, str]:
    low = {h.strip().lower(): h for h in headers}
    out: dict[str, str] = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in low:
                out[key] = low[n]
                break
    return out


class CsvAdapter:
    name = "csv"
    cost_per_unit_cents = 0.0

    def __init__(self, path: str | Path, source_name: str = "csv", seed: bool = False):
        self.path = Path(path)
        self.name = source_name
        self.seed = seed

    async def probe(self) -> Status:
        return Status("ok" if self.path.exists() else "off", detail=str(self.path))

    async def fetch(self, query: SourceQuery) -> AsyncIterator[SourceRecord]:
        with self.path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = _map_headers(list(reader.fieldnames or []))
            for i, row in enumerate(reader):
                if i >= query.limit:
                    break
                payload: dict[str, object] = {
                    k: (row.get(src) or "").strip() for k, src in cols.items()
                }
                payload["raw"] = row
                payload["seed_client"] = self.seed
                if not payload.get("name"):
                    continue
                ext = hashlib.sha1(
                    f"{payload.get('name')}|{payload.get('phone')}|{payload.get('address')}".encode()
                ).hexdigest()
                yield SourceRecord(source=self.name, external_id=ext, fetched_at=now(),
                                   payload=payload)
