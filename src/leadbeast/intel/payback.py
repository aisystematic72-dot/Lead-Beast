"""Payback rule (rule 9): one extra customer a month must be worth >= 2x the monthly fee.

Always a range, never a point; provisional until buyer interviews confirm the inputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from leadbeast.packs.schema import Offer

MIN_MULTIPLE = 2.0


@dataclass(frozen=True)
class PaybackRange:
    monthly_fee: float
    value_low: float
    value_high: float
    multiple_low: float
    multiple_high: float
    verdict: str  # pass | marginal | fail
    provisional: bool
    customers_to_break_even_low: float
    customers_to_break_even_high: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def payback(offer: Offer) -> PaybackRange:
    fee = offer.monthly_fee
    lo, hi = offer.customer_value_low / fee, offer.customer_value_high / fee
    if lo >= MIN_MULTIPLE:
        verdict = "pass"
    elif hi >= MIN_MULTIPLE:
        verdict = "marginal"
    else:
        verdict = "fail"
    return PaybackRange(
        monthly_fee=fee, value_low=offer.customer_value_low, value_high=offer.customer_value_high,
        multiple_low=round(lo, 2), multiple_high=round(hi, 2), verdict=verdict,
        provisional=offer.provisional,
        customers_to_break_even_low=round(fee / offer.customer_value_high, 2)
        if offer.customer_value_high else float("inf"),
        customers_to_break_even_high=round(fee / offer.customer_value_low, 2)
        if offer.customer_value_low else float("inf"),
    )
