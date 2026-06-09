"""BNPL FastAPI routes (Iter-116).

  GET  /api/bnpl/settings                          — list both providers
  GET  /api/bnpl/settings/{provider}               — masked config
  PUT  /api/bnpl/settings/{provider}               — upsert config (encrypted)
  POST /api/bnpl/{provider}/test-connection        — validates credentials
  POST /api/bnpl/tabby/sync                        — pull payments from
                                                     activation_date (or
                                                     ?since=YYYY-MM-DD for
                                                     manual backfill).
  GET  /api/bnpl/{provider}/transactions           — list local data
  GET  /api/bnpl/{provider}/refunds                — list local data
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import get_current_user_from_db

from .clients.tabby import TabbyClient, TabbyError
from .clients.tamara import TamaraClient, TamaraError
from .config_store import (
    BNPL_PROVIDERS, DEFAULTS,
    ensure_indexes as ensure_settings_indexes,
    get_raw_secrets, get_settings, record_test_result, save_settings,
)
from .sync_service import ensure_sync_indexes, sync_tabby_payments


async def ensure_bnpl_indexes(db) -> None:
    await ensure_settings_indexes(db)
    await ensure_sync_indexes(db)


def attach_bnpl_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/bnpl", tags=["bnpl"])

    # ── SETTINGS ───────────────────────────────────────────────
    @router.get("/settings")
    async def list_all_settings(user: dict = Depends(current_user)):
        out = {}
        for p in BNPL_PROVIDERS:
            out[p] = await get_settings(db, user["id"], p)
        return {"providers": out, "defaults": DEFAULTS}

    @router.get("/settings/{provider}")
    async def get_provider_settings(
        provider: str, user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        return await get_settings(db, user["id"], provider)

    @router.put("/settings/{provider}")
    async def update_provider_settings(
        provider: str, payload: dict, user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        try:
            return await save_settings(db, user["id"], provider, payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    # ── TEST CONNECTION ────────────────────────────────────────
    @router.post("/{provider}/test-connection")
    async def test_connection(
        provider: str, user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        secrets = await get_raw_secrets(db, user["id"], provider)
        try:
            if provider == "tabby":
                if not secrets.get("secret_key"):
                    raise HTTPException(400, "Tabby secret_key not set")
                cli = TabbyClient(
                    secret_key=secrets["secret_key"],
                    merchant_code=secrets.get("merchant_code") or "",
                    base_url=secrets.get("api_base_url") or "https://api.tabby.sa",
                )
                res = await cli.test_connection()
            else:
                if not secrets.get("api_token"):
                    raise HTTPException(400, "Tamara api_token not set")
                cli = TamaraClient(
                    api_token=secrets["api_token"],
                    base_url=secrets.get("api_base_url") or "https://api.tamara.co",
                )
                res = await cli.test_connection()
        except (TabbyError, TamaraError) as exc:
            await record_test_result(db, user["id"], provider, False, str(exc))
            raise HTTPException(400, str(exc))
        await record_test_result(db, user["id"], provider, True, None)
        return {"ok": True, "provider": provider, "detail": res}

    # ── SYNC (Tabby) ───────────────────────────────────────────
    @router.post("/tabby/sync")
    async def tabby_sync(
        since: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="Optional backfill date YYYY-MM-DD (overrides "
                        "activation_date for this call only).",
        ),
        user: dict = Depends(current_user),
    ):
        since_iso = f"{since}T00:00:00Z" if since else None
        res = await sync_tabby_payments(
            db, user["id"], since_iso=since_iso,
        )
        if not res.get("ok"):
            raise HTTPException(400, res.get("error") or "sync failed")
        return res

    # ── LIST LOCAL DATA ────────────────────────────────────────
    @router.get("/{provider}/transactions")
    async def list_transactions(
        provider: str,
        from_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(200, ge=1, le=2000),
        user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        q = {"user_id": user["id"], "provider": provider}
        if from_date or to_date:
            d = {}
            if from_date:
                d["$gte"] = from_date
            if to_date:
                d["$lte"] = to_date + "T23:59:59Z"
            q["created_at_provider"] = d
        rows = await (
            db.payment_transactions.find(q, {"_id": 0, "raw_payload": 0})
            .sort([("created_at_provider", -1)])
            .limit(limit).to_list(limit)
        )
        return {"items": rows, "count": len(rows)}

    @router.get("/{provider}/refunds")
    async def list_refunds(
        provider: str,
        limit: int = Query(200, ge=1, le=2000),
        user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        rows = await (
            db.payment_refunds.find(
                {"user_id": user["id"], "provider": provider},
                {"_id": 0, "raw": 0},
            )
            .sort([("refunded_at", -1)])
            .limit(limit).to_list(limit)
        )
        return {"items": rows, "count": len(rows)}

    parent_router.include_router(router)
