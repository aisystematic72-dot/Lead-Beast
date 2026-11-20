"""Polite HTTP fetcher: robots.txt, per-host rate limit with jitter, daily host cap, retries.

Every outbound read goes through here. A blocked or disallowed fetch is reported, never
worked around: callers turn it into an `unknown` evidence state.
"""

from __future__ import annotations

import asyncio
import random
import time
import urllib.robotparser
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from leadbeast.core.config import Settings, get_settings
from leadbeast.core.errors import HostCapReached, RobotsDisallowed


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    text: str
    headers: dict[str, str]
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400


class _Transient(Exception):
    pass


class Fetcher:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.s = settings or get_settings()
        self._client = client or httpx.AsyncClient(
            timeout=self.s.http_timeout_s,
            follow_redirects=True,
            headers={"User-Agent": self.s.user_agent},
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._count: dict[tuple[str, date], int] = defaultdict(int)
        self.respect_robots = True

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                r = await self._client.get(f"{base}/robots.txt")
                if r.status_code >= 400:
                    rp = None  # no robots file: allowed
                else:
                    assert rp is not None
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.s.user_agent, url)

    async def _pace(self, host: str) -> None:
        key = (host, date.today())
        if self._count[key] >= self.s.daily_request_cap_per_host:
            raise HostCapReached(host)
        async with self._locks[host]:
            gap = self.s.per_host_min_interval_s * random.uniform(1.0, 1.6)
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()
            self._count[key] += 1

    async def get(self, url: str) -> FetchResult:
        if not await self._allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, max=20),
            retry=retry_if_exception_type((_Transient, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                await self._pace(host)
                t0 = time.monotonic()
                r = await self._client.get(url)
                if r.status_code == 429 or r.status_code >= 500:
                    ra = r.headers.get("Retry-After")
                    if ra and ra.isdigit():
                        await asyncio.sleep(min(int(ra), 60))
                    raise _Transient(f"{r.status_code} {url}")
                return FetchResult(
                    url=url,
                    final_url=str(r.url),
                    status=r.status_code,
                    text=r.text,
                    headers={k.lower(): v for k, v in r.headers.items()},
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
        raise AssertionError("unreachable")
