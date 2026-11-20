"""Plain CSV 'CRM': appends rows to a file. Useful before a real CRM is set up."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from leadbeast.core.ports import Status
from leadbeast.domain.business import Business, Contact


class CsvCrmAdapter:
    name = "csv"

    def __init__(self, path: str | Path = "var/crm_export.csv"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def probe(self) -> Status:
        return Status("ok")

    def _write(self, row: dict[str, Any]) -> None:
        new = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    async def upsert_business(self, biz: Business, snap: dict[str, Any],
                              crm_id: str | None = None) -> str:
        self._write({"kind": "business", "id": str(biz.id), "name": biz.canonical_name,
                     "phone": biz.phone_e164 or "", "website": biz.website_url or "",
                     "class": snap.get("class"), "total": snap.get("total")})
        return str(biz.id)

    async def upsert_contact(self, c: Contact, crm_biz_id: str) -> str:
        self._write({"kind": "contact", "id": crm_biz_id, "name": c.name or "",
                     "phone": c.phone_e164 or "", "website": c.email or "", "class": c.role,
                     "total": ""})
        return f"{crm_biz_id}:{c.email or c.phone_e164}"

    async def log_activity(self, crm_id: str, msg: dict[str, Any]) -> None:
        return None

    async def set_stage(self, crm_id: str, stage: str) -> None:
        return None
