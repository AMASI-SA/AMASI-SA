"""Iter-250a — Financial Pages Inventory route (READ-ONLY).

  GET /api/audit/financial-pages-inventory
       [?classification=KEEP|MERGE|DEPRECATE|DELETE]
       [?area=banks_and_accounts|bnpl|...]
       [?risk=HIGH|MEDIUM|LOW]

Returns the full inventory + aggregated summary. Pure read of an
in-process constant list.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ai_analysis_routes import make_ai_analysis_router
from financial_pages_inventory_data import INVENTORY, summary


def make_financial_pages_inventory_router(current_user):
    router = APIRouter(tags=["audit", "inventory"])

    @router.get("/audit/financial-pages-inventory")
    async def get_inventory(
        classification: Optional[str] = Query(
            None,
            description="Filter by KEEP/MERGE/DEPRECATE/DELETE"),
        area: Optional[str] = Query(
            None, description="Filter by domain area"),
        risk: Optional[str] = Query(
            None, description="Filter by LOW/MEDIUM/HIGH"),
        user: dict = Depends(current_user),
    ):
        rows = INVENTORY
        if classification:
            rows = [r for r in rows
                    if r["classification"] == classification.upper()]
        if area:
            rows = [r for r in rows if r["area"] == area]
        if risk:
            rows = [r for r in rows if r["risk"] == risk.upper()]
        return {
            "ok": True,
            "iter": "iter250a",
            "read_only": True,
            "summary": summary(),
            "rows": rows,
            "row_count": len(rows),
        }

    router.include_router(make_ai_analysis_router(current_user))
    return router
