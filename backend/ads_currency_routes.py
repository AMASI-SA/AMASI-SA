"""Iter-236 — Ads currency & bank-commission settings.

Endpoints
─────────
GET    /api/ads-currency-settings
       → { usd_to_sar_rate, bank_commission_pct,
           bank_fees_expense_account_id }

PUT    /api/ads-currency-settings
       Body: { usd_to_sar_rate?, bank_commission_pct? }
       → persists settings + auto-creates the
         "رسوم بنكية وعمولات بطاقات" expense account on first save.

PUT    /api/ads-currency-settings/account/{counterparty_id}
       Body: { currency: "USD"|"SAR", apply_bank_commission: bool }
       → saves currency + commission flag on the ad-account counterparty.

GET    /api/ads-currency-settings/summary
       Query: ?date_from=&date_to=&counterparty_id=
       → { total_ads_spend, total_bank_fees, total_due }

GET    /api/ads-currency-settings/preview
       Query: ?original_amount=&currency=&apply_bank_commission=
       → preview the SAR + bank fee calculation BEFORE creating the bill.

Data model
──────────
collection `ads_currency_settings` (one doc per user_id):
  { user_id, usd_to_sar_rate, bank_commission_pct,
    bank_fees_expense_account_id, updated_at }

collection `counterparties` (extended fields on ad_account kind):
  { ..., currency: "USD"|"SAR", apply_bank_commission: bool }

collection `liabilities` (snapshot fields on ad_account kind):
  { ..., original_amount, original_currency, exchange_rate_used,
        bank_commission_pct_used, bank_commission_amount }
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db

BANK_FEES_EXPENSE_NAME = "رسوم بنكية وعمولات بطاقات"
DEFAULT_USD_TO_SAR = 3.7544
DEFAULT_BANK_COMMISSION_PCT = 2.30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _r(v: float) -> float:
    return round(float(v or 0), 2)


class AdsCurrencySettingsIn(BaseModel):
    usd_to_sar_rate: Optional[float] = Field(None, gt=0, le=20)
    bank_commission_pct: Optional[float] = Field(None, ge=0, le=20)


class AdAccountConfigIn(BaseModel):
    currency: Literal["USD", "SAR"]
    apply_bank_commission: bool = True


def compute_ads_amounts(
    *,
    original_amount: float,
    currency: str,
    usd_to_sar_rate: float,
    bank_commission_pct: float,
    apply_bank_commission: bool,
) -> dict:
    """Pure calculation — no DB.  Used by both `/preview` AND by the
    liabilities create-flow so they agree to the cent."""
    amt = float(original_amount or 0)
    if amt <= 0:
        return {
            "original_amount": 0.0, "original_currency": currency,
            "sar_amount": 0.0, "exchange_rate_used": 0.0,
            "bank_commission_pct_used": 0.0,
            "bank_commission_amount": 0.0, "total_due_sar": 0.0,
        }
    if currency == "USD":
        rate = float(usd_to_sar_rate or 0)
        sar_amount = amt * rate
    else:
        rate = 0.0    # not applied for SAR accounts
        sar_amount = amt
    if apply_bank_commission:
        pct = float(bank_commission_pct or 0)
        bank_fee = sar_amount * (pct / 100.0)
    else:
        pct = 0.0
        bank_fee = 0.0
    return {
        "original_amount": _r(amt),
        "original_currency": currency,
        "sar_amount": _r(sar_amount),
        "exchange_rate_used": float(rate) if currency == "USD" else 0.0,
        "bank_commission_pct_used": float(pct),
        "bank_commission_amount": _r(bank_fee),
        "total_due_sar": _r(sar_amount + bank_fee),
    }


def attach_ads_currency_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/ads-currency-settings",
                       tags=["ads-currency"])

    async def _ensure_bank_fees_account(uid: str) -> str:
        """Auto-create the «رسوم بنكية وعمولات بطاقات» expense account
        on the first settings save. Returns its id."""
        existing = await db.expense_accounts.find_one(
            {"user_id": uid, "name": BANK_FEES_EXPENSE_NAME},
            {"_id": 0, "id": 1},
        )
        if existing:
            return existing["id"]
        eid = str(uuid.uuid4())
        await db.expense_accounts.insert_one({
            "id": eid,
            "user_id": uid,
            "name": BANK_FEES_EXPENSE_NAME,
            "category": "operating",
            "auto_generated": True,
            "created_at": _now(),
            "updated_at": _now(),
            "iter": "iter236",
        })
        return eid

    async def _load_settings(uid: str) -> dict:
        doc = await db.ads_currency_settings.find_one(
            {"user_id": uid}, {"_id": 0},
        )
        if not doc:
            doc = {
                "user_id": uid,
                "usd_to_sar_rate": DEFAULT_USD_TO_SAR,
                "bank_commission_pct": DEFAULT_BANK_COMMISSION_PCT,
                "bank_fees_expense_account_id": None,
            }
        return doc

    @router.get("")
    async def get_settings(user: dict = Depends(current_user)):
        return await _load_settings(user["id"])

    @router.put("")
    async def update_settings(payload: AdsCurrencySettingsIn,
                              user: dict = Depends(current_user)):
        uid = user["id"]
        cur = await _load_settings(uid)
        # Ensure bank-fees expense account exists once settings are
        # persisted (so journal posting always has a target).
        bank_fees_id = (cur.get("bank_fees_expense_account_id")
                        or await _ensure_bank_fees_account(uid))
        rate = (payload.usd_to_sar_rate
                if payload.usd_to_sar_rate is not None
                else cur.get("usd_to_sar_rate", DEFAULT_USD_TO_SAR))
        pct = (payload.bank_commission_pct
               if payload.bank_commission_pct is not None
               else cur.get("bank_commission_pct",
                            DEFAULT_BANK_COMMISSION_PCT))
        await db.ads_currency_settings.update_one(
            {"user_id": uid},
            {"$set": {
                "user_id": uid,
                "usd_to_sar_rate": float(rate),
                "bank_commission_pct": float(pct),
                "bank_fees_expense_account_id": bank_fees_id,
                "updated_at": _now(),
            }},
            upsert=True,
        )
        return await _load_settings(uid)

    @router.put("/account/{counterparty_id}")
    async def update_account_config(counterparty_id: str,
                                    payload: AdAccountConfigIn,
                                    user: dict = Depends(current_user)):
        uid = user["id"]
        cp = await db.counterparties.find_one(
            {"id": counterparty_id, "user_id": uid,
             "kind": "ad_account"},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not cp:
            raise HTTPException(404, "ad account not found")
        await db.counterparties.update_one(
            {"id": counterparty_id, "user_id": uid},
            {"$set": {
                "currency": payload.currency,
                "apply_bank_commission": bool(payload.apply_bank_commission),
                "updated_at": _now(),
            }},
        )
        return {
            "success": True,
            "counterparty_id": counterparty_id,
            "name": cp.get("name"),
            "currency": payload.currency,
            "apply_bank_commission": bool(payload.apply_bank_commission),
        }

    @router.get("/preview")
    async def preview(
        original_amount: float = Query(..., gt=0),
        currency: Literal["USD", "SAR"] = Query(...),
        apply_bank_commission: bool = Query(True),
        user: dict = Depends(current_user),
    ):
        s = await _load_settings(user["id"])
        return compute_ads_amounts(
            original_amount=original_amount,
            currency=currency,
            usd_to_sar_rate=float(s.get("usd_to_sar_rate") or 0),
            bank_commission_pct=float(s.get("bank_commission_pct") or 0),
            apply_bank_commission=apply_bank_commission,
        )

    @router.get("/summary")
    async def summary(
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        counterparty_id: Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        """Aggregate snapshot fields across all ad_account liabilities
        for the period. Read-only.  Falls back to expected_amount when
        snapshot fields are missing (legacy bills).
        """
        uid = user["id"]
        q: dict = {"user_id": uid, "kind": "ad_account"}
        if counterparty_id:
            q["counterparty_id"] = counterparty_id
        if date_from or date_to:
            q["due_date"] = {}
            if date_from:
                q["due_date"]["$gte"] = date_from
            if date_to:
                q["due_date"]["$lte"] = date_to
        total_ads_spend = 0.0
        total_bank_fees = 0.0
        total_due = 0.0
        count = 0
        async for r in db.liabilities.find(q, {
            "_id": 0,
            "expected_amount": 1,
            "bank_commission_amount": 1,
            "sar_amount": 1,
        }):
            count += 1
            sar = float(r.get("sar_amount") or 0)
            fee = float(r.get("bank_commission_amount") or 0)
            if sar > 0:
                total_ads_spend += sar
                total_bank_fees += fee
                total_due += sar + fee
            else:
                # Legacy row without snapshot — count expected_amount as ads spend.
                amt = float(r.get("expected_amount") or 0)
                total_ads_spend += amt
                total_due += amt
        return {
            "count": count,
            "total_ads_spend": _r(total_ads_spend),
            "total_bank_fees": _r(total_bank_fees),
            "total_due": _r(total_due),
            "filters": {
                "date_from": date_from,
                "date_to": date_to,
                "counterparty_id": counterparty_id,
            },
        }

    parent_router.include_router(router)
