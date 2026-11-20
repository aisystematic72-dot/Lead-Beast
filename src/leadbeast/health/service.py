"""Connector health (M22). Every adapter implements probe(); failures open the circuit."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.core.ports import HealthCheck, Status
from leadbeast.db import models as m
from leadbeast.evidence.ledger import emit

FAILURES_TO_OPEN = 3


def next_circuit(current: str, consecutive_failures: int, probe_ok: bool) -> tuple[str, int]:
    """Pure circuit-breaker transition: closed -> open -> half_open -> closed."""
    if probe_ok:
        return ("closed", 0) if current in ("half_open", "closed") else ("half_open", 0)
    fails = consecutive_failures + 1
    if current == "half_open" or fails >= FAILURES_TO_OPEN:
        return "open", fails
    return current, fails


def adapter_for(conn: m.Connector) -> HealthCheck | None:
    name = conn.name
    if name == "osm":
        from leadbeast.sources.osm import OsmAdapter

        return OsmAdapter()
    if name == "ollama":
        from leadbeast.ai.ollama import OllamaClient

        return OllamaClient()
    if name == "espocrm":
        from leadbeast.crm.espocrm import EspoCrmAdapter

        return EspoCrmAdapter()
    if name in ("smtp", "ses"):
        from leadbeast.outreach.channels import SmtpChannel

        return SmtpChannel()
    if name == "dns_auth":
        from leadbeast.policy.deliverability import DnsAuthProbe

        return DnsAuthProbe()
    return None


async def probe_connector(s: AsyncSession, conn: m.Connector) -> Status:
    adapter = adapter_for(conn)
    if adapter is None:
        st = Status("ok", detail="no remote probe (local adapter)")
    else:
        try:
            st = await adapter.probe()
        except Exception as e:  # a probe must never crash the health job
            st = Status("off", detail=repr(e))
    s.add(m.ConnectorHealth(connector_id=conn.id, checked_at=now(), status=st.status,
                            latency_ms=st.latency_ms, detail=st.detail[:500]))
    circuit, fails = next_circuit(conn.circuit, conn.consecutive_failures, st.status != "off")
    if circuit != conn.circuit:
        await emit(s, "CircuitChanged", None, {"connector": conn.name, "to": circuit})
    conn.circuit, conn.consecutive_failures = circuit, fails
    return st


async def probe_all(s: AsyncSession) -> dict[str, Status]:
    out: dict[str, Status] = {}
    for conn in (await s.scalars(select(m.Connector).where(m.Connector.enabled.is_(True)))).all():
        out[conn.name] = await probe_connector(s, conn)
    return out


DEFAULT_CONNECTORS = [
    ("source", "osm", 0), ("source", "csv", 0), ("contact", "site_contacts", 0),
    ("model", "ollama", 0), ("crm", "espocrm", 0), ("channel", "smtp", 0),
    ("channel", "dns_auth", 0), ("contact", "contact_finder", 0),
]


async def seed_connectors(s: AsyncSession) -> int:
    n = 0
    for kind, name, cap in DEFAULT_CONNECTORS:
        if await s.scalar(select(m.Connector).where(m.Connector.name == name)) is None:
            s.add(m.Connector(kind=kind, name=name, monthly_cap=cap, config={}))
            n += 1
    return n
