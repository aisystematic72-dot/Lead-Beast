"""Source hub (M2). Each adapter declares its cost and yields raw records kept verbatim."""

from __future__ import annotations

from leadbeast.core.ports import SourceAdapter


def get_adapter(name: str, **kw: object) -> SourceAdapter:
    if name == "csv":
        from leadbeast.sources.csv_adapter import CsvAdapter

        return CsvAdapter(**kw)  # type: ignore[arg-type]
    if name == "osm":
        from leadbeast.sources.osm import OsmAdapter

        return OsmAdapter(**kw)  # type: ignore[arg-type]
    raise KeyError(f"unknown source adapter: {name}")
