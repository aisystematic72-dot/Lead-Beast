from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Address(BaseModel):
    line1: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str = "US"


class SourceRecord(BaseModel):
    id: UUID | None = None
    source: str
    external_id: str
    fetched_at: datetime
    payload: dict[str, Any]
    business_id: UUID | None = None

    # normalised view used by identity resolution
    name: str | None = None
    phone: str | None = None
    website: str | None = None
    address: Address = Field(default_factory=Address)
    lat: float | None = None
    lon: float | None = None
    trade: str | None = None


class Business(BaseModel):
    id: UUID
    canonical_name: str
    phone_e164: str | None = None
    domain: str | None = None
    website_url: str | None = None
    address: Address = Field(default_factory=Address)
    lat: float | None = None
    lon: float | None = None
    trade: str | None = None
    size_band: str | None = None
    status: str = "active"


class Contact(BaseModel):
    id: UUID | None = None
    business_id: UUID
    name: str | None = None
    role: str | None = None
    email: str | None = None
    phone_e164: str | None = None
    channel_pref: str = "email"
    confidence: str = "low"
    source_url: str | None = None
    is_webmail: bool = False
    is_role_address: bool = False
