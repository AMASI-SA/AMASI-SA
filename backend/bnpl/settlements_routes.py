"""HTTP routes for BNPL Automatic Settlements — Phase 4."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from .settlements_service import (
    compute_all_settlements,
    compute_settlement_for_provider,
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

    parent_router.include_router(router)
