"""EspoCRM adapter (default CRM). LeadBeast stays the source of evidence; the CRM holds people,
deals and conversations. Idempotent: look up by crm_id, then by external key, then create."""

from __future__ import annotations

import time
from typing import Any

import httpx

from leadbeast.core.config import get_settings
from leadbeast.core.ports import Status
from leadbeast.core.secrets import resolve
from leadbeast.domain.business import Business, Contact

STAGES = {"new": "New", "contacted": "In Process", "replied": "In Process",
          "won": "Converted", "lost": "Dead"}


class EspoCrmAdapter:
    name = "espocrm"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 client: httpx.AsyncClient | None = None):
        st = get_settings()
        self.base = (base_url or st.espocrm_url).rstrip("/") + "/api/v1"
        key = api_key or resolve(st.espocrm_api_key_ref) or ""
        self.c = client or httpx.AsyncClient(timeout=20, headers={"X-Api-Key": key})

    async def probe(self) -> Status:
        t0 = time.monotonic()
        try:
            r = await self.c.get(f"{self.base}/App/user")
            ms = int((time.monotonic() - t0) * 1000)
            return Status("ok" if r.status_code == 200 else "off", ms, f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return Status("off", None, str(e))

    async def _find(self, entity: str, field: str, value: str) -> str | None:
        r = await self.c.get(f"{self.base}/{entity}", params={
            "where[0][type]": "equals", "where[0][attribute]": field,
            "where[0][value]": value, "maxSize": 1})
        r.raise_for_status()
        lst = r.json().get("list", [])
        return lst[0]["id"] if lst else None

    async def upsert_business(self, biz: Business, snap: dict[str, Any],
                              crm_id: str | None = None) -> str:
        body = {
            "name": biz.canonical_name, "website": biz.website_url, "phoneNumber": biz.phone_e164,
            "billingAddressStreet": biz.address.line1, "billingAddressCity": biz.address.city,
            "billingAddressState": biz.address.state,
            "billingAddressPostalCode": biz.address.postcode,
            "industry": biz.trade, "sicCode": str(biz.id),  # stable external key
            "description": (f"LeadBeast class {snap.get('class')} score {snap.get('total')} "
                            f"({snap.get('confidence')}). Gaps: {snap.get('gap_types')}"),
        }
        crm_id = crm_id or await self._find("Account", "sicCode", str(biz.id))
        if crm_id:
            (await self.c.put(f"{self.base}/Account/{crm_id}", json=body)).raise_for_status()
            return crm_id
        r = await self.c.post(f"{self.base}/Account", json=body)
        r.raise_for_status()
        return str(r.json()["id"])

    async def upsert_contact(self, c: Contact, crm_biz_id: str) -> str:
        first, _, last = (c.name or "").partition(" ")
        body = {"firstName": first or None, "lastName": last or (c.role or "Contact"),
                "emailAddress": c.email, "phoneNumber": c.phone_e164, "accountId": crm_biz_id,
                "title": c.role}
        existing = await self._find("Contact", "emailAddress", c.email) if c.email else None
        if existing:
            (await self.c.put(f"{self.base}/Contact/{existing}", json=body)).raise_for_status()
            return existing
        r = await self.c.post(f"{self.base}/Contact", json=body)
        r.raise_for_status()
        return str(r.json()["id"])

    async def log_activity(self, crm_id: str, msg: dict[str, Any]) -> None:
        body = {"post": msg.get("body", "")[:4000], "parentType": "Account", "parentId": crm_id,
                "type": "Post"}
        (await self.c.post(f"{self.base}/Note", json=body)).raise_for_status()

    async def set_stage(self, crm_id: str, stage: str) -> None:
        r = await self.c.put(f"{self.base}/Account/{crm_id}",
                             json={"description": f"Stage: {STAGES.get(stage, stage)}"})
        r.raise_for_status()
