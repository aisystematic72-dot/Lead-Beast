"""Shared detector helpers: page fetching with a per-run cache, HTML parsing, patterns."""

from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from datetime import datetime

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from leadbeast.core.errors import HostCapReached, RobotsDisallowed
from leadbeast.core.http import FetchResult


@dataclass(frozen=True)
class Page:
    url: str
    result: FetchResult | None
    error: str | None  # timeout | blocked | robots | ssl | dns | http_<code> | transport

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok

    @property
    def html(self) -> str:
        return self.result.text if self.result else ""

    def tree(self) -> HTMLParser:
        return HTMLParser(self.html or "<html></html>")


class PageCache:
    """One fetch per URL per detector run, shared by detectors (they never call each other)."""

    def __init__(self, fetcher: object):
        self.fetcher = fetcher
        self._pages: dict[str, Page] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, url: str) -> Page:
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            if url not in self._pages:
                self._pages[url] = await self._fetch(url)
            return self._pages[url]

    async def _fetch(self, url: str) -> Page:
        try:
            r = await self.fetcher.get(url)  # type: ignore[attr-defined]
            err = None if r.ok else f"http_{r.status}"
            if r.status in (401, 403, 429):
                err = "blocked"
            return Page(url, r, err)
        except RobotsDisallowed:
            return Page(url, None, "robots")
        except HostCapReached:
            return Page(url, None, "blocked")
        except httpx.ConnectError as e:
            msg = str(e).lower()
            if "certificate" in msg or "ssl" in msg or isinstance(e.__cause__, ssl.SSLError):
                return Page(url, None, "ssl")
            if "name or service" in msg or "getaddrinfo" in msg or "nodename" in msg:
                return Page(url, None, "dns")
            return Page(url, None, "transport")
        except httpx.TimeoutException:
            return Page(url, None, "timeout")
        except httpx.HTTPError:
            return Page(url, None, "transport")


def homepage(url: str | None) -> str | None:
    if not url:
        return None
    return url if re.match(r"^https?://", url) else "http://" + url


def text_of(tree: HTMLParser, limit: int = 20000) -> str:
    for tag in tree.css("script, style, noscript"):
        tag.decompose()
    body = tree.body
    txt = body.text(separator=" ") if body else tree.text(separator=" ")
    return re.sub(r"\s+", " ", txt).strip()[:limit]


def find_span(html: str, pattern: str, width: int = 60) -> str | None:
    m = re.search(pattern, html, re.I)
    if not m:
        return None
    a, b = max(0, m.start() - width // 2), min(len(html), m.end() + width // 2)
    return html[a:b].strip()


PARKED_PATTERNS = [
    r"this domain (is|may be) for sale", r"buy this domain", r"domain parking",
    r"parked (free|domain)", r"sedoparking", r"godaddy\.com/domains", r"hugedomains",
    r"dan\.com", r"afternic", r"this web page is parked",
]
COMING_SOON = [r"coming soon", r"under construction", r"site is being built", r"launching soon"]
YEAR_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d{2})", re.I)


def copyright_year(html: str) -> int | None:
    years = [int(y) for y in YEAR_RE.findall(html)]
    return max(years) if years else None


def years_since(year: int, at: datetime) -> int:
    return max(0, at.year - year)
