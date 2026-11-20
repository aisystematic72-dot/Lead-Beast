"""OpenStreetMap via the Overpass API (open map data, free, ODbL; attribution required).

Respects the public instance's fair-use policy: one query per collection, a timeout, and the
fixed User-Agent. Trades map to OSM tags in TRADE_TAGS; extend it for new packs.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx

from leadbeast.core.clock import now
from leadbeast.core.config import get_settings
from leadbeast.core.ports import SourceQuery, Status
from leadbeast.domain.business import SourceRecord

TRADE_TAGS: dict[str, list[str]] = {
    "hvac": ['"craft"="hvac"', '"shop"="hvac"'],
    "plumbing": ['"craft"="plumber"'],
    "roofing": ['"craft"="roofer"'],
    "electrician": ['"craft"="electrician"'],
    "dental": ['"amenity"="dentist"', '"healthcare"="dentist"'],
    "cafe": ['"amenity"="cafe"'],
    "landscaping": ['"craft"="gardener"', '"landuse"="landscaping"'],
    "cleaning": ['"shop"="dry_cleaning"', '"craft"="cleaning"'],
}


def build_query(q: SourceQuery) -> str:
    tags = [t for trade in (q.trades or list(TRADE_TAGS)) for t in TRADE_TAGS.get(trade, [])]
    if q.bbox:
        s, w, n, e = q.bbox
        scope = f"({s},{w},{n},{e})"
        head = ""
    else:
        head = f'area["name"="{q.area}"]["boundary"="administrative"]->.a;'
        scope = "(area.a)"
    body = "".join(f"nwr[{t}]{scope};" for t in tags)
    # A whole-state area query takes ~2.5 minutes on the public instance.
    return f"[out:json][timeout:180];{head}({body});out center tags {q.limit};"


def _trade_of(tags: dict[str, str]) -> str | None:
    for trade, tl in TRADE_TAGS.items():
        for t in tl:
            k, v = (p.strip('"') for p in t.split("="))
            if tags.get(k) == v:
                return trade
    return None


class OsmAdapter:
    name = "osm"
    cost_per_unit_cents = 0.0

    def __init__(self, endpoint: str = "https://overpass-api.de/api/interpreter",
                 client: httpx.AsyncClient | None = None):
        self.endpoint = endpoint
        self._client = client

    def _c(self) -> httpx.AsyncClient:
        return self._client or httpx.AsyncClient(
            timeout=200, headers={"User-Agent": get_settings().user_agent})

    async def probe(self) -> Status:
        t0 = time.monotonic()
        try:
            async with self._c() as c:
                r = await c.get(self.endpoint.replace("interpreter", "status"))
            ms = int((time.monotonic() - t0) * 1000)
            return Status("ok" if r.status_code < 400 else "off", ms, f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return Status("off", None, str(e))

    async def fetch(self, query: SourceQuery) -> AsyncIterator[SourceRecord]:
        c = self._c()
        try:
            r = await c.post(self.endpoint, data={"data": build_query(query)})
            r.raise_for_status()
            data = r.json()
        finally:
            if self._client is None:
                await c.aclose()
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            if not tags.get("name"):
                continue
            center = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
            addr = {
                "line1": " ".join(x for x in (tags.get("addr:housenumber"),
                                              tags.get("addr:street")) if x) or None,
                "city": tags.get("addr:city"), "state": tags.get("addr:state"),
                "postcode": tags.get("addr:postcode"),
            }
            yield SourceRecord(
                source="osm", external_id=f"{el['type']}/{el['id']}", fetched_at=now(),
                payload={
                    "name": tags.get("name"),
                    "phone": tags.get("phone") or tags.get("contact:phone"),
                    "website": tags.get("website") or tags.get("contact:website"),
                    "email": tags.get("email") or tags.get("contact:email"),
                    "address": addr, "lat": center.get("lat"), "lon": center.get("lon"),
                    "trade": _trade_of(tags), "hours": tags.get("opening_hours"),
                    "status": "closed" if tags.get("disused:shop") or tags.get("disused:amenity")
                    else None,
                    "raw": tags,
                },
            )
