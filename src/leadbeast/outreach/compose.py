"""Fill a pack template with cited, golden-qualified findings only. Pure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Composed:
    subject: str
    body: str
    cited_evidence_ids: list[str]
    cited_signals: list[str]


class _Safe(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def compose(
    template: str, subject_tpl: str, *, business_name: str, trade: str | None, city: str | None,
    contact_name: str | None, findings: list[dict[str, Any]], operator_name: str,
    operator_phone: str, max_findings: int = 3,
) -> Composed:
    chosen = [f for f in findings if f.get("evidence_id")][:max_findings]
    lines = "\n".join(f"- {f['statement']}" for f in chosen)
    first = (contact_name or "").split()[0] if contact_name else ""
    vals = _Safe(
        business_name=business_name, trade=(trade or "local business").replace("_", " "),
        city=city or "your area", contact_greeting=first or "there", findings=lines,
        operator_name=operator_name, operator_phone=operator_phone,
    )
    return Composed(
        subject=subject_tpl.format_map(vals), body=template.format_map(vals).strip(),
        cited_evidence_ids=[f["evidence_id"] for f in chosen],
        cited_signals=[f["signal"] for f in chosen],
    )


def footer(operator_name: str, operator_address: str, unsubscribe_url: str) -> str:
    return (f"\n\n--\n{operator_name}\n{operator_address}\n"
            f"Not interested? Reply STOP or unsubscribe: {unsubscribe_url}")
