"""Channel adapters. SMTP works with Amazon SES SMTP or any relay; manual logs a call/visit."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import time
from collections.abc import AsyncIterator
from email.message import EmailMessage
from email.utils import make_msgid

from leadbeast.core.config import get_settings
from leadbeast.core.ports import ApprovedDraft, InboundMessage, ProviderReceipt, Status
from leadbeast.core.secrets import resolve


class SmtpChannel:
    channel = "email"

    def __init__(self) -> None:
        self.s = get_settings()

    async def probe(self) -> Status:
        if not self.s.smtp_host:
            return Status("off", detail="LB_SMTP_HOST not set")
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self._noop)
            return Status("ok", int((time.monotonic() - t0) * 1000))
        except (OSError, smtplib.SMTPException) as e:
            return Status("off", detail=str(e))

    def _noop(self) -> None:
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=10) as c:
            c.starttls(context=ssl.create_default_context())
            c.noop()

    def _send(self, d: ApprovedDraft) -> str:
        msg = EmailMessage()
        mid = make_msgid(domain=(self.s.sender_email.split("@")[-1] or "leadbeast.local"))
        msg["Message-ID"] = mid
        msg["From"] = f"{self.s.operator_name} <{self.s.sender_email}>"
        msg["To"] = d.to
        msg["Subject"] = d.subject
        for k, v in d.headers.items():
            msg[k] = v
        msg.set_content(d.body)
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30) as c:
            c.starttls(context=ssl.create_default_context())
            user, pw = resolve(self.s.smtp_user_ref), resolve(self.s.smtp_password_ref)
            if user and pw:
                c.login(user, pw)
            c.send_message(msg)
        return mid

    async def send(self, draft: ApprovedDraft) -> ProviderReceipt:
        if not self.s.smtp_host or not self.s.sender_email:
            return ProviderReceipt("", False, "smtp not configured")
        try:
            mid = await asyncio.to_thread(self._send, draft)
            return ProviderReceipt(mid, True)
        except (OSError, smtplib.SMTPException) as e:
            return ProviderReceipt("", False, str(e))

    async def inbound(self) -> AsyncIterator[InboundMessage]:  # replies arrive via webhook
        return
        yield  # pragma: no cover


class ManualChannel:
    """Records that a person made the contact (call, visit). Nothing is transmitted."""

    channel = "manual"

    async def probe(self) -> Status:
        return Status("ok")

    async def send(self, draft: ApprovedDraft) -> ProviderReceipt:
        return ProviderReceipt(f"manual-{draft.draft_id}", True, "logged by operator")

    async def inbound(self) -> AsyncIterator[InboundMessage]:
        return
        yield  # pragma: no cover


def channel_for(name: str) -> SmtpChannel | ManualChannel:
    return SmtpChannel() if name == "email" else ManualChannel()
