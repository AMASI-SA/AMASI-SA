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
from payment_gateway_metrics import compute_metrics
from reconciliation_routes import (
    ACCOUNT_KEY_TO_CENTRAL_KEYS,
    _central_expected_for_account,
)


# ── Catalogue ──────────────────────────────────────────────────────────────
ACCOUNT_TYPES = ("bank", "cash", "payment_platform", "ads_platform")

ACCOUNT_TYPE_LABELS = {
    "bank":             "حساب بنكي",
    "cash":             "صندوق نقدي",
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
    "cash": [
        "الصندوق الرئيسي", "صندوق المعرض", "صندوق المستودع",
        "صندوق الفرع", "خزينة المدير", "نقدية في يد الموظف",
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
    CANONICAL_TOP_LEVEL_KEYS,
    normalize_payment_method,
    resolve_account_key,
)


# Iter-111 — Build the set of bank_transfer sub-keys (specific Saudi banks
# that roll up under "تحويل بنكي"). The merchant can route each one to a
# real bank account via `bank_transfer_aliases` on the account doc.
def _known_bank_transfer_sub_keys() -> set[str]:
    """Sub-keys whose parent is `bank_transfer` AND which are NOT the
    generic catch-all itself. These are the only ones the routing UI
    will surface and the only ones the validator accepts.
    """
    return {
        sub
        for sub, _display, _alias, parent in _PAYMENT_ALIASES
        if parent == "bank_transfer" and sub != "bank_transfer"
    }


def _bank_transfer_sub_key_options() -> list[dict]:
    """Routing dropdown options sorted by display name. Deduped — each
    sub-key appears once with its first-seen display name."""
    seen: dict[str, str] = {}
    for sub, display, _alias, parent in _PAYMENT_ALIASES:
        if parent == "bank_transfer" and sub != "bank_transfer":
            seen.setdefault(sub, display)
    return [
        {"sub_key": k, "display": v}
        for k, v in sorted(seen.items(), key=lambda kv: kv[1])
    ]


async def ensure_accounts_indexes(db) -> None:
    """Guarantee a unique partial index on (user_id, normalized_payment_method)
    for auto-created accounts, so even a bug in the sync code can't insert
    two ghost rows for the same canonical payment method. Idempotent — safe
    to call on every startup.
    """
    try:
        await db.accounts.create_index(
            [("user_id", 1), ("normalized_payment_method", 1)],
            name="uniq_auto_user_normalized_pm",
            unique=True,
            partialFilterExpression={
                "auto_created": True,
                "normalized_payment_method": {"$type": "string"},
            },
        )
    except Exception:  # noqa: BLE001
        # Older Mongo (<3.2) doesn't support partial indexes — we still get
        # value from the cleanup pass + resolve_account_key gate. Don't crash.
        pass


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
    "shipping_debt_payment",
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
    "shipping_debt_payment": "سداد مستحقات شحن",
    "manual_adjustment": "تسوية يدوية",
    # ── Iter-198 — ledger-sourced transaction labels for migrated banks
    "sale":              "مبيعات",
    "topup":             "تعبئة",
    "spend":             "صرف إعلانات",
    "salary_payment":    "صرف راتب",
    "salary_accrual":    "استحقاق راتب",
    "advance_grant":     "منح سُلفة",
    "advance_repay_cash": "استرداد سُلفة",
    "custody_grant":     "تسليم عهدة",
    "custody_return":    "استرداد عهدة",
    "custody_transfer":  "تحويل عهدة",
    "purchase_invoice":  "فاتورة مشتريات",
    "salary_settle":     "تسوية راتب",
    "supplier_payment":  "سداد مورد",
    "external_loan":     "إقراض",
    "external_loan_repayment": "استرداد إقراض",
    "courier_cod_settle": "تسوية مندوب COD",
    "correction":        "تصحيح",
    "reversal":          "عكس",
    "adjustment":        "تسوية",
    "payment":           "دفعة",
}

TRANSACTION_STATUSES = ("posted", "pending", "reconciled")


async def _ledger_based_tx_feed(db, user_id: str, account_id: str) -> list:
    """Iter-198 — derive a transactions feed from `general_ledger` for
    migrated bank/cash accounts so the running balance matches the
    iter-192 SSOT used by the top card.

    Returns rows in DESCENDING chronological order (newest first) to
    preserve the existing UI contract. Each row carries:
        id, transaction_type, type_label, amount, direction (in/out),
        description, transaction_date, balance_after, status,
        created_at, txn_group_id, source='ledger', metadata.
    """
    rows = await db.general_ledger.find(
        {"user_id": user_id,
         "entity_type": "bank",
         "entity_id": account_id,
         "sub_account": "main",
         "status": "posted"},
        {"_id": 0, "id": 1, "entry_type": 1, "side": 1, "amount": 1,
         "notes": 1, "posted_at": 1, "created_at": 1,
         "txn_group_id": 1, "metadata": 1, "actor_name": 1,
         "reversal_of_txn_group_id": 1,
         "corrects_txn_group_id": 1},
    ).sort([("posted_at", 1), ("created_at", 1), ("id", 1)]).to_list(50000)

    # Iter-200 — gather all txn_group_ids that have been
    # reversed or corrected, in one batch query each, so we can
    # decorate the rows without N+1 round-trips.
    src_group_ids = list({
        r.get("txn_group_id") for r in rows if r.get("txn_group_id")
    })
    reversed_map: dict = {}
    if src_group_ids:
        async for rv in db.general_ledger.find(
            {"user_id": user_id,
             "entry_type": "reversal",
             "reversal_of_txn_group_id": {"$in": src_group_ids},
             "status": "posted"},
            {"_id": 0, "reversal_of_txn_group_id": 1,
             "txn_group_id": 1, "posted_at": 1, "metadata": 1},
        ):
            target = rv.get("reversal_of_txn_group_id")
            if target and target not in reversed_map:
                md = rv.get("metadata") or {}
                reversed_map[target] = {
                    "reversal_group_id": rv.get("txn_group_id"),
                    "reversed_at": rv.get("posted_at"),
                    "reason": md.get("reason"),
                    "amount": md.get("original_amount"),
                }
    corrected_map: dict = {}
    if src_group_ids:
        async for cr in db.general_ledger.find(
            {"user_id": user_id,
             "entry_type": "correction",
             "corrects_txn_group_id": {"$in": src_group_ids},
             "status": "posted"},
            {"_id": 0, "corrects_txn_group_id": 1,
             "txn_group_id": 1, "posted_at": 1, "metadata": 1,
             "amount": 1},
        ):
            target = cr.get("corrects_txn_group_id")
            if not target:
                continue
            md = cr.get("metadata") or {}
            grp = corrected_map.setdefault(target, {
                "groups": set(),
                "total_amount": 0.0,
                "last_at": cr.get("posted_at"),
                "last_reason": md.get("reason"),
            })
            grp["groups"].add(cr.get("txn_group_id"))
            grp["total_amount"] += float(cr.get("amount") or 0) / 2.0
            if cr.get("posted_at") and (
                not grp["last_at"]
                or cr.get("posted_at") > grp["last_at"]
            ):
                grp["last_at"] = cr.get("posted_at")
                grp["last_reason"] = md.get("reason")

    running = 0.0
    out: list = []
    for r in rows:
        amt = float(r.get("amount") or 0)
        side = r.get("side")
        if side == "debit":
            running += amt
            direction = "in"
        else:
            running -= amt
            direction = "out"
        ttype = r.get("entry_type") or "adjustment"
        md = r.get("metadata") or {}
        gid = r.get("txn_group_id")
        rev_info = reversed_map.get(gid) if gid else None
        corr_info = corrected_map.get(gid) if gid else None

        out.append({
            "id": r.get("id"),
            "transaction_type": ttype,
            "type_label": TRANSACTION_TYPE_LABELS.get(ttype, ttype),
            "amount": round(amt, 2),
            "direction": direction,
            "description": (
                r.get("notes")
                or md.get("description")
                or md.get("party_name")
                or md.get("employee_name")
                or ""
            ),
            "transaction_date": (
                r.get("posted_at") or r.get("created_at")
            ),
            "balance_after": round(running, 2),
            "status": "posted",
            "created_at": r.get("created_at"),
            "txn_group_id": gid,
            "actor_name": r.get("actor_name"),
            "metadata": md,
            "source": "ledger",
            # Iter-200 — audit badges
            "is_reversal": ttype == "reversal",
            "is_correction": ttype == "correction",
            "reversal_of_txn_group_id":
                r.get("reversal_of_txn_group_id"),
            "corrects_txn_group_id":
                r.get("corrects_txn_group_id"),
            "was_reversed": rev_info is not None,
            "reversal_info": rev_info,
            "was_corrected": corr_info is not None,
            "correction_info": (
                {"correction_count": len(corr_info["groups"]),
                 "total_amount":
                     round(corr_info["total_amount"], 2),
                 "last_at": corr_info["last_at"],
                 "last_reason": corr_info["last_reason"]}
                if corr_info else None
            ),
        })

    # Reverse so newest is first (matches current UI ORDER BY desc).
    out.reverse()
    return out


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
    # Iter-111 — bank-transfer routing. List of payment_methods.py sub-keys
    # (e.g. ["bank_rajhi", "bank_ahli"]) whose order revenue this bank
    # account should receive directly instead of the generic
    # "تحويل بنكي" rollup. Only meaningful when account_type == "bank".
    bank_transfer_aliases: Optional[list[str]] = None

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


class TransferIn(BaseModel):
    """Internal transfer between two of the user's own accounts.

    Generates TWO linked `internal_transfer` rows in `account_transactions`:
      • one OUT from `from_account_id`
      • one IN  to  `to_account_id`
    Both share the same `transfer_id` so the UI can render them as a single
    movement and the API can undo the pair atomically.
    """
    from_account_id: str
    to_account_id: str
    amount: float = Field(..., gt=0)
    transfer_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    reference: Optional[str] = Field("", max_length=120)
    notes: Optional[str] = Field("", max_length=500)
    attachment_url: Optional[str] = None


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

    # Iter-217 — SSOT for ALL bank/cash/payment_platform accounts.
    # Delegates to `account_balance_ssot` so the per-row balance MATCHES
    # `/accounts/summary` and `/accounting/financial-position`. Behaviour:
    #   • BNPL platforms → canonical BNPL formula.
    #   • Any account with ledger activity → ledger net (+ legacy
    #     current_balance as implicit opening if no opening_balance
    #     entry exists, mirroring Iter-192 semantics).
    #   • No ledger activity → legacy current_balance fallback.
    if out.get("account_type") in ("bank", "cash", "payment_platform"):
        try:
            from financial_position_ssot import account_balance_ssot
            ssot_bal = await account_balance_ssot(
                db, user_id=user_id, account=out,
            )
            if abs(ssot_bal - float(out.get("current_balance") or 0)) > 0.005:
                out["current_balance_legacy"] = out.get("current_balance")
            out["current_balance"] = round(float(ssot_bal), 2)
            out["balance_source"] = "ssot"
            return out
        except Exception:  # noqa: BLE001
            # Never let SSOT computation block account listing.
            out["balance_source"] = "legacy"

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
        """Powers the top 4 summary cards on /accounts. Iter-217 —
        delegates per-account balance to the shared SSOT rule so the
        numbers here MATCH `/accounting/financial-position` and the
        per-row balances rendered by `_account_with_meta`."""
        from financial_position_ssot import account_balance_ssot
        cur = db.accounts.find(
            {"user_id": user["id"], "status": {"$ne": "hidden"}},
            {"_id": 0, "id": 1, "account_type": 1, "current_balance": 1,
             "status": 1, "provider_name": 1, "name": 1,
             "normalized_payment_method": 1},
        )
        by_type: dict[str, float] = {t: 0.0 for t in ACCOUNT_TYPES}
        grand = 0.0
        async for d in cur:
            t = d.get("account_type")
            try:
                bal = await account_balance_ssot(
                    db, user_id=user["id"], account=d,
                )
            except Exception:  # noqa: BLE001
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
        type: Optional[str] = None,
        status: Optional[str] = None,
        include_hidden: bool = False,
    ):
        # Iter-170 — accept both `type` and `account_type` so callers
        # that send the shorter alias (e.g. `?type=bank`) get filtered
        # results. Previously `type=bank` was silently ignored and the
        # endpoint returned ALL accounts → caused duplicate entries in
        # the «خصم من حساب» dropdown of the Unified Entry screen.
        q: dict = {"user_id": user["id"]}
        effective_type = account_type or type
        if effective_type:
            q["account_type"] = effective_type
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

    @router.get("/unclassified-payment-methods")
    async def list_unclassified_payment_methods(user: dict = Depends(current_user)):
        """Diagnostic — list raw payment_method values the normalizer could
        NOT map to a canonical account. These are NEVER turned into accounts.
        Useful for the operator to add new aliases to payment_methods.py.
        """
        rows = await db.unclassified_payment_methods.find(
            {"user_id": user["id"]}, {"_id": 0},
        ).sort("count", -1).to_list(500)
        return {"rows": rows, "count": len(rows)}

    @router.get("/{account_id}")
    async def get_account(account_id: str, user: dict = Depends(current_user)):
        doc = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}, {"_id": 0}
        )
        if not doc:
            raise HTTPException(404, "Account not found")
        return await _account_with_meta(db, user["id"], doc)

    # ── Iter-111 — Bank-transfer routing helpers ──────────────────────
    @router.get("/bank-transfer-routing/options")
    async def list_routing_options(_: dict = Depends(current_user)):
        """Return the canonical list of bank-transfer sub-keys the user
        can assign to a bank account (Rajhi / Inma / Ahli / Riyad …)."""
        return {"options": _bank_transfer_sub_key_options()}

    @router.get("/bank-transfer-routing/map")
    async def get_routing_map(user: dict = Depends(current_user)):
        """Current routing: which sub-key is bound to which bank, plus
        the per-bank aggregates so the user sees the impact at a glance.
        """
        out: list[dict] = []
        async for b in db.accounts.find(
            {"user_id": user["id"], "account_type": "bank"},
            {"_id": 0},
        ).sort("name", 1):
            out.append({
                "id": b["id"], "name": b["name"],
                "bank_transfer_aliases": b.get("bank_transfer_aliases") or [],
                "current_balance": b.get("current_balance") or 0.0,
                "expected_orders_balance": b.get("expected_orders_balance") or 0.0,
                "orders_count": b.get("orders_count") or 0,
                "sub_methods": b.get("sub_methods") or [],
                "last_synced_at": b.get("last_synced_at"),
            })
        return {"banks": out}

    @router.get("/{account_id}/breakdown")
    async def account_breakdown(
        account_id: str, user: dict = Depends(current_user),
    ):
        """Iter-111 diagnostic — explain a bank's final balance.

        Returns the components:
          • opening_balance (manual seed at account creation)
          • incoming_from_customer_bank_transfers (routed order revenue)
          • incoming_from_payment_gateways (transfers IN from سلة / تمارا …)
          • incoming_manual_deposits (other 'in' transactions)
          • outgoing_liability_payments
          • outgoing_expenses
          • outgoing_to_other_accounts (transfers OUT)
          • final_balance & recorded_balance — should match.
        """
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": user["id"]}, {"_id": 0},
        )
        if not acc:
            raise HTTPException(404, "Account not found")

        opening = float(acc.get("opening_balance") or 0)
        expected_orders = float(acc.get("expected_orders_balance") or 0)

        # Walk ledger and bucket each transaction by transaction_type +
        # direction. transaction_type comes from TRANSACTION_TYPES (see
        # top of file): manual_deposit / liability_payment / expense /
        # internal_transfer …
        buckets = {
            "incoming_from_payment_gateways": 0.0,    # transfers in
            "incoming_manual_deposits": 0.0,
            "outgoing_liability_payments": 0.0,
            "outgoing_expenses": 0.0,
            "outgoing_to_other_accounts": 0.0,
            "incoming_other": 0.0,
            "outgoing_other": 0.0,
        }
        tx_count = 0
        async for t in db.account_transactions.find(
            {"user_id": user["id"], "account_id": account_id},
            {"_id": 0},
        ):
            tx_count += 1
            ttype = t.get("transaction_type") or ""
            direction = t.get("direction")
            amt = float(t.get("amount") or 0)
            if direction == "in":
                if ttype == "internal_transfer":
                    buckets["incoming_from_payment_gateways"] += amt
                elif ttype in ("manual_deposit", "opening_balance"):
                    buckets["incoming_manual_deposits"] += amt
                else:
                    buckets["incoming_other"] += amt
            else:  # out
                if ttype == "liability_payment":
                    buckets["outgoing_liability_payments"] += amt
                elif ttype == "expense":
                    buckets["outgoing_expenses"] += amt
                elif ttype == "internal_transfer":
                    buckets["outgoing_to_other_accounts"] += amt
                else:
                    buckets["outgoing_other"] += amt

        # final_balance computed from bottom-up: opening + expected_orders
        # + incoming - outgoing.
        computed = (
            opening + expected_orders
            + buckets["incoming_from_payment_gateways"]
            + buckets["incoming_manual_deposits"]
            + buckets["incoming_other"]
            - buckets["outgoing_liability_payments"]
            - buckets["outgoing_expenses"]
            - buckets["outgoing_to_other_accounts"]
            - buckets["outgoing_other"]
        )
        recorded = float(acc.get("current_balance") or 0)
        return {
            "id": acc["id"], "name": acc["name"],
            "account_type": acc.get("account_type"),
            "bank_transfer_aliases": acc.get("bank_transfer_aliases") or [],
            "opening_balance": round(opening, 2),
            "incoming_from_customer_bank_transfers": round(expected_orders, 2),
            "orders_count": acc.get("orders_count") or 0,
            "incoming_from_payment_gateways": round(buckets["incoming_from_payment_gateways"], 2),
            "incoming_manual_deposits": round(buckets["incoming_manual_deposits"], 2),
            "incoming_other": round(buckets["incoming_other"], 2),
            "outgoing_liability_payments": round(buckets["outgoing_liability_payments"], 2),
            "outgoing_expenses": round(buckets["outgoing_expenses"], 2),
            "outgoing_to_other_accounts": round(buckets["outgoing_to_other_accounts"], 2),
            "outgoing_other": round(buckets["outgoing_other"], 2),
            "transactions_count": tx_count,
            "final_balance": round(computed, 2),
            "recorded_balance": round(recorded, 2),
            "discrepancy": round(computed - recorded, 2),
            "sub_methods": acc.get("sub_methods") or [],
        }

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
        # Iter-111 — bank_transfer_aliases (validated against the known
        # sub-keys defined in payment_methods.py). Only set when the
        # account is a bank; for other types we silently ignore the field
        # so callers don't have to special-case.
        if payload.bank_transfer_aliases is not None:
            if existing.get("account_type") != "bank":
                raise HTTPException(
                    400,
                    "توجيه التحويلات البنكية متاح فقط للحسابات من نوع `bank`.",
                )
            allowed = _known_bank_transfer_sub_keys()
            cleaned = []
            for k in payload.bank_transfer_aliases:
                k = (k or "").strip()
                if not k:
                    continue
                if k not in allowed:
                    raise HTTPException(
                        400,
                        f"الـ alias `{k}` غير معروف. المتاح: {sorted(allowed)}",
                    )
                if k not in cleaned:
                    cleaned.append(k)
            # Reject double-binding: each sub-key can route to ONE bank
            # only. Look for conflicts with OTHER banks' existing routing.
            if cleaned:
                clash = await db.accounts.find_one(
                    {
                        "user_id": user["id"],
                        "account_type": "bank",
                        "id": {"$ne": account_id},
                        "bank_transfer_aliases": {"$in": cleaned},
                    },
                    {"_id": 0, "name": 1, "bank_transfer_aliases": 1},
                )
                if clash:
                    dup = sorted(set(cleaned) & set(clash.get("bank_transfer_aliases") or []))
                    raise HTTPException(
                        400,
                        f"الـ aliases {dup} موجَّهة بالفعل إلى البنك «{clash['name']}». فك الربط من هناك أولاً.",
                    )
            update["bank_transfer_aliases"] = cleaned
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
        # Iter-198 — SSOT for the transactions log.
        # Production bug: top-card balance (from `_account_with_meta`,
        # which honours the iter-192 ledger SSOT) diverged from the
        # last `balance_after` in this list by 87k+ SAR because every
        # post-migration operation is now written to `general_ledger`
        # ONLY — `account_transactions` is frozen at the migration
        # snapshot. Solution: for migrated bank/cash accounts, derive
        # the transactions feed FROM the ledger so the running balance
        # walks the same source the top card consumes.
        uid = user["id"]
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": uid},
            {"_id": 0, "id": 1, "account_type": 1, "currency": 1},
        )
        if not acc:
            raise HTTPException(404, "Account not found")

        # Detect migration anchor (opening_balance posted in ledger).
        # Only `bank` / `cash` accounts route through the ledger.
        is_migrated = False
        if acc.get("account_type") in ("bank", "cash"):
            anchor = await db.general_ledger.find_one(
                {"user_id": uid, "entity_type": "bank",
                 "entity_id": account_id,
                 "entry_type": "opening_balance",
                 "status": "posted"},
                {"_id": 1},
            )
            is_migrated = bool(anchor)

        if is_migrated:
            return await _ledger_based_tx_feed(db, uid, account_id)

        # Legacy path — pre-migration accounts keep the old behaviour.
        docs = await db.account_transactions.find(
            {"user_id": uid, "account_id": account_id}, {"_id": 0},
        ).sort([("transaction_date", -1), ("created_at", -1)]).to_list(20000)
        for d in docs:
            d["type_label"] = TRANSACTION_TYPE_LABELS.get(
                d.get("transaction_type"), d.get("transaction_type"))
            d["source"] = "account_tx"
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

        iter-70: also honours `hide_inferred_date_orders` so the two
        sources stay in lock-step on which orders count.
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
        # Mirror the Dashboard's optional "hide inferred date" toggle so
        # /api/dashboard and /api/accounts agree on the universe of orders.
        if settings.get("hide_inferred_date_orders"):
            match_stage["order_date_inferred"] = {"$ne": True}

        pipeline = [
            {"$match": match_stage},
            # Phase 80 — prefer actual_net_amount (from settlement files
            # uploaded via /payment-settlements) when present. Orders not
            # yet matched against any settlement file fall back to the
            # estimated total_amount so the expected balance stays
            # populated. payment_fee_status='actual' is the flag set by
            # the settlement importer.
            {"$addFields": {
                "_settlement_net": {
                    "$cond": [
                        {"$and": [
                            {"$eq": ["$payment_fee_status", "actual"]},
                            {"$ne": ["$actual_net_amount", None]},
                        ]},
                        "$actual_net_amount",
                        {"$ifNull": ["$total_amount", 0]},
                    ],
                },
            }},
            {"$group": {
                "_id": {"$ifNull": ["$payment_method", ""]},
                "amount": {"$sum": "$_settlement_net"},
                "count":  {"$sum": 1},
            }},
        ]
        # Iter-111 — Build routing map: { sub_key → bank_account_id }
        # so any orders whose payment_method maps to that sub_key are
        # credited DIRECTLY to the bank account instead of accumulating
        # in the generic "تحويل بنكي" rollup.
        bank_routing: dict[str, str] = {}
        # Track aggregates per routed bank → {bank_id: {amount, count,
        # raw_names, sub_methods}} so we can update each bank's
        # expected_orders_balance below.
        bank_aggregates: dict[str, dict] = {}
        async for b in db.accounts.find(
            {"user_id": uid, "account_type": "bank",
             "bank_transfer_aliases": {"$exists": True, "$ne": []}},
            {"_id": 0, "id": 1, "name": 1, "bank_transfer_aliases": 1},
        ):
            for sk in (b.get("bank_transfer_aliases") or []):
                bank_routing[sk] = b["id"]
            bank_aggregates[b["id"]] = {
                "name": b["name"],
                "amount": 0.0, "count": 0,
                "raw_names": [], "sub_methods": {},
            }

        # Two-level group:
        #   groups[account_key] = {
        #     display, parent_key, amount, count, raw_names[], sub_methods{}
        #   }
        groups: dict[str, dict] = {}
        # Track raw payment-method strings we could NOT classify so the
        # operator can fix the alias table later. Never becomes an account.
        unclassified: dict[str, dict] = {}
        async for row in db.unified_orders.aggregate(pipeline):
            raw = (row.get("_id") or "").strip()
            amount = float(row.get("amount") or 0)
            count = int(row.get("count") or 0)

            # The single classification gate for the whole app. If the raw
            # value is empty / "\N" / "غير محدد" / unknown → log + skip.
            account_key, account_display = resolve_account_key(raw)
            if account_key is None:
                slot = unclassified.setdefault(raw or "(empty)", {
                    "raw": raw or "(empty)",
                    "amount": 0.0,
                    "count": 0,
                })
                slot["amount"] += amount
                slot["count"] += count
                continue

            sub_key, sub_display, parent_key = normalize_payment_method(raw)

            # Iter-111 — if this sub_key is routed to a bank, divert the
            # amount/count into the bank's aggregate and SKIP the rollup
            # tally. The rollup ("تحويل بنكي") therefore only retains
            # bank-transfer revenue that is NOT routed (e.g. orders with
            # the generic "حوالة بنكية" string and no specific bank).
            if sub_key in bank_routing:
                bank_id = bank_routing[sub_key]
                bagg = bank_aggregates[bank_id]
                bagg["amount"] += amount
                bagg["count"] += count
                if raw and raw not in bagg["raw_names"]:
                    bagg["raw_names"].append(raw)
                sub_slot = bagg["sub_methods"].setdefault(sub_key, {
                    "key": sub_key, "display": sub_display,
                    "amount": 0.0, "count": 0,
                })
                sub_slot["amount"] += amount
                sub_slot["count"] += count
                continue

            slot = groups.setdefault(account_key, {
                "key": account_key,
                "display": account_display,
                "parent_key": parent_key,
                "amount": 0.0,
                "count": 0,
                "raw_names": [],
                "sub_methods": {},  # sub_key → {key, display, amount, count}
            })
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

        # Persist the unclassified report so the operator can see it later.
        await db.unclassified_payment_methods.delete_many({"user_id": uid})
        if unclassified:
            await db.unclassified_payment_methods.insert_many([
                {
                    "id": str(uuid.uuid4()),
                    "user_id": uid,
                    "raw_payment_method": u["raw"],
                    "amount": round(u["amount"], 2),
                    "count": u["count"],
                    "logged_at": now,
                }
                for u in unclassified.values()
            ])

        # 2. Cleanup: remove auto-created payment_platform accounts that are
        #    NOT in the canonical top-level set OR no longer map to a current
        #    group. Covers:
        #      a) Salla sub-rails that were once standalone (mada, Apple Pay…)
        #      b) Stale accounts named after raw payment_method spellings
        #         we couldn't normalise before iter-63 (e.g. "البطاقة الإئتمانية"
        #         with hamza, or "\N" null markers).
        #      c) Ghost accounts created by older builds when normalize_payment_method
        #         fell back to a slug (e.g. unknown_method_xyz).
        #    Only deletes when the account has zero transactions, so we
        #    never destroy any manual settlement entries.
        current_keys = set(groups.keys())
        stale_query = {
            "user_id": uid,
            "account_type": "payment_platform",
            "auto_created": True,
        }
        removed_subs: list[str] = []
        kept_with_tx: list[str] = []
        async for s in db.accounts.find(stale_query, {"_id": 0, "id": 1, "name": 1, "normalized_payment_method": 1}):
            key = s.get("normalized_payment_method")
            # Keep if it's a current canonical top-level account that also
            # appears in this sync. Anything else is stale OR non-canonical.
            if key in CANONICAL_TOP_LEVEL_KEYS and key in current_keys:
                continue
            tx_count = await db.account_transactions.count_documents(
                {"user_id": uid, "account_id": s["id"]}
            )
            if tx_count == 0:
                await db.accounts.delete_one({"id": s["id"], "user_id": uid})
                removed_subs.append(s["name"])
            else:
                # Has manual transactions — hide instead of delete so the
                # ledger stays intact but it stops polluting the asset total.
                await db.accounts.update_one(
                    {"id": s["id"], "user_id": uid},
                    {"$set": {"status": "hidden", "updated_at": now}},
                )
                kept_with_tx.append(s["name"])

        # Iter-81 — single source of truth. The central
        # /payment-gateway-metrics applies the SAME priority chain
        # (actual settlement > estimated) and is what Reports /
        # Reconciliation read. Use it to populate
        # `expected_orders_balance` so the three pages always agree.
        central = await compute_metrics(db, uid)
        central_rows = central.get("rows") or []

        # Iter-111 — the central metrics does its own aggregation pass
        # over unified_orders and is unaware of our bank routing. For
        # the `bank_transfer` rollup specifically, we must subtract the
        # routed amount so the rollup only reflects unrouted revenue.
        routed_total = round(sum(b["amount"] for b in bank_aggregates.values()), 2)

        # 3. Upsert one account per top-level key.
        created = 0
        updated = 0
        result_accounts = []
        for key, data in groups.items():
            # Prefer central net for the canonical account key. Falls back
            # to local pipeline amount when central has nothing for this
            # rail (e.g. very fresh data not in unified_orders yet).
            #
            # Iter-111 — when bank routing is active, the local pipeline
            # (which uses payment_methods.normalize_payment_method) has
            # a richer alias table than the central metrics. For the
            # bank_transfer rollup specifically, always use local data
            # so that routed sub-keys are correctly excluded (the local
            # loop already `continue`d for them).
            c_net, c_orders, c_actual = _central_expected_for_account(
                central_rows, key
            )
            local_aware_bank_transfer = (
                key == "bank_transfer" and routed_total > 0
            )
            if c_orders > 0 and not local_aware_bank_transfer:
                expected = c_net
                orders_count = c_orders
            else:
                # Local data is already routing-aware (we `continue`d
                # earlier for routed sub-keys).
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
                # Refresh expected + sub-method breakdown. Also keep the
                # canonical display name in sync so renames in
                # payment_methods.py propagate to existing auto-accounts.
                # IMPORTANT: do NOT overwrite `current_balance` here — let
                # `_recompute_balance` derive it from the ledger so any
                # internal_transfer rows (Phase 2.1) keep their effect.
                update_fields = {
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
                # Recompute current_balance from ledger so transfers stay applied.
                await _recompute_balance(db, uid, existing["id"])
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

        # Iter-111 — apply routed bank-transfer aggregates to the user's
        # bank accounts. Every bank in the routing map gets its
        # `expected_orders_balance` refreshed to reflect ONLY the routed
        # sub-keys. Banks without routing remain untouched (0 by default
        # — they only move via manual transfers).
        routed_banks_synced: list[dict] = []
        for bank_id, bagg in bank_aggregates.items():
            sub_list = sorted(
                bagg["sub_methods"].values(),
                key=lambda x: x["amount"], reverse=True,
            )
            sub_list = [
                {**s, "amount": round(float(s["amount"]), 2),
                 "count": int(s["count"])}
                for s in sub_list
            ]
            expected = round(float(bagg["amount"]), 2)
            await db.accounts.update_one(
                {"id": bank_id, "user_id": uid},
                {"$set": {
                    "expected_orders_balance": expected,
                    "orders_count": int(bagg["count"]),
                    "raw_payment_names": bagg["raw_names"][:20],
                    "sub_methods": sub_list,
                    "last_synced_at": now,
                    "updated_at": now,
                }},
            )
            await _recompute_balance(db, uid, bank_id)
            fresh = await db.accounts.find_one(
                {"id": bank_id, "user_id": uid}, {"_id": 0},
            )
            if fresh:
                routed_banks_synced.append(fresh)

        # Banks that USED to have routing but now don't (user cleared
        # their `bank_transfer_aliases`) must also be reset so we don't
        # leave stale `expected_orders_balance` from a previous sync.
        async for stale in db.accounts.find(
            {"user_id": uid, "account_type": "bank",
             "expected_orders_balance": {"$gt": 0},
             "id": {"$nin": list(bank_aggregates.keys())}},
            {"_id": 0, "id": 1, "bank_transfer_aliases": 1},
        ):
            if stale.get("bank_transfer_aliases"):
                continue  # has routing but no orders matched — keep at 0
            await db.accounts.update_one(
                {"id": stale["id"], "user_id": uid},
                {"$set": {"expected_orders_balance": 0.0,
                          "orders_count": 0, "sub_methods": [],
                          "last_synced_at": now, "updated_at": now}},
            )
            await _recompute_balance(db, uid, stale["id"])

        return {
            "ok": True,
            "synced": len(groups),
            "created": created,
            "updated": updated,
            "removed_legacy": removed_subs,
            "hidden_with_transactions": kept_with_tx,
            "unclassified_count": len(unclassified),
            "unclassified": [
                {"raw": u["raw"], "amount": round(u["amount"], 2), "count": u["count"]}
                for u in unclassified.values()
            ],
            "routed_banks": [
                {"id": b["id"], "name": b["name"],
                 "expected_orders_balance": b.get("expected_orders_balance"),
                 "orders_count": b.get("orders_count"),
                 "bank_transfer_aliases": b.get("bank_transfer_aliases") or [],
                 "sub_methods": b.get("sub_methods") or []}
                for b in routed_banks_synced
            ],
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

    @router.post("/ensure-default-banks")
    async def ensure_default_banks(user: dict = Depends(current_user)):
        """Create the 3 default Saudi bank accounts if they don't exist yet.

        Idempotent — only inserts banks the user hasn't already added (matches
        on `name` case-insensitively). Returns what was created vs already-there.
        """
        DEFAULTS = [
            {"name": "بنك الإنماء",  "provider_name": "بنك الإنماء"},
            {"name": "بنك الأهلي",   "provider_name": "البنك الأهلي السعودي"},
            {"name": "بنك الراجحي",  "provider_name": "مصرف الراجحي"},
        ]
        uid = user["id"]
        now = _now()
        created: list[dict] = []
        existing_names: list[str] = []
        for bank in DEFAULTS:
            # Case-insensitive match — catches "بنك الراجحي" vs "الراجحي" too.
            exists = await db.accounts.find_one(
                {
                    "user_id": uid,
                    "account_type": "bank",
                    "name": {"$regex": f"^{re.escape(bank['name'])}$", "$options": "i"},
                },
                {"_id": 0, "id": 1, "name": 1},
            )
            if exists:
                existing_names.append(exists["name"])
                continue
            doc = {
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "name": bank["name"],
                "account_type": "bank",
                "provider_name": bank["provider_name"],
                "currency": "SAR",
                "opening_balance": 0.0,
                "opening_balance_date": now[:10],
                "current_balance": 0.0,
                "default_bank_account_id": None,
                "status": "active",
                "notes": "تم إنشاؤه تلقائياً عند فتح شاشة التحويلات.",
                "auto_created": True,
                "source": "default_banks",
                "created_at": now,
                "updated_at": now,
            }
            await db.accounts.insert_one(doc)
            doc.pop("_id", None)
            created.append({"id": doc["id"], "name": doc["name"]})
        return {"ok": True, "created": created, "existing": existing_names}

    parent_router.include_router(router)
