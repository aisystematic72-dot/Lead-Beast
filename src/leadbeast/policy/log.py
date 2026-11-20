"""Every policy decision is logged with its inputs so a denial can be explained."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.clock import now
from leadbeast.db import models as m
from leadbeast.policy.engine import Action, Decision, PolicyContext


def _jsonable(ctx: PolicyContext) -> dict[str, Any]:
    d = asdict(ctx)
    d["body"] = (ctx.body[:200] + "...") if len(ctx.body) > 200 else ctx.body
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


async def record(
    s: AsyncSession, action: Action, ctx: PolicyContext, decision: Decision, actor: str | None
) -> None:
    s.add(m.PolicyDecisionRow(
        action=action, decision=decision.verdict.value, reasons=list(decision.reasons),
        inputs=_jsonable(ctx), actor=actor, at=now(),
    ))
