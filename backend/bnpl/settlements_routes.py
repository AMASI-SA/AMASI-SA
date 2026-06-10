"""HTTP routes for BNPL Automatic Settlements — Phase 4."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from .settlements_service import (
    compute_all_settlements,
    compute_settlement_for_provider,
    compute_weekly_settlements,
    PROVIDERS,
)


def attach_bnpl_settlements_routes(parent_router, *, db, get_current_user):
    router = APIRouter(prefix="/bnpl/settlements", tags=["BNPL Settlements"])

    @router.get("/summary")
    async def settlements_summary(
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """One call → full BNPL settlement breakdown for both providers
        plus global totals.  Optional date filter (YYYY-MM-DD).
        Wrapped in try/except so Cloudflare can't 524 us."""
        try:
            return {
                "success": True,
                **(await compute_all_settlements(db, user["id"], from_date, to_date)),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/{provider}")
    async def provider_settlement(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """Settlement for a single provider — used by the per-provider
        detail drawer on the frontend."""
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            return {
                "success": True,
                **(await compute_settlement_for_provider(
                    db, user["id"], provider, from_date, to_date,
                )),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/weekly/{provider}")
    async def weekly_settlements(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """List of weekly settlements for ONE provider (one row per
        weekly invoice).  Default range = activation_date → today."""
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            rows = await compute_weekly_settlements(
                db, user["id"], provider, from_date, to_date,
            )
            totals = {
                k: round(sum(r[k] for r in rows), 2)
                for k in ("gross_sales", "total_refunds", "net_sales",
                         "commission", "commission_vat", "settlement_fee",
                         "net_payable", "transferred_amount",
                         "remaining_with_provider")
            }
            totals["invoices_count"] = len(rows)
            return {
                "success": True,
                "provider": provider,
                "rows": rows,
                "totals": totals,
                "range": {
                    "from": (rows[0]["from"] if rows else from_date),
                    "to":   (rows[-1]["to"] if rows else to_date),
                },
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/balances/canonical")
    async def canonical_balances(user: dict = Depends(get_current_user)):
        """Single Source of Truth for Tabby + Tamara balances.  Every
        page that shows a BNPL balance should call this endpoint so
        all pages agree on the number."""
        try:
            from .balance_service import get_all_bnpl_balances
            balances = await get_all_bnpl_balances(db, user["id"])
            return {
                "success": True,
                "balances": balances,
                "total": round(sum(float(b["balance"] or 0) for b in balances), 2),
                "formula_doc": (
                    "balance = gross_sales − refunds − commission − VAT "
                    "− settlement_fee − transferred_to_bank"
                ),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    parent_router.include_router(router)
