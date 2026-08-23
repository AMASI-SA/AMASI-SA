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
from courier_cod_fee_rules import calculate_courier_cod_fee


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

    # Iter-240 — mirror this shipping payment into general_ledger (SSOT).
    try:
        from ledger_double_write import mirror_account_txn_to_ledger
        await mirror_account_txn_to_ledger(
            db,
            user_id=user_id,
            account_id=account_id,
            account_transaction_id=tx_id,
            amount=round(float(amount), 2),
            direction="out",
            transaction_type="shipping_debt_payment",
            transaction_date=payment_date,
            description=desc[:280],
            counter_entity_type="shipping_company",
            counter_entity_id=company_name or "shipping_unknown",
            created_by_endpoint="shipping_accounts._post_shipping_payment_tx",
            idempotency_key=f"shipping_payment:{payment_id}",
        )
    except Exception as _e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "iter240 mirror failed for shipping payment %s: %s", tx_id, _e
        )
    return tx_id


async def _delete_shipping_payment_tx(
    db, user_id: str, *, transaction_id: str, account_id: str,
) -> None:
    await db.account_transactions.delete_one(
        {"id": transaction_id, "user_id": user_id}
    )
    # Iter-240 — also purge the mirrored ledger pair.
    try:
        await db.general_ledger.delete_many({
            "user_id": user_id,
            "metadata.account_transaction_id": transaction_id,
            "metadata.source": "account_transaction_double_write",
        })
    except Exception:  # noqa: BLE001
        pass
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

    # Iter-149 v3 — apply COD cutoff so pre-accounting orders don't
    # inflate the shipping liability shown on `/api/liabilities/summary`
    # (و بالتالي على بطاقة المركز المالي).
    try:
        from accounting_cutoffs import get_cutoff
        cod_cutoff = await get_cutoff(db, user_id, "cod")
    except Exception:
        cod_cutoff = None

    orders_query: dict = {"user_id": user_id, "is_pre_accounting": {"$ne": True}}
    if cod_cutoff:
        orders_query["received_at"] = {"$gte": cod_cutoff + "T00:00:00"}

    out: dict[str, dict] = {}
    # Hoist SSOT import once per request (instead of per order).
    from shipping_cost_ssot import shipping_breakdown

    async for o in db.unified_orders.find(
        orders_query,
        {"_id": 0, "order_status": 1, "shipping_company": 1, "shipping_cost": 1},
    ):
        if not _matches_any_sync(o.get("order_status", ""), included_statuses):
            continue
        canonical, cfg = _resolve_company_sync(o.get("shipping_company", ""), cfg_map)
        if not cfg or not cfg.get("is_deferred"):
            continue
        # SSOT — same priority as the rest of the app: settings first,
        # Salla only when no settings cost.
        bd = shipping_breakdown(
            {"shipping_company": canonical,
             "shipping_cost": float(o.get("shipping_cost") or 0)},
            {canonical: cfg},
        )
        cost = bd["base"]    # the unit cost going INTO the liability
        total = bd["total"]  # base + tax
        entry = out.setdefault(canonical, {
            "owed": 0.0,
            "orders_count": 0,
            "cost_per_order": cost,
        })
        entry["owed"] += round(total, 4)
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
    courier. Aggregated from `shipping_payments` keyed by `company_name`.

    Iter-149 v3 — apply bank_transfer cutoff so pre-accounting payments
    are excluded.
    """
    try:
        from accounting_cutoffs import get_cutoff
        bank_cutoff = await get_cutoff(db, user_id, "bank_transfer")
    except Exception:
        bank_cutoff = None
    match: dict = {"user_id": user_id, "is_pre_accounting": {"$ne": True}}
    if bank_cutoff:
        match["payment_date"] = {"$gte": bank_cutoff}
    pipeline = [
        {"$match": match},
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

    # ─── Iter-144 — Per-company UNIFIED ledger ─────────────────
    # Combines:
    #   • COD approved (delivered orders)        — what couriers owe us
    #   • COD pending  (non-delivered)           — info only, NOT in net
    #   • Shipping cost (delivered)              — what we owe couriers
    #   • COD fees (% + fixed/order, delivered)  — what couriers charge us
    #   • courier_to_bank transfers              — money received from courier
    #   • bank_to_courier payments               — money we paid courier
    # net = cod_approved − shipping − cod_fee − courier_to_bank + bank_to_courier
    # Only DEFERRED companies enter the ledger.  Immediate companies
    # have shipping_cost booked as direct operating expense elsewhere.
    @router.get("/ledger")
    async def shipping_ledger(user: dict = Depends(current_user)):
        uid = user["id"]
        settings = await ensure_user_settings(db, uid)
        cod_approved_statuses = settings.get(
            "cod_approved_statuses",
        ) or ["تم التوصيل", "delivered", "completed"]
        cfg_map = await _shipping_config_map(uid)
        deferred_names = await _deferred_company_names(uid)

        def status_is_delivered(s: str) -> bool:
            return _matches_any(s or "", cod_approved_statuses)

        # 1) Walk unified_orders once — for each deferred company, accumulate
        #    delivered COD, pending COD, delivered shipping cost, delivered order count.
        per_company: dict[str, dict] = {}
        for n in deferred_names:
            per_company[n] = {
                "name": n, "is_deferred": True,
                "cod_approved": 0.0, "cod_pending": 0.0,
                "shipping_cost": 0.0,
                "delivered_orders_count": 0,
                "pending_cod_orders_count": 0,
                "cod_fee_net": 0.0,
                "cod_fee_vat": 0.0,
                "cod_fee_total": 0.0,
                "cod_fee_rules_needing_review": 0,
                "cod_fee_percent": float(cfg_map.get(n.lower(), {}).get("cod_fee_percent") or 0),
                "cod_fee_fixed_per_order": float(cfg_map.get(n.lower(), {}).get("cod_fee_fixed_per_order") or 0),
                "vat_rate": float(cfg_map.get(n.lower(), {}).get("vat_rate") or 0),
                "cod_fee_config": cfg_map.get(n.lower(), {}),
            }

        from balances import _is_cod_method
        # Iter-149 v3 — pull both relevant cutoffs.  Shipping (COD)
        # orders are scoped to the `cod` cutoff; courier_transfers to
        # the `bank_transfer` cutoff (they're bank-side cash movements).
        try:
            from accounting_cutoffs import get_cutoff
            cod_cutoff = await get_cutoff(db, uid, "cod")
            bank_cutoff = await get_cutoff(db, uid, "bank_transfer")
        except Exception:
            cod_cutoff = None
            bank_cutoff = None

        orders_query: dict = {"user_id": uid, "is_pre_accounting": {"$ne": True}}
        if cod_cutoff:
            orders_query["received_at"] = {"$gte": cod_cutoff + "T00:00:00"}

        # ─ SSOT shipping-cost helper ─
        from shipping_cost_ssot import shipping_breakdown
        # Build name → cfg map used by the SSOT helper (resolves canonical
        # names through the same _resolve_company aliases).
        ssot_cfg_map: dict = {}
        for canonical, cfg in cfg_map.items():
            ssot_cfg_map[canonical] = cfg

        async for o in db.unified_orders.find(
            orders_query,
            {"_id": 0, "order_status": 1, "shipping_company": 1,
             "shipping_cost": 1, "total_amount": 1, "payment_method": 1},
        ):
            canonical, cfg = _resolve_company(o.get("shipping_company", ""), cfg_map)
            if not cfg or not cfg.get("is_deferred"):
                continue   # Immediate companies excluded — booked as direct expense.
            row = per_company.setdefault(canonical, {
                "name": canonical, "is_deferred": True,
                "cod_approved": 0.0, "cod_pending": 0.0,
                "shipping_base": 0.0,   # base only (no VAT)
                "shipping_tax":  0.0,   # VAT amount
                "shipping_cost": 0.0,   # = base + tax (kept for backward compat)
                "delivered_orders_count": 0, "pending_cod_orders_count": 0,
                "cod_fee_net": 0.0,
                "cod_fee_vat": 0.0,
                "cod_fee_total": 0.0,
                "cod_fee_rules_needing_review": 0,
                "cod_fee_percent": float(cfg.get("cod_fee_percent") or 0),
                "cod_fee_fixed_per_order": float(cfg.get("cod_fee_fixed_per_order") or 0),
                "vat_rate": float(cfg.get("vat_rate") or 0),
                "cod_fee_config": cfg,
            })
            delivered = status_is_delivered(o.get("order_status", ""))
            is_cod = _is_cod_method(o.get("payment_method") or "")
            total = float(o.get("total_amount") or 0)
            # Use the SSOT helper; normalize the order's company to canonical
            # so the helper finds the matching config.
            order_for_calc = {**o, "shipping_company": canonical}
            bd = shipping_breakdown(order_for_calc, {canonical: cfg})
            if delivered:
                row["delivered_orders_count"] += 1
                row["shipping_base"] += bd["base"]
                row["shipping_tax"]  += bd["tax"]
                row["shipping_cost"] += bd["total"]   # base + tax
                if is_cod:
                    row["cod_approved"] += total
                    fee_calc = calculate_courier_cod_fee(total, cfg)
                    row["cod_fee_net"] += fee_calc["fee_net"]
                    row["cod_fee_vat"] += fee_calc["fee_vat"]
                    row["cod_fee_total"] += fee_calc["fee_total"]
                    if fee_calc.get("needs_review"):
                        row["cod_fee_rules_needing_review"] += 1
            else:
                if is_cod:
                    row["cod_pending"] += total
                    row["pending_cod_orders_count"] += 1

        # 2) Fold in courier_transfers (both directions).
        # Iter-149 v3 — filter by bank_transfer cutoff so pre-accounting
        # courier transfers don't affect the ledger.
        transfer_query: dict = {"user_id": uid}
        if bank_cutoff:
            transfer_query["transfer_date"] = {"$gte": bank_cutoff}
        async for t in db.courier_transfers.find(
            transfer_query, {"_id": 0},
        ):
            name = (t.get("company_name") or "").strip()
            if name not in per_company:
                continue
            amt = float(t.get("amount") or 0)
            direction = t.get("direction") or "courier_to_bank"
            if direction == "courier_to_bank":
                per_company[name].setdefault("courier_to_bank", 0.0)
                per_company[name]["courier_to_bank"] += amt
            elif direction == "bank_to_courier":
                per_company[name].setdefault("bank_to_courier", 0.0)
                per_company[name]["bank_to_courier"] += amt

        # 3) Compute COD fees and net per company.
        rows = []
        for name, r in per_company.items():
            cod_approved = round(r["cod_approved"], 2)
            shipping_base = round(r.get("shipping_base", 0.0), 2)
            shipping_tax = round(r.get("shipping_tax", 0.0), 2)
            shipping_cost = round(r["shipping_cost"], 2)   # = base + tax
            cod_fee_net = round(r.get("cod_fee_net", 0.0), 2)
            cod_fee_vat = round(r.get("cod_fee_vat", 0.0), 2)
            cod_fee = round(r.get("cod_fee_total", 0.0), 2)
            c2b = round(r.get("courier_to_bank", 0.0), 2)
            b2c = round(r.get("bank_to_courier", 0.0), 2)
            net = round(cod_approved - shipping_cost - cod_fee - c2b + b2c, 2)
            interpretation = (
                "لنا عند الشركة" if net > 0.005
                else "علينا للشركة" if net < -0.005
                else "متوازن"
            )
            rows.append({
                "name": name,
                "is_deferred": True,
                "cod_approved": cod_approved,
                "cod_pending": round(r["cod_pending"], 2),
                "shipping_base":  shipping_base,
                "shipping_tax":   shipping_tax,
                "shipping_cost":  shipping_cost,   # base + tax (SSOT)
                "cod_fee": cod_fee,
                "cod_fee_net": cod_fee_net,
                "cod_fee_vat": cod_fee_vat,
                "cod_fee_rule_mode": (
                    "tiered" if (r.get("cod_fee_config") or {}).get("cod_fee_tiers")
                    else "flat"
                ),
                "cod_fee_rules_needing_review": int(
                    r.get("cod_fee_rules_needing_review") or 0
                ),
                "courier_to_bank": c2b,
                "bank_to_courier": b2c,
                "net_balance": net,
                "interpretation": interpretation,
                "delivered_orders_count": r["delivered_orders_count"],
                "pending_cod_orders_count": r["pending_cod_orders_count"],
                "cod_fee_percent": r["cod_fee_percent"],
                "cod_fee_fixed_per_order": r["cod_fee_fixed_per_order"],
                "vat_rate":       r.get("vat_rate", 0.0),
            })
        rows.sort(key=lambda x: (-abs(x["net_balance"]), x["name"]))

        # 4) Top-line totals (deferred companies only).
        totals = {
            "cod_approved": round(sum(r["cod_approved"] for r in rows), 2),
            "cod_pending":  round(sum(r["cod_pending"]  for r in rows), 2),
            "shipping_base": round(sum(r.get("shipping_base", 0) for r in rows), 2),
            "shipping_tax":  round(sum(r.get("shipping_tax", 0)  for r in rows), 2),
            "shipping_cost": round(sum(r["shipping_cost"] for r in rows), 2),  # base+tax
            "cod_fee":     round(sum(r["cod_fee"]     for r in rows), 2),
            "cod_fee_net": round(sum(r.get("cod_fee_net", 0) for r in rows), 2),
            "cod_fee_vat": round(sum(r.get("cod_fee_vat", 0) for r in rows), 2),
            "cod_fee_rules_needing_review": sum(
                int(r.get("cod_fee_rules_needing_review") or 0) for r in rows
            ),
            "courier_to_bank": round(sum(r["courier_to_bank"] for r in rows), 2),
            "bank_to_courier": round(sum(r["bank_to_courier"] for r in rows), 2),
            "net_owed_to_us": round(sum(r["net_balance"] for r in rows if r["net_balance"] > 0), 2),
            "net_owed_by_us": round(abs(sum(r["net_balance"] for r in rows if r["net_balance"] < 0)), 2),
            "net_balance":   round(sum(r["net_balance"] for r in rows), 2),
        }
        return {"companies": rows, "totals": totals}

    # ─── Iter-144 — Courier transfers (two-way ledger movements) ──
    @router.get("/transfers")
    async def list_courier_transfers(
        company: Optional[str] = None,
        direction: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        q = {"user_id": user["id"]}
        if company:
            q["company_name"] = company.strip()
        if direction in ("courier_to_bank", "bank_to_courier"):
            q["direction"] = direction
        items = await db.courier_transfers.find(q, {"_id": 0}).sort(
            "transfer_date", -1,
        ).to_list(1000)
        return {"items": items}

    @router.post("/transfers")
    async def add_courier_transfer(payload: dict, user: dict = Depends(current_user)):
        uid = user["id"]
        company = (payload.get("company_name") or "").strip()
        direction = payload.get("direction") or "courier_to_bank"
        amount = round(float(payload.get("amount") or 0), 2)
        date_str = (payload.get("transfer_date") or "").strip()
        if not company:
            raise HTTPException(400, "اسم شركة الشحن مطلوب")
        if direction not in ("courier_to_bank", "bank_to_courier"):
            raise HTTPException(400, "اتجاه التحويل غير صحيح")
        if amount <= 0:
            raise HTTPException(400, "المبلغ يجب أن يكون موجباً")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "صيغة التاريخ يجب أن تكون YYYY-MM-DD")
        bank_id = payload.get("bank_account_id") or None
        if bank_id:
            acc = await db.accounts.find_one(
                {"id": bank_id, "user_id": uid},
                {"_id": 0, "id": 1, "current_balance": 1, "name": 1},
            )
            if not acc:
                raise HTTPException(404, "الحساب البنكي المختار غير موجود")
            # Iter-152 — Block outgoing transfers if bank balance is
            # insufficient.  Applies ONLY to bank_to_courier (cash
            # leaving the bank).  courier_to_bank is INCOMING money so
            # no balance check needed.
            if direction == "bank_to_courier":
                bal = float(acc.get("current_balance") or 0)
                if bal + 0.01 < amount:
                    raise HTTPException(
                        400,
                        f"رصيد الحساب البنكي «{acc.get('name','')}» غير كافٍ — "
                        f"المتاح {bal:.2f} ر.س والمحاولة لتحويل {amount:.2f} ر.س",
                    )

        # Iter-152 — Validate against the courier's outstanding balance
        # from the live ledger.  We piggy-back the existing /ledger
        # endpoint by reading the in-memory rows for this company.
        # Convention (from line 614):
        #   net_balance = cod_approved − shipping_cost − cod_fee
        #                 − courier_to_bank + bank_to_courier
        # net_balance > 0 → courier owes us (لنا عند الشركة).
        # net_balance < 0 → we owe courier (علينا للشركة).
        try:
            ledger = await shipping_ledger(user=user)  # reuse same router fn
            company_row = next(
                (r for r in (ledger.get("companies") or [])
                 if (r.get("name") or "").lower() == company.lower()),
                None,
            )
        except Exception:
            company_row = None

        # Don't enforce balance rules for non-deferred / unknown
        # companies (the ledger only tracks deferred ones).
        if company_row is not None:
            net = float(company_row.get("net_balance") or 0)
            if direction == "courier_to_bank":
                # The courier is sending us money — they cannot send
                # more than what they actually owe us.
                if net <= 0:
                    raise HTTPException(
                        400,
                        f"لا توجد مديونية حالية على شركة «{company}» — "
                        f"رصيدها الحالي {net:.2f} ر.س ولا يمكن استلام تحويل منها",
                    )
                if amount > net + 0.01:
                    raise HTTPException(
                        400,
                        f"المبلغ ({amount:.2f} ر.س) أكبر من المستحق على «{company}» "
                        f"({net:.2f} ر.س). يمكن استلام مبلغ يساوي أو أقل من المستحق فقط.",
                    )
            else:  # bank_to_courier
                # We're paying the courier — over-payment is allowed
                # (it shifts the net balance into "courier owes us")
                # but we flag it so the UI can warn the merchant.
                owed_to_courier = max(0.0, -net)
                # Save the over-payment delta in the response.
                overpayment = max(0.0, round(amount - owed_to_courier, 2))
                # No exception — caller is informed via the response.

        # Post the bank movement (in=courier_to_bank, out=bank_to_courier)
        tx_id = None
        if bank_id:
            tx_id = str(uuid.uuid4())
            await db.account_transactions.insert_one({
                "id": tx_id, "user_id": uid, "account_id": bank_id,
                "transaction_type": "courier_transfer",
                "amount": amount,
                "direction": "in" if direction == "courier_to_bank" else "out",
                "description": (
                    f"تحويل من شركة الشحن — {company}"
                    if direction == "courier_to_bank"
                    else f"دفع لشركة الشحن — {company}"
                ),
                "transaction_date": date_str,
                "balance_after": 0.0,
                "status": "posted",
                "reference": (payload.get("reference") or "")[:64],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            await _recompute_shipping_account_balance(db, uid, bank_id)
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "company_name": company,
            "direction": direction,
            "amount": amount,
            "transfer_date": date_str,
            "bank_account_id": bank_id,
            "linked_transaction_id": tx_id,
            "reference": (payload.get("reference") or "").strip(),
            "note": (payload.get("note") or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.courier_transfers.insert_one(doc)
        doc.pop("_id", None)
        # Surface the over-payment notice to the UI (non-blocking).
        if direction == "bank_to_courier" and company_row is not None:
            net = float(company_row.get("net_balance") or 0)
            owed = max(0.0, -net)
            over = max(0.0, round(amount - owed, 2))
            if over > 0.01:
                doc["overpayment"] = over
                doc["overpayment_note"] = (
                    f"تم دفع مبلغ يزيد عن المستحق بـ {over:.2f} ر.س — "
                    f"أصبحت «{company}» مدينة لك بهذا المبلغ."
                )
        return doc

    @router.delete("/transfers/{transfer_id}")
    async def delete_courier_transfer(transfer_id: str, user: dict = Depends(current_user)):
        uid = user["id"]
        t = await db.courier_transfers.find_one(
            {"id": transfer_id, "user_id": uid}, {"_id": 0},
        )
        if not t:
            raise HTTPException(404, "التحويل غير موجود")
        if t.get("linked_transaction_id") and t.get("bank_account_id"):
            await db.account_transactions.delete_one(
                {"id": t["linked_transaction_id"], "user_id": uid},
            )
            await _recompute_shipping_account_balance(db, uid, t["bank_account_id"])
        await db.courier_transfers.delete_one({"id": transfer_id, "user_id": uid})
        return {"ok": True}

    return router


def attach_shipping_accounts_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
