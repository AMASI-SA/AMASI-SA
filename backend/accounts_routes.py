"""Financial Accounts & Transactions (الأصول والحسابات المالية)
================================================================
Iter-57 Phase 1 — foundation layer for the upcoming accounting system.

This module introduces TWO new collections that future iterations (payroll,
debt tracking, ads spend reconciliation, internal transfers, BNPL/Salla
settlement matching) will all hang off:

  • `accounts`              — every "wallet" the merchant owns: banks,
    payment platforms (Salla/Tamara/Tabby/…), and ad-platform balances.
  • `account_transactions`  — every monetary event that moves balance in
    or out of an account.

Design principles
-----------------
- Each transaction stores `balance_after` at write time — so historical
  ledgers stay correct even if older transactions are edited later. We
  recompute the trail on edit/delete to keep `current_balance` honest.
- `current_balance` lives on the account doc to avoid summing the whole
  ledger on every dashboard read.
- Creating an account with a non-zero `opening_balance` auto-generates an
  "opening_balance" transaction so the audit trail is complete from day 1.
- Deletion is allowed only when the account has exactly ONE transaction
  (the opening one) — anything richer must be hidden, not removed.
"""

from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator

from auth import get_current_user_from_db


# ── Catalogue ──────────────────────────────────────────────────────────────
ACCOUNT_TYPES = ("bank", "payment_platform", "ads_platform")

ACCOUNT_TYPE_LABELS = {
    "bank":             "حساب بنكي",
    "payment_platform": "منصة دفع",
    "ads_platform":     "حساب إعلاني",
}

# Curated suggestions for the "Add account" modal — the user can still type
# anything for the provider name; these just power the dropdown.
SUGGESTED_PROVIDERS = {
    "bank": [
        "بنك الراجحي", "بنك الأهلي", "بنك الإنماء", "بنك ساب",
        "بنك الرياض", "بنك البلاد", "بنك الجزيرة",
    ],
    "payment_platform": [
        "سلة", "تابي", "تمارا", "إمكان",
        "STC Pay", "Apple Pay", "مدى", "Visa", "MasterCard",
    ],
    "ads_platform": ["Snapchat Ads", "TikTok Ads", "Meta Ads", "Google Ads"],
}

ACCOUNT_STATUSES = ("active", "hidden", "inactive")

# ── Payment-method normalisation (single source of truth in payment_methods.py)
from payment_methods import (
    PAYMENT_ALIASES as _PAYMENT_ALIASES,
    PARENT_LABELS as _PARENT_LABELS,
    normalize_payment_method,
)


# Movement vocabulary — kept intentionally small for iter-57; phase 2 will
# add `transfer_in/out`, `debt_paid`, …
TRANSACTION_TYPES = (
    "opening_balance",
    "income",
    "expense",
    "internal_transfer",
    "settlement",
    "debt",
    "debt_payment",
    "manual_adjustment",
)

TRANSACTION_TYPE_LABELS = {
    "opening_balance":   "رصيد افتتاحي",
    "income":            "دخل",
    "expense":           "مصروف",
    "internal_transfer": "تحويل داخلي",
    "settlement":        "تسوية",
    "debt":              "دين",
    "debt_payment":      "سداد دين",
    "manual_adjustment": "تسوية يدوية",
}

TRANSACTION_STATUSES = ("posted", "pending", "reconciled")


# ── Pydantic ───────────────────────────────────────────────────────────────
class AccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    account_type: str
    provider_name: Optional[str] = Field(None, max_length=80)
    currency: str = Field("SAR", min_length=2, max_length=8)
    opening_balance: float = 0.0
    opening_balance_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    default_bank_account_id: Optional[str] = None
    notes: Optional[str] = Field("", max_length=500)

    @validator("account_type")
    def _t(cls, v):
        if v not in ACCOUNT_TYPES:
            raise ValueError(f"account_type must be one of {ACCOUNT_TYPES}")
        return v


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    provider_name: Optional[str] = Field(None, max_length=80)
    currency: Optional[str] = Field(None, min_length=2, max_length=8)
    status: Optional[str] = None
    default_bank_account_id: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=500)

    @validator("status")
    def _s(cls, v):
        if v is not None and v not in ACCOUNT_STATUSES:
            raise ValueError(f"status must be one of {ACCOUNT_STATUSES}")
        return v


class TransactionIn(BaseModel):
    transaction_type: str
    amount: float = Field(..., gt=0)
    direction: Literal["in", "out"]
    description: Optional[str] = Field("", max_length=500)
    transaction_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: str = "posted"
    attachment_url: Optional[str] = None

    @validator("transaction_type")
    def _t(cls, v):
        if v not in TRANSACTION_TYPES:
            raise ValueError(f"transaction_type must be one of {TRANSACTION_TYPES}")
        return v

    @validator("status")
    def _s(cls, v):
        if v not in TRANSACTION_STATUSES:
            raise ValueError(f"status must be one of {TRANSACTION_STATUSES}")
        return v


# ── Helpers ────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


async def _recompute_balance(db, user_id: str, account_id: str) -> float:
    """Walk the account's transactions in chronological order, rewriting each
    row's `balance_after`. Returns the final balance which is then stored on
    the account doc as `current_balance`. O(n) but n is small (<10k usually);
    we accept that for correctness.

    For accounts auto-created from order payment_methods, the running ledger
    starts from `expected_orders_balance` (the gross order amount) so adding
    a real bank-transfer transaction (direction="out") deducts from it.
    """
    acc = await db.accounts.find_one(
        {"id": account_id, "user_id": user_id},
        {"_id": 0, "expected_orders_balance": 1, "opening_balance": 1},
    ) or {}
    expected = float(acc.get("expected_orders_balance") or 0)

    docs = await db.account_transactions.find(
        {"user_id": user_id, "account_id": account_id},
        {"_id": 0},
    ).sort([("transaction_date", 1), ("created_at", 1)]).to_list(50000)

    running = expected  # auto-orders balance lives at the bottom of the stack
    for d in docs:
        amt = float(d.get("amount", 0) or 0)
        running += amt if d.get("direction") == "in" else -amt
        new_balance = round(running, 2)
        if d.get("balance_after") != new_balance:
            await db.account_transactions.update_one(
                {"id": d["id"], "user_id": user_id},
                {"$set": {"balance_after": new_balance, "updated_at": _now()}},
            )
    final = round(running, 2)
    await db.accounts.update_one(
        {"id": account_id, "user_id": user_id},
        {"$set": {"current_balance": final, "updated_at": _now()}},
    )
    return final


async def _account_with_meta(db, user_id: str, doc: dict) -> dict:
    """Enrich an account doc with derived UI-friendly fields."""
    out = _strip(doc)
    out["account_type_label"] = ACCOUNT_TYPE_LABELS.get(out.get("account_type"), "—")
    out["transactions_count"] = await db.account_transactions.count_documents(
        {"user_id": user_id, "account_id": out["id"]}
    )
    return out


# ── Router ─────────────────────────────────────────────────────────────────
def attach_accounts_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/accounts", tags=["accounts"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("/catalogue")
    async def catalogue(_: dict = Depends(current_user)):
        """Static lookup used by the Add Account modal dropdowns."""
        return {
            "account_types": [
                {"key": k, "label": ACCOUNT_TYPE_LABELS[k]} for k in ACCOUNT_TYPES
            ],
            "suggested_providers": SUGGESTED_PROVIDERS,
            "statuses": list(ACCOUNT_STATUSES),
            "transaction_types": [
                {"key": k, "label": TRANSACTION_TYPE_LABELS[k]} for k in TRANSACTION_TYPES
            ],
        }

    @router.get("/summary")
    async def summary(user: dict = Depends(current_user)):
        """Powers the top 4 summary cards on /accounts."""
        cur = db.accounts.find(
            {"user_id": user["id"], "status": {"$ne": "hidden"}},
            {"_id": 0, "account_type": 1, "current_balance": 1, "status": 1},
        )
        by_type: dict[str, float] = {t: 0.0 for t in ACCOUNT_TYPES}
        grand = 0.0
        async for d in cur:
            t = d.get("account_type")
            bal = float(d.get("current_balance") or 0)
            grand += bal
            if t in by_type:
                by_type[t] += bal
        return {
            "grand_total": round(grand, 2),
            "by_type": {k: round(v, 2) for k, v in by_type.items()},
        }

    @router.get("")
    async def list_accounts(
        user: dict = Depends(current_user),
        account_type: Optional[str] = None,
        status: Optional[str] = None,
        include_hidden: bool = False,
    ):
        q: dict = {"user_id": user["id"]}
        if account_type:
            q["account_type"] = account_type
        if status:
            q["status"] = status
        elif not include_hidden:
            q["status"] = {"$ne": "hidden"}
        docs = await db.accounts.find(q, {"_id": 0}).sort("created_at", -1).to_list(2000)
        return [await _account_with_meta(db, user["id"], d) for d in docs]

    @router.post("")
    async def create_account(payload: AccountIn, user: dict = Depends(current_user)):
        now = _now()
        opening = round(float(payload.opening_balance), 2)
        opening_date = payload.opening_balance_date or now[:10]
        account = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "name": payload.name.strip(),
            "account_type": payload.account_type,
            "provider_name": (payload.provider_name or "").strip() or None,
            "currency": payload.currency.upper(),
            "opening_balance": opening,
            "opening_balance_date": opening_date,
            "current_balance": opening,  # will be re-validated by recompute
            "default_bank_account_id": payload.default_bank_account_id,
            "status": "active",
            "notes": (payload.notes or "").strip(),
            "created_at": now,
            "updated_at": now,
        }
        await db.accounts.insert_one(account)

        # Auto-create the opening balance transaction (only if non-zero).
        if opening != 0:
            await db.account_transactions.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "account_id": account["id"],
                "transaction_type": "opening_balance",
                "amount": abs(opening),
                "direction": "in" if opening >= 0 else "out",
                "description": "رصيد افتتاحي",
                "transaction_date": opening_date,
                "balance_after": opening,
                "status": "posted",
                "attachment_url": None,
                "created_at": now,
                "updated_at": now,
            })
            # Recompute (idempotent) so balance_after is canonical.
            await _recompute_balance(db, user["id"], account["id"])

        return await _account_with_meta(db, user["id"], account)

    @router.get("/{account_id}")
    async def get_account(account_id: str, user: dict = Depends(current_user)):
        doc = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}, {"_id": 0}
        )
        if not doc:
            raise HTTPException(404, "Account not found")
        return await _account_with_meta(db, user["id"], doc)

    @router.put("/{account_id}")
    async def update_account(
        account_id: str, payload: AccountUpdate, user: dict = Depends(current_user)
    ):
        existing = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}
        )
        if not existing:
            raise HTTPException(404, "Account not found")
        update: dict = {"updated_at": _now()}
        for fld in ("name", "provider_name", "currency", "status",
                    "default_bank_account_id", "notes"):
            val = getattr(payload, fld, None)
            if val is not None:
                update[fld] = val.strip() if isinstance(val, str) else val
        await db.accounts.update_one(
            {"id": account_id, "user_id": user["id"]}, {"$set": update}
        )
        doc = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}, {"_id": 0}
        )
        return await _account_with_meta(db, user["id"], doc)

    @router.delete("/{account_id}")
    async def delete_account(account_id: str, user: dict = Depends(current_user)):
        existing = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}
        )
        if not existing:
            raise HTTPException(404, "Account not found")
        # Allow delete only if 0 or 1 (opening) transactions.
        count = await db.account_transactions.count_documents(
            {"user_id": user["id"], "account_id": account_id}
        )
        if count > 1:
            raise HTTPException(
                400,
                "لا يمكن حذف الحساب لأنه مرتبط بحركات مالية. يمكن إخفاؤه بدلاً من الحذف.",
            )
        await db.account_transactions.delete_many(
            {"user_id": user["id"], "account_id": account_id}
        )
        await db.accounts.delete_one({"id": account_id, "user_id": user["id"]})
        return {"ok": True}

    # ── Transactions ───────────────────────────────────────────────────────
    @router.get("/{account_id}/transactions")
    async def list_transactions(account_id: str, user: dict = Depends(current_user)):
        exists = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}, {"_id": 0, "id": 1}
        )
        if not exists:
            raise HTTPException(404, "Account not found")
        docs = await db.account_transactions.find(
            {"user_id": user["id"], "account_id": account_id}, {"_id": 0},
        ).sort([("transaction_date", -1), ("created_at", -1)]).to_list(20000)
        for d in docs:
            d["type_label"] = TRANSACTION_TYPE_LABELS.get(d.get("transaction_type"), d.get("transaction_type"))
        return docs

    @router.post("/{account_id}/transactions")
    async def create_transaction(
        account_id: str, payload: TransactionIn, user: dict = Depends(current_user)
    ):
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}, {"_id": 0, "id": 1}
        )
        if not acc:
            raise HTTPException(404, "Account not found")
        now = _now()
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "account_id": account_id,
            "transaction_type": payload.transaction_type,
            "amount": round(float(payload.amount), 2),
            "direction": payload.direction,
            "description": (payload.description or "").strip(),
            "transaction_date": payload.transaction_date,
            "balance_after": 0.0,  # set by recompute
            "status": payload.status,
            "attachment_url": payload.attachment_url,
            "created_at": now,
            "updated_at": now,
        }
        await db.account_transactions.insert_one(doc)
        await _recompute_balance(db, user["id"], account_id)
        fresh = await db.account_transactions.find_one(
            {"id": doc["id"], "user_id": user["id"]}, {"_id": 0}
        )
        fresh["type_label"] = TRANSACTION_TYPE_LABELS.get(fresh.get("transaction_type"))
        return fresh

    @router.delete("/{account_id}/transactions/{tx_id}")
    async def delete_transaction(
        account_id: str, tx_id: str, user: dict = Depends(current_user)
    ):
        res = await db.account_transactions.delete_one(
            {"id": tx_id, "user_id": user["id"], "account_id": account_id}
        )
        if res.deleted_count == 0:
            raise HTTPException(404, "Transaction not found")
        await _recompute_balance(db, user["id"], account_id)
        return {"ok": True}

    @router.post("/sync-payment-methods")
    async def sync_payment_methods(user: dict = Depends(current_user)):
        """Scan `unified_orders` for distinct payment_method values and
        sync `payment_platform` accounts.

        iter-64: applies the SAME `report_included_statuses` filter the
        Dashboard uses, so total assets line up with total sales for the
        same status whitelist. Without this, sync counted refunded /
        cancelled orders the dashboard already excluded.
        """
        uid = user["id"]
        now = _now()

        # Apply user's dashboard status whitelist if configured.
        from auth import ensure_user_settings
        settings = await ensure_user_settings(db, uid)
        included = settings.get("report_included_statuses") or []
        match_stage: dict = {"user_id": uid}
        if included:
            # Case-insensitive partial match — same semantics as
            # server._matches_any used by the Dashboard.
            patterns = [{"order_status": {"$regex": re.escape(s), "$options": "i"}}
                        for s in included if s]
            if patterns:
                match_stage["$or"] = patterns

        pipeline = [
            {"$match": match_stage},
            {"$group": {
                "_id": {"$ifNull": ["$payment_method", ""]},
                "amount": {"$sum": {"$ifNull": ["$total_amount", 0]}},
                "count":  {"$sum": 1},
            }},
        ]
        # Two-level group:
        #   groups[account_key] = {
        #     display, parent_key, amount, count, raw_names[], sub_methods{}
        #   }
        groups: dict[str, dict] = {}
        async for row in db.unified_orders.aggregate(pipeline):
            raw = (row.get("_id") or "").strip()
            sub_key, sub_display, parent_key = normalize_payment_method(raw)
            if not sub_key:
                continue
            account_key = parent_key or sub_key
            account_display = _PARENT_LABELS.get(parent_key, sub_display) if parent_key else sub_display
            slot = groups.setdefault(account_key, {
                "key": account_key,
                "display": account_display,
                "parent_key": parent_key,
                "amount": 0.0,
                "count": 0,
                "raw_names": [],
                "sub_methods": {},  # sub_key → {key, display, amount, count}
            })
            amount = float(row.get("amount") or 0)
            count = int(row.get("count") or 0)
            slot["amount"] += amount
            slot["count"] += count
            if raw and raw not in slot["raw_names"]:
                slot["raw_names"].append(raw)
            sub_slot = slot["sub_methods"].setdefault(sub_key, {
                "key": sub_key,
                "display": sub_display,
                "amount": 0.0,
                "count": 0,
            })
            sub_slot["amount"] += amount
            sub_slot["count"] += count

        # 2. Cleanup: remove auto-created accounts that no longer map to a
        #    current canonical group. Covers two cases:
        #      a) Salla sub-rails that were once standalone (mada, Apple Pay…)
        #      b) Stale accounts named after raw payment_method spellings
        #         we couldn't normalise before iter-63 (e.g. "البطاقة الإئتمانية"
        #         with hamza, or "\N" null markers).
        #    Only deletes when the account has zero transactions, so we
        #    never destroy any manual settlement entries.
        salla_subs = {sk for sk, _, _, p in _PAYMENT_ALIASES if p == "salla"}
        current_keys = set(groups.keys())
        stale_query = {
            "user_id": uid,
            "auto_created": True,
            "source": "orders_payment_method",
        }
        removed_subs: list[str] = []
        async for s in db.accounts.find(stale_query, {"_id": 0, "id": 1, "name": 1, "normalized_payment_method": 1}):
            key = s.get("normalized_payment_method")
            # Keep if it's a current canonical top-level account
            if key in current_keys:
                continue
            # Otherwise it's stale (legacy sub-rail OR an old non-canonical
            # spelling that the new normalization no longer produces).
            tx_count = await db.account_transactions.count_documents(
                {"user_id": uid, "account_id": s["id"]}
            )
            if tx_count == 0:
                await db.accounts.delete_one({"id": s["id"], "user_id": uid})
                removed_subs.append(s["name"])

        # 3. Upsert one account per top-level key.
        created = 0
        updated = 0
        result_accounts = []
        for key, data in groups.items():
            expected = round(float(data["amount"]), 2)
            orders_count = int(data["count"])
            # Sub-methods list sorted by amount desc for nicer detail UI.
            sub_list = sorted(
                data["sub_methods"].values(), key=lambda x: x["amount"], reverse=True
            )
            sub_list = [
                {**s, "amount": round(float(s["amount"]), 2), "count": int(s["count"])}
                for s in sub_list
            ]

            existing = await db.accounts.find_one(
                {"user_id": uid, "normalized_payment_method": key},
                {"_id": 0},
            )
            if existing:
                # Refresh balance + sub-method breakdown. Also keep the
                # canonical display name in sync so renames in
                # payment_methods.py propagate to existing auto-accounts.
                update_fields = {
                    "current_balance": round(
                        float(existing.get("opening_balance") or 0) + expected, 2
                    ),
                    "expected_orders_balance": expected,
                    "orders_count": orders_count,
                    "raw_payment_names": data["raw_names"][:20],
                    "sub_methods": sub_list,
                    "updated_at": now,
                    "last_synced_at": now,
                }
                if existing.get("auto_created"):
                    update_fields["name"] = data["display"]
                    update_fields["provider_name"] = data["display"]
                await db.accounts.update_one(
                    {"id": existing["id"], "user_id": uid},
                    {"$set": update_fields},
                )
                updated += 1
                fresh = await db.accounts.find_one(
                    {"id": existing["id"], "user_id": uid}, {"_id": 0}
                )
                result_accounts.append(fresh)
            else:
                doc = {
                    "id": str(uuid.uuid4()),
                    "user_id": uid,
                    "name": data["display"],
                    "account_type": "payment_platform",
                    "provider_name": data["display"],
                    "currency": "SAR",
                    "opening_balance": 0.0,
                    "opening_balance_date": now[:10],
                    "current_balance": expected,
                    "expected_orders_balance": expected,
                    "orders_count": orders_count,
                    "raw_payment_names": data["raw_names"][:20],
                    "sub_methods": sub_list,
                    "default_bank_account_id": None,
                    "status": "active",
                    "notes": "تم إنشاؤه تلقائياً من طرق الدفع في الطلبات.",
                    "auto_created": True,
                    "source": "orders_payment_method",
                    "normalized_payment_method": key,
                    "created_at": now,
                    "updated_at": now,
                    "last_synced_at": now,
                }
                await db.accounts.insert_one(doc)
                created += 1
                doc.pop("_id", None)
                result_accounts.append(doc)

        return {
            "ok": True,
            "synced": len(groups),
            "created": created,
            "updated": updated,
            "removed_legacy": removed_subs,
            "accounts": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "normalized_payment_method": a.get("normalized_payment_method"),
                    "current_balance": a.get("current_balance"),
                    "orders_count": a.get("orders_count"),
                    "sub_methods": a.get("sub_methods") or [],
                }
                for a in result_accounts
            ],
        }

    parent_router.include_router(router)
