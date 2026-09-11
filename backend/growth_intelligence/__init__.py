"""Growth Intelligence domain for market, season and product discovery.

This package is intentionally separate from Campaign AI. It produces governed
market context and opportunity objects that Campaign AI may consume as evidence;
it does not run inside the five-hour advertising decision cadence and does not
perform supplier orders, Salla writes, or Ads API writes.
"""

from .schemas import (
    GCCCountryOpportunity,
    GrowthIntelligenceSnapshot,
    LiquidityEvent,
    ProductDiscoveryCandidate,
    SeasonalOpportunity,
)

__all__ = [
    "GCCCountryOpportunity",
    "GrowthIntelligenceSnapshot",
    "LiquidityEvent",
    "ProductDiscoveryCandidate",
    "SeasonalOpportunity",
]
