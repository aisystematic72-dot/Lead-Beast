"""Notifiers: in-app, ntfy (recommended first), Telegram, webhook (HMAC-signed)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, ClassVar, Protocol

import httpx

from leadbeast.alerts.models import AlertCard, DeliveryResult
from leadbeast.core.secrets import resolve


class Notifier(Protocol):
    kind: ClassVar[str]

    async def send(self, card: AlertCard) -> DeliveryResult: ...
    async def health(self) -> bool: ...


class InAppNotifier:
    kind = "inapp"

    async def send(self, card: AlertCard) -> DeliveryResult:
        return DeliveryResult(ok=True, detail="stored")  # the panic_alert row is the inbox

    async def health(self) -> bool:
        return True


class NtfyNotifier:
    kind = "ntfy"

    def __init__(self, config: dict[str, Any], client: httpx.AsyncClient | None = None):
        self.base = config.get("base_url", "http://ntfy").rstrip("/")
        self.topic = config["topic"]
        self.token = resolve(config.get("token_ref"))
        self.c = client or httpx.AsyncClient(timeout=15)

    async def send(self, card: AlertCard) -> DeliveryResult:
        headers = {"Title": card.title[:200], "Priority": "high" if card.pressure >= 80 else
                   "default", "Click": card.url, "Tags": card.direction}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = await self.c.post(f"{self.base}/{self.topic}",
                                  content=f"{card.line} · pressure {card.pressure}".encode(),
                                  headers=headers)
            return DeliveryResult(ok=r.status_code < 300, detail=f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return DeliveryResult(ok=False, detail=str(e))

    async def health(self) -> bool:
        try:
            return (await self.c.get(f"{self.base}/v1/health")).status_code == 200
        except httpx.HTTPError:
            return False


class TelegramNotifier:
    kind = "telegram"

    def __init__(self, config: dict[str, Any], client: httpx.AsyncClient | None = None):
        self.token = resolve(config.get("token_ref")) or ""
        self.chat_id = config["chat_id"]
        self.c = client or httpx.AsyncClient(timeout=15)

    async def send(self, card: AlertCard) -> DeliveryResult:
        kb = {"inline_keyboard": [[{"text": a.label, "url": a.url} for a in card.actions]]}
        try:
            r = await self.c.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json={
                "chat_id": self.chat_id, "text": f"{card.title}\n{card.line}\n{card.url}",
                "reply_markup": kb})
            return DeliveryResult(ok=r.status_code == 200, detail=f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return DeliveryResult(ok=False, detail=str(e))

    async def health(self) -> bool:
        try:
            r = await self.c.get(f"https://api.telegram.org/bot{self.token}/getMe")
            return r.status_code == 200
        except httpx.HTTPError:
            return False


class WebhookNotifier:
    kind = "webhook"

    def __init__(self, config: dict[str, Any], client: httpx.AsyncClient | None = None):
        self.url = config["url"]
        self.secret = (resolve(config.get("secret_ref")) or "").encode()
        self.c = client or httpx.AsyncClient(timeout=15)

    async def send(self, card: AlertCard) -> DeliveryResult:
        body = json.dumps(card.model_dump()).encode()
        sig = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        try:
            r = await self.c.post(self.url, content=body, headers={
                "Content-Type": "application/json", "X-LeadBeast-Signature": sig})
            return DeliveryResult(ok=r.status_code < 300, detail=f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return DeliveryResult(ok=False, detail=str(e))

    async def health(self) -> bool:
        return True


def notifier_for(kind: str, config: dict[str, Any]) -> Notifier:
    return {
        "inapp": lambda: InAppNotifier(),
        "ntfy": lambda: NtfyNotifier(config),
        "telegram": lambda: TelegramNotifier(config),
        "webhook": lambda: WebhookNotifier(config),
    }[kind]()
