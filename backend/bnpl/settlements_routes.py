"""HTTP routes for BNPL Automatic Settlements — Phase 4."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from .settlements_service import (
    compute_all_settlements,
    compute_settlement_for_provider,
    compute_weekly_settlements,
    _compute_period_items,
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

    @router.get("/items/{provider}")
    async def settlement_items(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: str = Query(..., alias="from",
                               pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: str = Query(..., alias="to",
                             pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """Iter-120 — return the raw sales + refund items inside a
        single settlement period.  Powers the two detail tables shown
        when the merchant expands a weekly settlement row.

        IMPORTANT ACCOUNTING RULE:
          • Sales:  orders whose ORDER DATE  ∈ [from, to].
          • Refunds: refunds whose REFUND DATE ∈ [from, to] — regardless
            of when the original order was placed.  Each refund row is
            enriched with its original order's date and amount so the
            merchant can see when the refund crosses period boundaries.
        """
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            items = await _compute_period_items(
                db, user["id"], provider, from_date, to_date,
            )
            return {
                "success":     True,
                "provider":    provider,
                "period":      {"from": from_date, "to": to_date},
                "sales":       items["sales"],
                "refunds":     items["refunds"],
                "sales_total": round(
                    sum(s["amount"] for s in items["sales"]), 2,
                ),
                "refunds_total": round(
                    sum(r["refund_amount"] for r in items["refunds"]), 2,
                ),
                "cross_period_refunds_count": sum(
                    1 for r in items["refunds"]
                    if (r.get("order_date") or "")[:10] < from_date
                ),
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
                k: round(sum(r.get(k, 0) for r in rows), 2)
                for k in ("gross_sales", "total_refunds", "net_sales",
                         "commission", "commission_vat", "settlement_fee",
                         "settlement_fee_vat",   # Iter-134
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

    @router.get("/matching/{provider}")
    async def matching_for_provider(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """Phase 4-B — auto-match weekly invoices with bank transfers.

        Returns each invoice's match status (`matched` / `unmatched`
        / `over` / `under`) plus the list of leftover transfers that
        the system could not assign to any invoice.  Read-only — no
        DB writes."""
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            from .matching_service import compute_matches_for_provider
            return {
                "success": True,
                **(await compute_matches_for_provider(
                    db, user["id"], provider, from_date, to_date,
                )),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    parent_router.include_router(router)
