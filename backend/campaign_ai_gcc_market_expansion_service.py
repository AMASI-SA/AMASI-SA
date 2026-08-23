"""Store Profit Manager orchestration for GCC expansion evidence and planning."""
from __future__ import annotations

from datetime import date
from typing import Any

from campaign_ai_gcc_market_evidence import load_gcc_market_evidence
from campaign_ai_gcc_market_expansion_planner import refresh_gcc_market_expansion_plan

CONTRACT_VERSION = "gcc_market_expansion_service_v1"


async def refresh_gcc_market_expansion_from_sources(
    db: Any,
    user_id: str,
    *,
    as_of: date,
    opportunity_plan: dict[str, Any],
    observed_days: int = 30,
) -> dict[str, Any]:
    """Refresh one daily GCC plan from tenant-scoped governed evidence."""
    evidence = await load_gcc_market_evidence(
        db,
        user_id,
        observed_days=observed_days,
    )
    return await refresh_gcc_market_expansion_plan(
        db,
        user_id,
        as_of=as_of,
        opportunity_plan=opportunity_plan,
        market_evidence=evidence,
    )


__all__ = [
    "CONTRACT_VERSION",
    "refresh_gcc_market_expansion_from_sources",
]
