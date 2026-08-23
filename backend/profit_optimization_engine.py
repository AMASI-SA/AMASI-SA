"""Recommendation-only profit optimization foundation."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProfitSignal:
    revenue: float
    product_cost: float
    ad_cost: float
    shipping_cost: float = 0.0
    fees: float = 0.0

    def net_profit(self) -> float:
        return self.revenue - self.product_cost - self.ad_cost - self.shipping_cost - self.fees


def optimize_recommendation(signal: ProfitSignal) -> dict:
    return {
        "net_profit": signal.net_profit(),
        "action": "review_only",
        "auto_execution": False,
    }
