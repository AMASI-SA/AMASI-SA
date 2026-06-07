"""Accounts payable for deferred shipping companies.

A deferred shipping company is one whose cost is NOT deducted from Salla's
transfer to the bank. We accrue what the merchant owes them based on each
analysis (orders_count × cost_per_order × (1 + VAT)) and let the merchant
record payments against that liability over time.

Each company's account shows:
- total_owed   = sum of shipping_breakdown.total_cost across analyses (deferred rows only)
- total_paid   = sum of shipping_payments.amount
- remaining    = total_owed − total_paid
- payments[]   = ledger entries (date, amount, invoice_number, note)

Endpoints (all under /api/shipping-accounts):
- GET    /                          → list every deferred company w/ totals + payments
- GET    /{company}/payments        → payments for one company
- POST   /{company}/payments        → record a payment
- DELETE /payments/{payment_id}     → delete a payment
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db, ensure_user_settings, DEFAULT_SHIPPING_COMPANIES


# Iter-101 — orders are an accrued shipping liability ONLY when they
# actually got delivered (or completed). Anything still in-transit,
# cancelled, or refunded creates NO obligation toward the courier.
DELIVERED_STATUSES_DEFAULT = [
    "تم التوصيل", "تم الاستلام", "تم التنفيذ",
    "delivered", "completed",
]


class PaymentIn(BaseModel):
    amount: float = Field(gt=0)
    payment_date: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD
    invoice_number: Optional[str] = ""
    note: Optional[str] = ""
    # Iter-95: optional link to a bank/cash account. When set, the payment
    # auto-posts an out-flowing account_transactions row so the bank
    # balance and the financial-position screen stay in sync.
    paid_from_account_id: Optional[str] = None


# Iter-95 — bank movement helpers (mirrors expenses_routes Iter-94 pattern).
async def _recompute_shipping_account_balance(db, user_id: str, account_id: str) -> None:
    """Walk all transactions of `account_id` chronologically and refresh
    `balance_after` + `current_balance`."""
    acc = await db.accounts.find_one(
        {"id": account_id, "user_id": user_id},
        {"_id": 0, "expected_orders_balance": 1},
    ) or {}
    running = float(acc.get("expected_orders_balance") or 0)
    docs = await db.account_transactions.find(
        {"user_id": user_id, "account_id": account_id},
        {"_id": 0, "id": 1, "amount": 1, "direction": 1, "balance_after": 1},
    ).sort([("transaction_date", 1), ("created_at", 1)]).to_list(50000)
    now = datetime.now(timezone.utc).isoformat()
    for d in docs:
        amt = float(d.get("amount", 0) or 0)
        running += amt if d.get("direction") == "in" else -amt
        new_balance = round(running, 2)
        if d.get("balance_after") != new_balance:
            await db.account_transactions.update_one(
                {"id": d["id"], "user_id": user_id},
                {"$set": {"balance_after": new_balance, "updated_at": now}},
            )
    final = round(running, 2)
    await db.accounts.update_one(
        {"id": account_id, "user_id": user_id},
        {"$set": {"current_balance": final, "updated_at": now}},
    )


async def _post_shipping_payment_tx(
    db, user_id: str, *,
    payment_id: str, account_id: str,
    amount: float, payment_date: str,
    company_name: str, invoice: str,
) -> str:
    """Insert an out-flowing account_transactions row tied to a shipping
    payment. Returns the new transaction id."""
    tx_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    desc = f"سداد مستحقات شركة الشحن — {company_name}"
    if invoice:
        desc += f" (فاتورة {invoice})"
    await db.account_transactions.insert_one({
        "id": tx_id,
        "user_id": user_id,
        "account_id": account_id,
        "transaction_type": "shipping_debt_payment",
        "amount": round(float(amount), 2),
        "direction": "out",
        "description": desc[:280],
        "transaction_date": payment_date,
        "balance_after": 0.0,    # set by recompute below
        "status": "posted",
        "reference": invoice or "",
        "peer_shipping_payment_id": payment_id,
        "created_at": now,
        "updated_at": now,
    })
    await _recompute_shipping_account_balance(db, user_id, account_id)
    return tx_id


async def _delete_shipping_payment_tx(
    db, user_id: str, *, transaction_id: str, account_id: str,
) -> None:
    await db.account_transactions.delete_one(
        {"id": transaction_id, "user_id": user_id}
    )
    await _recompute_shipping_account_balance(db, user_id, account_id)


def _matches_any_sync(value: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    v = (value or "").strip().lower()
    for a in allowed:
        a_lc = a.strip().lower()
        if a_lc and (a_lc == v or a_lc in v or v in a_lc):
            return True
    return False


def _resolve_company_sync(name: str, cfg_map: dict[str, dict]) -> tuple[str, dict] | tuple[None, None]:
    v = (name or "").strip().lower()
    if not v:
        return None, None
    if v in cfg_map:
        cfg = cfg_map[v]
        return cfg.get("name", "").strip(), cfg
    for k, cfg in cfg_map.items():
        if k and (k in v or v in k):
            return cfg.get("name", "").strip(), cfg
    return None, None


async def _deferred_company_names_db(db, user_id: str) -> list[str]:
    settings = await ensure_user_settings(db, user_id)
    return [
        (s.get("name") or "").strip()
        for s in settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES)
        if s.get("is_deferred") and (s.get("name") or "").strip()
    ]


async def _shipping_config_map_db(db, user_id: str) -> dict[str, dict]:
    settings = await ensure_user_settings(db, user_id)
    out: dict[str, dict] = {}
    for s in settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES):
        n = (s.get("name") or "").strip()
        if not n:
            continue
        out[n.lower()] = s
    return out


async def compute_owed_per_company(db, user_id: str) -> dict[str, dict]:
    """Module-level reusable version. Iter-101 — strict status filter:
    only orders matching delivered/completed statuses ever accrue a
    shipping liability. Defaults to DELIVERED_STATUSES_DEFAULT when the
    user hasn't customised `report_included_statuses` in settings.

    The legacy `analyses.report.shipping_breakdown` path lacks per-order
    statuses and so can ONLY be trusted if the user has not overridden
    the default delivered filter AND there is no fresh unified_orders
    data. We unconditionally prefer `unified_orders` here (single source
    of truth) and skip the legacy path to avoid double-counting and to
    enforce the delivered-only rule.
    """
    settings = await ensure_user_settings(db, user_id)
    included_statuses = (
        settings.get("report_included_statuses")
        or DELIVERED_STATUSES_DEFAULT
    )
    cfg_map = await _shipping_config_map_db(db, user_id)
    deferred_set = set(await _deferred_company_names_db(db, user_id))

    out: dict[str, dict] = {}

    async for o in db.unified_orders.find(
        {"user_id": user_id},
        {"_id": 0, "order_status": 1, "shipping_company": 1, "shipping_cost": 1},
    ):
        if not _matches_any_sync(o.get("order_status", ""), included_statuses):
            continue
        canonical, cfg = _resolve_company_sync(o.get("shipping_company", ""), cfg_map)
        if not cfg or not cfg.get("is_deferred"):
            continue
        order_cost = float(o.get("shipping_cost") or 0)
        cfg_cost = float(cfg.get("cost") or 0)
        vat = float(cfg.get("vat_rate") or 0)
        cost = order_cost if order_cost > 0 else cfg_cost
        entry = out.setdefault(canonical, {
            "owed": 0.0,
            "orders_count": 0,
            "cost_per_order": cost,
        })
        entry["owed"] += round(cost * (1 + vat), 4)
        entry["orders_count"] += 1
        if cost > 0:
            entry["cost_per_order"] = cost

    for k in out:
        out[k]["owed"] = round(out[k]["owed"], 2)
    for n in deferred_set:
        out.setdefault(n, {"owed": 0.0, "orders_count": 0, "cost_per_order": 0.0})
    return out


async def compute_paid_per_company(db, user_id: str) -> dict[str, float]:
    """Total amount already paid (or deducted via COD net method) to each
    courier. Aggregated from `shipping_payments` keyed by `company_name`."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$company_name", "paid": {"$sum": "$amount"}}},
    ]
    out: dict[str, float] = {}
    async for doc in db.shipping_payments.aggregate(pipeline):
        out[(doc.get("_id") or "").strip()] = round(float(doc.get("paid", 0) or 0), 2)
    return out


def _build_router(db) -> APIRouter:
    router = APIRouter(prefix="/shipping-accounts", tags=["shipping-accounts"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _matches_any(value: str, allowed: list[str]) -> bool:
        if not allowed:
            return True
        v = (value or "").strip().lower()
        for a in allowed:
            a_lc = a.strip().lower()
            if a_lc and (a_lc == v or a_lc in v or v in a_lc):
                return True
        return False

    async def _deferred_company_names(user_id: str) -> list[str]:
        settings = await ensure_user_settings(db, user_id)
        return [
            (s.get("name") or "").strip()
            for s in settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES)
            if s.get("is_deferred") and (s.get("name") or "").strip()
        ]

    async def _shipping_config_map(user_id: str) -> dict[str, dict]:
        """Map shipping_company_name (lower) → config dict from settings."""
        settings = await ensure_user_settings(db, user_id)
        out: dict[str, dict] = {}
        for s in settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES):
            n = (s.get("name") or "").strip()
            if not n:
                continue
            out[n.lower()] = s
        return out

    def _resolve_company(name: str, cfg_map: dict[str, dict]) -> tuple[str, dict] | tuple[None, None]:
        """Resolve a raw order's shipping_company string to a configured deferred
        company entry (case-insensitive partial match)."""
        v = (name or "").strip().lower()
        if not v:
            return None, None
        # exact first
        if v in cfg_map:
            cfg = cfg_map[v]
            return cfg.get("name", "").strip(), cfg
        # substring
        for k, cfg in cfg_map.items():
            if k and (k in v or v in k):
                return cfg.get("name", "").strip(), cfg
        return None, None

    async def _owed_per_company(user_id: str) -> dict[str, dict]:
        # Iter-101 — delegate to module-level reusable helper.
        return await compute_owed_per_company(db, user_id)

    async def _paid_per_company(user_id: str) -> dict[str, float]:
        return await compute_paid_per_company(db, user_id)

    @router.get("")
    async def list_accounts(user: dict = Depends(current_user)):
        configured = await _deferred_company_names(user["id"])
        owed_map = await _owed_per_company(user["id"])
        paid_map = await _paid_per_company(user["id"])

        # Union: configured ∪ any company with accrued/paid balance in DB
        all_names = list({*configured, *owed_map.keys(), *paid_map.keys()})
        accounts = []
        for name in sorted(all_names):
            owed_info = owed_map.get(name, {"owed": 0.0, "orders_count": 0, "cost_per_order": 0.0})
            owed = owed_info["owed"]
            paid = paid_map.get(name, 0.0)
            accounts.append({
                "name": name,
                "is_configured": name in configured,
                "orders_count": owed_info["orders_count"],
                "cost_per_order": owed_info["cost_per_order"],
                "total_owed": round(owed, 2),
                "total_paid": round(paid, 2),
                "remaining": round(owed - paid, 2),
            })
        return {
            "accounts": accounts,
            "totals": {
                "total_owed": round(sum(a["total_owed"] for a in accounts), 2),
                "total_paid": round(sum(a["total_paid"] for a in accounts), 2),
                "remaining": round(sum(a["remaining"] for a in accounts), 2),
            },
        }

    @router.get("/{company}/payments")
    async def list_payments(company: str, user: dict = Depends(current_user)):
        items = await db.shipping_payments.find(
            {"user_id": user["id"], "company_name": company.strip()},
            {"_id": 0},
        ).sort("payment_date", -1).to_list(500)
        return {"payments": items}

    @router.post("/{company}/payments")
    async def add_payment(company: str, payload: PaymentIn, user: dict = Depends(current_user)):
        try:
            datetime.strptime(payload.payment_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")
        if not company.strip():
            raise HTTPException(status_code=400, detail="اسم الشركة مطلوب")

        # Iter-98 — normalise so SMSA / سمسا / smsa all collapse to "سمسا"
        from shipping_companies import scrub_shipping_company
        company_name = scrub_shipping_company(company.strip()) or company.strip()

        # Iter-95: validate the optional bank account if linked.
        linked_tx_id = None
        if payload.paid_from_account_id:
            acc = await db.accounts.find_one(
                {"id": payload.paid_from_account_id, "user_id": user["id"]},
                {"_id": 0, "id": 1, "name": 1},
            )
            if not acc:
                raise HTTPException(status_code=404, detail="الحساب المختار للدفع غير موجود")

        payment_id = str(uuid.uuid4())
        amount = round(float(payload.amount), 2)
        # Iter-98 — use the normalised company_name resolved above.
        invoice = (payload.invoice_number or "").strip()

        # Iter-95: post the bank movement first; if it fails, no payment row is left dangling.
        if payload.paid_from_account_id:
            linked_tx_id = await _post_shipping_payment_tx(
                db, user["id"],
                payment_id=payment_id,
                account_id=payload.paid_from_account_id,
                amount=amount,
                payment_date=payload.payment_date,
                company_name=company_name,
                invoice=invoice,
            )

        doc = {
            "id": payment_id,
            "user_id": user["id"],
            "company_name": company_name,
            "amount": amount,
            "payment_date": payload.payment_date,
            "invoice_number": invoice,
            "note": (payload.note or "").strip(),
            "paid_from_account_id": payload.paid_from_account_id,
            "linked_transaction_id": linked_tx_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.shipping_payments.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.delete("/payments/{payment_id}")
    async def delete_payment(payment_id: str, user: dict = Depends(current_user)):
        # Iter-95: roll back the linked bank movement if any.
        existing = await db.shipping_payments.find_one(
            {"id": payment_id, "user_id": user["id"]},
            {"_id": 0, "linked_transaction_id": 1, "paid_from_account_id": 1},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="الدفعة غير موجودة")
        if existing.get("linked_transaction_id") and existing.get("paid_from_account_id"):
            await _delete_shipping_payment_tx(
                db, user["id"],
                transaction_id=existing["linked_transaction_id"],
                account_id=existing["paid_from_account_id"],
            )
        await db.shipping_payments.delete_one(
            {"id": payment_id, "user_id": user["id"]}
        )
        return {"ok": True}

    # Iter-98 — return the canonical list of shipping companies the merchant
    # has actually used, sorted by usage frequency. Drawn from THREE sources
    # already in the DB (no new collection):
    #   • unified_orders.shipping_company   (the most authoritative source)
    #   • shipping_payments.company_name
    #   • transfers.shipping_company
    # All are pushed through `normalize_shipping_company()` so duplicate
    # spellings (SMSA / سمسا / smsa) collapse into the same canonical key.
    @router.get("/companies")
    async def list_shipping_companies(user: dict = Depends(current_user)):
        from shipping_companies import normalize_shipping_company
        from collections import Counter

        uid = user["id"]
        counter: Counter = Counter()
        display_by_key: dict[str, str] = {}

        async def _collect(coll, field):
            async for doc in db[coll].find(
                {"user_id": uid, field: {"$ne": None}},
                {"_id": 0, field: 1},
            ):
                raw = (doc.get(field) or "").strip()
                if not raw:
                    continue
                key, display = normalize_shipping_company(raw)
                counter[key] += 1
                # Prefer the canonical display name; fall back to the raw
                # value only when normalization couldn't classify it.
                display_by_key.setdefault(key, display)
                if display and not display.startswith("غير"):
                    display_by_key[key] = display

        await _collect("unified_orders", "shipping_company")
        await _collect("shipping_payments", "company_name")
        await _collect("transfers", "shipping_company")

        rows = [
            {
                "canonical": key,
                "display": display_by_key.get(key) or key,
                "usage_count": count,
            }
            for key, count in counter.most_common()
        ]

        # Add curated defaults that the user hasn't used yet so the
        # dropdown is never empty on a fresh account.
        from shipping_companies import (
            SMSA, IMILE, MANDOOB_RIYADH, ARAMEX, DHL, JT_EXPRESS,
        )
        DEFAULTS = [
            ("smsa", SMSA),
            ("imile", IMILE),
            ("mandoob_riyadh", MANDOOB_RIYADH),
            ("aramex", ARAMEX),
            ("dhl", DHL),
            ("jt_express", JT_EXPRESS),
        ]
        seen = {r["canonical"] for r in rows}
        for k, name in DEFAULTS:
            if k not in seen:
                rows.append({"canonical": k, "display": name, "usage_count": 0})

        return {"items": rows}

    return router


def attach_shipping_accounts_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
