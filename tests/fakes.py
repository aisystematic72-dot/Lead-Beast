"""Test doubles: a fetcher that serves recorded fixtures and never touches the network."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from leadbeast.core.http import FetchResult
from leadbeast.detectors.base import DetectorInput
from leadbeast.detectors.helpers import Page
from leadbeast.domain.business import Business

FIX = Path(__file__).parent / "fixtures" / "html"


class FixtureCache:
    """Stands in for PageCache. Map URL -> fixture name, or an error string."""

    def __init__(self, routes: dict[str, str], elapsed_ms: int = 600):
        self.routes = routes
        self.elapsed_ms = elapsed_ms
        self.calls: list[str] = []

    async def get(self, url: str) -> Page:
        self.calls.append(url)
        target = self.routes.get(url)
        if target is None:
            return Page(url, None, "transport")
        if target.startswith("error:"):
            return Page(url, None, target.split(":", 1)[1])
        html = (FIX / target).read_text(encoding="utf-8")
        final = url.replace("http://", "https://") if "https" in target else url
        return Page(url, FetchResult(url, final, 200, html, {}, self.elapsed_ms), None)


def biz(website: str | None = None, **kw: object) -> DetectorInput:
    b = Business(id=UUID("00000000-0000-7000-8000-0000000000aa"), canonical_name="Acme Plumbing",
                 website_url=website, domain=None, trade="plumbing")
    return DetectorInput(business=b, records=list(kw.pop("records", [])), facts=None)  # type: ignore[arg-type]


async def alive(_: str) -> bool:
    return True


async def dead(_: str) -> bool:
    return False
