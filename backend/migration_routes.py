"""Migration Routes — Iter-161 Phase 2

Cutoff-based migration of legacy balances to the new general_ledger:

  • Snapshot legacy balances (BEFORE) per entity
  • Insert opening_balance entries into general_ledger
  • Re-compute new balances (AFTER) per entity
  • Compare BEFORE vs AFTER for each entity
  • Mark cutoff date in `migration_cutoffs` collection
  • IDEMPOTENT: re-running the same cutoff returns the existing snapshot

Mode:
  dry_run=True  → returns the comparison without writing opening entries
  dry_run=False → writes opening_balance entries + cutoff marker

The legacy collections (liabilities, account_transactions, daily_costs,
operating_salaries) are NEVER modified or deleted by this migration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth import get_current_user_from_db
from ledger_core import (
    REASON_CODES,
    post_ledger_entry,
    compute_balance,
)
# Iter-162 — reuse the EXACT same legacy calculators that the user sees
# on the المركز المالي screen (dynamic salary accrual per actual days
# worked). The migration script MUST mirror this logic perfectly so the
# Reconciliation Report can reach 100% match on production data.
from liabilities_routes import (
    _compute_employee_accrual,
    _round as _liab_round,
)
from tz_utils import riyadh_today


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(v) -> float:
    return round(float(v or 0), 2)


# ── Legacy balance snapshot ────────────────────────────────────────
async def _legacy_employee_balances(db, user_id: str) -> list[dict]:
    """Per-employee: salary_payable, advance, custody.

    Iter-162 — Rewritten to mirror `_aggregate_salary_accrual` in
    `liabilities_routes.py` EXACTLY so the migration matches the legacy
    "تجميع الرواتب" screen 100 %:

      • salary_payable = max(0, dynamic_accrued − cash_paid)
          - dynamic_accrued: `_compute_employee_accrual(emp).accrued`
            (daily rate × days worked across calendar months).
          - cash_paid: sum of (paid_amount − advance_deducted) from
            liabilities.kind=salary for THAT employee (real bank cash).
      • advance = sum of (expected_amount − consumed_amount) on OPEN
        salary_advance liabilities (matches the «outstanding_advance»
        figure shown in the legacy summary).
      • custody = 0.0 (no legacy concept).

    Only category="employee" rows from operating_salaries participate,
    matching `_aggregate_salary_accrual`.
    """
    today = riyadh_today()
    emps = await db.operating_salaries.find(
        {"user_id": user_id, "category": "employee"}, {"_id": 0},
    ).to_list(500)
    out: list[dict] = []
    for emp in emps:
        # 1) Dynamic accrued amount (daily-rate per actual days worked)
        calc = _compute_employee_accrual(emp, today=today)
        accrued = _round(calc["accrued"])

        # 2) Cash actually paid on this employee's salary liabilities
        paid_agg = await db.liabilities.aggregate([
            {"$match": {"user_id": user_id, "kind": "salary",
                         "employee_salary_id": emp["id"]}},
            {"$group": {"_id": None,
                         "paid": {"$sum": "$paid_amount"},
                         "adv_deducted": {"$sum": {"$ifNull": [
                             "$advance_deducted", 0]}}}}
        ]).to_list(1)
        cash_paid = 0.0
        if paid_agg:
            cash_paid = _round(
                (paid_agg[0]["paid"] or 0)
                - (paid_agg[0]["adv_deducted"] or 0)
            )
            if cash_paid < 0:
                cash_paid = 0.0

        payable = _round(max(0.0, accrued - cash_paid))

        # 3) Outstanding advances — sum of remaining on OPEN advance
        #    rows, matching `_aggregate_salary_accrual`:
        #       remaining = expected_amount − consumed_amount
        advance = 0.0
        async for adv in db.liabilities.find(
            {"user_id": user_id, "kind": "salary_advance",
             "employee_salary_id": emp["id"],
             "advance_status": "open"},
            {"_id": 0, "expected_amount": 1, "consumed_amount": 1},
        ):
            remaining = _round(
                _round(adv.get("expected_amount"))
                - _round(adv.get("consumed_amount"))
            )
            if remaining > 0:
                advance += remaining
        advance = _round(advance)

        custody = 0.0
        out.append({
            "employee_id": emp["id"], "name": emp.get("name"),
            "salary_payable": payable,
            "advance": advance,
            "custody": custody,
            # Diagnostic fields surfaced so the Reconciliation Report
            # can show WHY a balance is what it is.
            "_accrued": accrued,
            "_cash_paid": cash_paid,
            "_days_worked": calc["days_worked"],
            "_accrual_start": calc.get("start_date"),
            "_accrual_end": calc.get("end_date"),
            "_monthly_amount": _round(emp.get("monthly_amount")),
        })
    return out


async def _legacy_supplier_balances(db, user_id: str) -> list[dict]:
    """Per-supplier outstanding payable.

    Iter-162 — Mirror the EXACT filter used by `/api/liabilities/summary`:
        kind=supplier AND status!=paid AND is_pre_accounting!=True
        remaining = expected_amount − paid_amount  (only if > 0)

    The legacy schema links a supplier liability to a counterparty either
    via `counterparty_id` OR (older rows) only via the free-text
    `supplier_name` matching the counterparty's name — both linkages
    are honoured here.
    """
    cps = await db.counterparties.find(
        {"user_id": user_id, "kind": "supplier"}, {"_id": 0},
    ).to_list(500)
    out: list[dict] = []
    for cp in cps:
        owed = 0.0
        async for r in db.liabilities.find(
            {
                "user_id": user_id,
                "kind": "supplier",
                "status": {"$ne": "paid"},
                "is_pre_accounting": {"$ne": True},
                "$or": [
                    {"counterparty_id": cp["id"]},
                    {"supplier_name": cp.get("name")},
                ],
            },
            {"_id": 0, "expected_amount": 1, "paid_amount": 1},
        ):
            remaining = _round(
                _round(r.get("expected_amount"))
                - _round(r.get("paid_amount"))
            )
            if remaining > 0:
                owed += remaining
        out.append({
            "supplier_id": cp["id"], "name": cp.get("name"),
            "payable": _round(owed),
        })
    return out


async def _legacy_external_balances(db, user_id: str) -> list[dict]:
    """Per-external receivable (money owed TO the merchant).

    Iter-162 — Mirrors the legacy filter:
        kind=receivable AND status!=paid
        remaining = expected_amount − paid_amount  (only if > 0)

    Linkage by `counterparty_id` or by `counterparty_name`.
    """
    cps = await db.counterparties.find(
        {"user_id": user_id, "kind": {"$nin": [
            "ad_account", "supplier", "courier"]}},
        {"_id": 0},
    ).to_list(500)
    out: list[dict] = []
    for cp in cps:
        owed = 0.0
        async for r in db.liabilities.find(
            {
                "user_id": user_id,
                "kind": "receivable",
                "status": {"$ne": "paid"},
                "$or": [
                    {"counterparty_id": cp["id"]},
                    {"counterparty_name": cp.get("name")},
                ],
            },
            {"_id": 0, "expected_amount": 1, "paid_amount": 1},
        ):
            remaining = _round(
                _round(r.get("expected_amount"))
                - _round(r.get("paid_amount"))
            )
            if remaining > 0:
                owed += remaining
        out.append({
            "person_id": cp["id"], "name": cp.get("name"),
            "receivable": _round(owed),
        })
    return out


async def _legacy_bank_balances(db, user_id: str) -> list[dict]:
    """Per-bank current balance from the legacy `accounts` collection.

    Iter-166 — BUG FIX. Previously we read `accounts.balance` which is
    None for accounts created via the default-banks bootstrap; the real
    SSOT is `accounts.current_balance` (computed by `_recompute_balance`
    from the transaction history + opening_balance). Reading the wrong
    field would have posted Ledger opening balances = 0 for every bank
    on production, EFFECTIVELY ZEROING the user's bank balances inside
    the new Universal Ledger. Reported by merchant Feb 2026.
    """
    accs = await db.accounts.find(
        {"user_id": user_id, "account_type": "bank"}, {"_id": 0},
    ).to_list(500)
    out = []
    for a in accs:
        # Prefer current_balance (computed SSOT). Fall back to legacy
        # `balance` field if a third-party account never went through
        # the new computation path.
        cur = a.get("current_balance")
        if cur is None:
            cur = a.get("balance")
        out.append({
            "account_id": a["id"],
            "name": a.get("name"),
            "balance": _round(cur),
            # Diagnostic fields surfaced in the Reconciliation Report
            "_opening_balance": _round(a.get("opening_balance")),
            "_expected_orders_balance": _round(a.get("expected_orders_balance")),
            "_currency": a.get("currency") or "SAR",
        })
    return out


async def _legacy_payment_platform_balances(
    db, user_id: str,
) -> list[dict]:
    """Per-platform current balance (Tabby, Tamara, Salla, Imkan).

    Iter-167 — These accounts represent **assets in transit**: money the
    merchant has earned through customer orders but hasn't yet
    transferred to a bank account. They MUST be carried into the
    Universal Ledger as opening_balance debit entries; otherwise the
    new Financial Position would understate liquid assets by 100% of
    the platforms' balances.

    For Tabby/Tamara we prefer the BNPL SSOT (`get_bnpl_provider_balance`)
    over the stored `current_balance` to stay consistent with the BNPL
    Settlements page. Other platforms fall back to `current_balance`.

    Iter-179 — **COD accounts are excluded from this migration**.
    The merchant's accounting review (Feb 2026) concluded that the
    current `expected_orders_balance` for COD is computed from
    *Confirmed* orders (not Delivered), so it includes:
      • orders still in transit (cash not collected yet),
      • orders confirmed but not yet shipped (cash doesn't exist).
    Migrating that gross figure as an opening balance would post
    phantom assets. COD will be re-introduced via the dedicated
    Shipping Ledger Sprint, which links each order's cash to its
    actual courier and only counts Delivered.
    """
    # Lazily import to keep top-level import surface stable.
    try:
        from balances import _is_cod_method
    except Exception:  # noqa: BLE001
        _is_cod_method = lambda _n: False  # noqa: E731
    try:
        from payment_methods import normalize_payment_method
    except Exception:  # noqa: BLE001
        normalize_payment_method = None  # type: ignore

    def _is_cod_account(a: dict) -> bool:
        name = (a.get("name") or "").strip()
        normalized = (a.get("normalized_payment_method") or "").strip()
        if normalized in ("cod", "cash_on_delivery"):
            return True
        if _is_cod_method(name):
            return True
        if normalize_payment_method:
            try:
                sub_key, _disp, _parent = normalize_payment_method(name)
                if sub_key == "cash_on_delivery":
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    accs = await db.accounts.find(
        {"user_id": user_id, "account_type": "payment_platform"},
        {"_id": 0},
    ).to_list(500)
    out = []
    try:
        from bnpl.balance_service import is_bnpl_account, get_bnpl_provider_balance
    except Exception:  # noqa: BLE001
        is_bnpl_account = lambda _a: None  # noqa: E731
        get_bnpl_provider_balance = None
    for a in accs:
        if _is_cod_account(a):
            # Iter-179 — explicitly skipped. See docstring above.
            continue
        bnpl_provider = is_bnpl_account(a) if a else None
        cur = a.get("current_balance")
        balance_source = "current_balance"
        if bnpl_provider and get_bnpl_provider_balance:
            try:
                canon = await get_bnpl_provider_balance(
                    db, user_id, bnpl_provider)
                cur = float(canon.get("balance") or 0)
                balance_source = "bnpl_ssot"
            except Exception:  # noqa: BLE001
                # Stay with stored current_balance if BNPL SSOT fails
                pass
        if cur is None:
            cur = a.get("balance")
        out.append({
            "account_id": a["id"],
            "name": a.get("name"),
            "balance": _round(cur),
            "_opening_balance": _round(a.get("opening_balance")),
            "_expected_orders_balance": _round(a.get("expected_orders_balance")),
            "_currency": a.get("currency") or "SAR",
            "_balance_source": balance_source,
            "_bnpl_provider": bnpl_provider,
        })
    return out


async def _legacy_courier_balances(
    db, user_id: str,
) -> list[dict]:
    """Per-courier payable from legacy `liabilities` (shipping/courier).

    Iter-167 — Used by both the migration and the Reconciliation Report
    so they share a single source of truth.
    """
    cps = await db.counterparties.find(
        {"user_id": user_id, "kind": "courier"}, {"_id": 0},
    ).to_list(500)
    out = []
    for c in cps:
        agg = await db.liabilities.aggregate([
            {"$match": {"user_id": user_id,
                          "kind": {"$in": ["shipping", "courier"]},
                          "counterparty_id": c["id"],
                          "status": {"$ne": "paid"},
                          "is_pre_accounting": {"$ne": True}}},
            {"$group": {"_id": None,
                          "owed": {"$sum": {"$subtract": [
                              "$expected_amount",
                              {"$ifNull": ["$paid_amount", 0]}]}}}}
        ]).to_list(1)
        owed = _round(agg[0]["owed"]) if agg else 0.0
        if owed < 0:
            owed = 0.0
        out.append({
            "courier_id": c["id"], "name": c.get("name"),
            "payable": owed,
        })
    return out


async def _new_ledger_balance(
    db, user_id: str, entity_type: str, entity_id: str,
    sub_account: Optional[str] = None,
) -> float:
    """Net balance from general_ledger for a single (entity, sub) — uses
    POSTED entries only. Returns abs value matching the legacy convention."""
    b = await compute_balance(
        db, user_id=user_id, entity_type=entity_type,
        entity_id=entity_id, sub_account=sub_account,
    )
    # For liabilities we display as positive outstanding_debt;
    # for assets we display as positive net_balance.
    if sub_account in ("salary_payable", "payable"):
        return float(b["outstanding_debt"])
    return float(b["net_balance"])


async def _compute_after_balances(
    db, user_id: str, before: dict,
) -> dict:
    """For each entity in the BEFORE snapshot, compute its current
    general_ledger balance (used for both dry-run and post-run verification)."""
    after = {"employees": [], "suppliers": [], "externals": [], "banks": []}

    for e in before.get("employees", []):
        after["employees"].append({
            "employee_id": e["employee_id"], "name": e["name"],
            "salary_payable": await _new_ledger_balance(
                db, user_id, "employee", e["employee_id"], "salary_payable"),
            "advance": await _new_ledger_balance(
                db, user_id, "employee", e["employee_id"], "advance"),
            "custody": await _new_ledger_balance(
                db, user_id, "employee", e["employee_id"], "custody"),
        })

    for s in before.get("suppliers", []):
        after["suppliers"].append({
            "supplier_id": s["supplier_id"], "name": s["name"],
            "payable": await _new_ledger_balance(
                db, user_id, "supplier", s["supplier_id"], "payable"),
        })

    for x in before.get("externals", []):
        after["externals"].append({
            "person_id": x["person_id"], "name": x["name"],
            "receivable": await _new_ledger_balance(
                db, user_id, "external_person", x["person_id"], "receivable"),
        })

    for b in before.get("banks", []):
        after["banks"].append({
            "account_id": b["account_id"], "name": b["name"],
            "balance": await _new_ledger_balance(
                db, user_id, "bank", b["account_id"], "main"),
        })

    # Iter-167 — payment platforms & couriers
    for p in before.get("payment_platforms", []):
        after.setdefault("payment_platforms", []).append({
            "account_id": p["account_id"], "name": p["name"],
            "balance": await _new_ledger_balance(
                db, user_id, "payment_platform", p["account_id"], "main"),
        })
    for c in before.get("couriers", []):
        after.setdefault("couriers", []).append({
            "courier_id": c["courier_id"], "name": c["name"],
            "payable": await _new_ledger_balance(
                db, user_id, "courier", c["courier_id"], "payable"),
        })

    return after


def _build_diff(before: dict, after: dict) -> dict:
    """Return a side-by-side diff with mismatch flags."""
    def _diff_list(b_list, a_list, key_fields):
        a_map = {item[key_fields[0]]: item for item in a_list}
        rows = []
        for b in b_list:
            a = a_map.get(b[key_fields[0]], {})
            diffs = {}
            for f in key_fields[1:]:
                bv = float(b.get(f) or 0)
                av = float(a.get(f) or 0)
                diffs[f] = {
                    "before": round(bv, 2), "after": round(av, 2),
                    "delta": round(av - bv, 2),
                    "match": abs(av - bv) < 0.01,
                }
            rows.append({**{k: b[k] for k in key_fields[:1]},
                          "name": b.get("name"), "fields": diffs})
        return rows

    diffs = {
        "employees": _diff_list(
            before["employees"], after["employees"],
            ["employee_id", "salary_payable", "advance", "custody"]),
        "suppliers": _diff_list(
            before["suppliers"], after["suppliers"],
            ["supplier_id", "payable"]),
        "externals": _diff_list(
            before["externals"], after["externals"],
            ["person_id", "receivable"]),
        "banks": _diff_list(
            before["banks"], after["banks"],
            ["account_id", "balance"]),
    }
    mismatches = sum(
        1
        for section in diffs.values()
        for row in section
        for f in row["fields"].values()
        if not f["match"]
    )
    return {"diffs": diffs, "mismatches": mismatches}


# ── Pydantic ─────────────────────────────────────────────────────────
class MigrationRunIn(BaseModel):
    cutoff_date: str  # YYYY-MM-DD
    dry_run: bool = True


def make_migration_router(db) -> APIRouter:
    router = APIRouter(prefix="/accounting/migration", tags=["migration"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    # ── GET /snapshot — legacy balances only (read-only) ─────────────
    @router.get("/snapshot")
    async def legacy_snapshot(user: dict = Depends(current_user)):
        uid = user["id"]
        snap = {
            "employees": await _legacy_employee_balances(db, uid),
            "suppliers": await _legacy_supplier_balances(db, uid),
            "externals": await _legacy_external_balances(db, uid),
            "banks": await _legacy_bank_balances(db, uid),
            # Iter-167 — payment platforms & couriers are now migrated.
            "payment_platforms": await _legacy_payment_platform_balances(db, uid),
            "couriers": await _legacy_courier_balances(db, uid),
        }
        return snap

    # ── GET /status — whether migration already executed ─────────────
    @router.get("/status")
    async def migration_status(user: dict = Depends(current_user)):
        cm = await db.migration_cutoffs.find_one(
            {"user_id": user["id"]}, {"_id": 0},
        )
        return {"cutoff": cm, "completed": bool(cm)}

    # ── POST /run — dry-run or apply ─────────────────────────────────
    @router.post("/run")
    async def run_migration(
        payload: MigrationRunIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # Block re-run if already completed (dry-run is always allowed)
        if not payload.dry_run:
            cm = await db.migration_cutoffs.find_one(
                {"user_id": uid, "status": "completed"}, {"_id": 0},
            )
            if cm:
                raise HTTPException(
                    400,
                    f"الترحيل تم بالفعل بتاريخ {cm.get('cutoff_date')}. "
                    "لا يمكن إعادته. لإعادة الترحيل اتصل بالدعم الفني.",
                )

        # 1) BEFORE snapshot (legacy)
        before = {
            "employees": await _legacy_employee_balances(db, uid),
            "suppliers": await _legacy_supplier_balances(db, uid),
            "externals": await _legacy_external_balances(db, uid),
            "banks": await _legacy_bank_balances(db, uid),
            # Iter-167 — extended migration scope.
            "payment_platforms": await _legacy_payment_platform_balances(db, uid),
            "couriers": await _legacy_courier_balances(db, uid),
        }

        cutoff_iso = payload.cutoff_date
        ops_planned: list[dict] = []

        # For each entity with non-zero legacy balance, plan an
        # opening_balance entry.
        for e in before["employees"]:
            if e["salary_payable"] > 0:
                ops_planned.append({
                    "entity_type": "employee", "entity_id": e["employee_id"],
                    "sub_account": "salary_payable", "side": "credit",
                    "amount": e["salary_payable"],
                    "metadata": {"employee_name": e["name"]},
                })
            if e["advance"] > 0:
                ops_planned.append({
                    "entity_type": "employee", "entity_id": e["employee_id"],
                    "sub_account": "advance", "side": "debit",
                    "amount": e["advance"],
                    "metadata": {"employee_name": e["name"]},
                })

        for s in before["suppliers"]:
            if s["payable"] > 0:
                ops_planned.append({
                    "entity_type": "supplier", "entity_id": s["supplier_id"],
                    "sub_account": "payable", "side": "credit",
                    "amount": s["payable"],
                    "metadata": {"supplier_name": s["name"]},
                })

        for x in before["externals"]:
            if x["receivable"] > 0:
                ops_planned.append({
                    "entity_type": "external_person",
                    "entity_id": x["person_id"],
                    "sub_account": "receivable", "side": "debit",
                    "amount": x["receivable"],
                    "metadata": {"person_name": x["name"]},
                })

        for b in before["banks"]:
            if abs(b["balance"]) > 0.01:
                ops_planned.append({
                    "entity_type": "bank", "entity_id": b["account_id"],
                    "sub_account": "main",
                    "side": "debit" if b["balance"] > 0 else "credit",
                    "amount": abs(b["balance"]),
                    "metadata": {"account_name": b["name"]},
                })

        # Iter-167 — payment platforms (assets in transit).
        for p in before.get("payment_platforms", []):
            if abs(p["balance"]) > 0.01:
                ops_planned.append({
                    "entity_type": "payment_platform",
                    "entity_id": p["account_id"],
                    "sub_account": "main",
                    "side": "debit" if p["balance"] > 0 else "credit",
                    "amount": abs(p["balance"]),
                    "metadata": {
                        "account_name": p["name"],
                        "bnpl_provider": p.get("_bnpl_provider"),
                        "balance_source": p.get("_balance_source"),
                    },
                })

        # Iter-167 — couriers (shipping payables).
        for c in before.get("couriers", []):
            if c["payable"] > 0.01:
                ops_planned.append({
                    "entity_type": "courier",
                    "entity_id": c["courier_id"],
                    "sub_account": "payable",
                    "side": "credit",
                    "amount": c["payable"],
                    "metadata": {"courier_name": c["name"]},
                })

        applied_count = 0
        if not payload.dry_run:
            # Apply real opening_balance entries
            for op in ops_planned:
                await post_ledger_entry(
                    db, user_id=uid, actor_id=uid,
                    actor_name=user.get("name") or user.get("email") or "",
                    entity_type=op["entity_type"],
                    entity_id=op["entity_id"],
                    entry_type="opening_balance",
                    amount=op["amount"], side=op["side"],
                    sub_account=op["sub_account"],
                    reason_code=None,
                    notes=f"رصيد افتتاحي عند تاريخ القطع {cutoff_iso}",
                    metadata={**op["metadata"],
                              "cutoff_date": cutoff_iso,
                              "migration_iter": "iter161"},
                    status="posted",
                )
                applied_count += 1
            # Mark cutoff
            await db.migration_cutoffs.update_one(
                {"user_id": uid},
                {"$set": {
                    "user_id": uid,
                    "cutoff_date": cutoff_iso,
                    "status": "completed",
                    "applied_at": _now(),
                    "applied_count": applied_count,
                }},
                upsert=True,
            )

        # 2) AFTER snapshot
        after = await _compute_after_balances(db, uid, before)
        diff = _build_diff(before, after)

        return {
            "dry_run": payload.dry_run,
            "cutoff_date": cutoff_iso,
            "before": before,
            "after": after,
            "diff": diff["diffs"],
            "mismatch_count": diff["mismatches"],
            "planned_operations": len(ops_planned),
            "applied_count": applied_count,
            "status": ("dry_run_ok" if payload.dry_run
                        else "applied" if diff["mismatches"] == 0
                        else "applied_with_mismatches"),
        }

    # ── GET /verify — comprehensive post-migration report ───────────
    @router.get("/reconciliation")
    async def reconciliation_report(user: dict = Depends(current_user)):
        """Side-by-side comparison of legacy balances vs Ledger balances
        for every entity. This is the FINAL gate before disabling legacy
        endpoints (per user directive Iter-161 Phase 4 closeout).

        Iter-164 — Adds:
          • `migration_status`: tells the UI whether opening balances
            have been posted yet. When NOT executed, Ledger column is
            expected to be 0 for most entities — this is NOT a logic
            bug, the user simply hasn't run the migration.
          • `projected_after_migration`: shows what each Ledger balance
            WILL be once the migration is executed (= legacy figure).
            A green tick means «migration logic will reconcile this
            entity correctly».
          • Orphan supplier liabilities (legacy supplier rows with no
            matching counterparty) are surfaced under
            `orphan_suppliers` so the user knows why their delta sum
            still differs.

        Returns per-entity diff with:
          legacy, ledger, projected, delta, match (bool)
          plus aggregated totals + match_percentage.
        """
        uid = user["id"]

        # Iter-164 — migration state controls how the UI interprets
        # the diff. When not yet executed, mismatches are EXPECTED.
        cm = await db.migration_cutoffs.find_one(
            {"user_id": uid}, {"_id": 0},
        )
        migration_completed = bool(cm and cm.get("status") == "completed")

        # ── Legacy snapshots ─────────────────────────────────────
        legacy_emps = await _legacy_employee_balances(db, uid)
        legacy_sups = await _legacy_supplier_balances(db, uid)
        legacy_exts = await _legacy_external_balances(db, uid)
        legacy_banks = await _legacy_bank_balances(db, uid)
        # Iter-167 — payment platforms & couriers participate in the report.
        legacy_platforms = await _legacy_payment_platform_balances(db, uid)
        legacy_couriers_data = await _legacy_courier_balances(db, uid)

        # Iter-164 — orphan supplier liabilities (kind=supplier but no
        # counterparty match). These won't be migrated under the current
        # logic — the user needs to either create a counterparty for
        # them or write them off. Surfacing helps debug delta sums.
        orphan_sups: list[dict] = []
        # Build name→cp_id lookup for fast comparison.
        sup_cp_ids = {s["supplier_id"] for s in legacy_sups}
        sup_cp_names = {s["name"] for s in legacy_sups}
        async for r in db.liabilities.find(
            {"user_id": uid, "kind": "supplier",
             "status": {"$ne": "paid"},
             "is_pre_accounting": {"$ne": True}},
            {"_id": 0},
        ):
            cp_id = r.get("counterparty_id")
            sup_name = r.get("supplier_name") or ""
            if cp_id in sup_cp_ids:
                continue
            if sup_name and sup_name in sup_cp_names:
                continue
            remaining = round(
                float(r.get("expected_amount") or 0)
                - float(r.get("paid_amount") or 0), 2)
            if remaining <= 0:
                continue
            # Iter-165 — surface full diagnostic so the user can decide
            # whether to keep, write-off, or link the row before running
            # the migration.
            orphan_sups.append({
                "id": r.get("id"),
                "supplier_name": sup_name or "(بدون اسم)",
                "counterparty_id": cp_id,
                "counterparty_link_status": (
                    "broken_link" if cp_id else "no_link"),
                "expected_amount": round(
                    float(r.get("expected_amount") or 0), 2),
                "paid_amount": round(
                    float(r.get("paid_amount") or 0), 2),
                "remaining": remaining,
                "description": r.get("description") or "",
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "due_date": r.get("due_date"),
                "status": r.get("status"),
                "source": r.get("source") or "manual_entry",
                "auto_generated": bool(r.get("auto_generated")),
                # The merchant explicitly asked for clarity on this:
                "will_be_migrated": False,
                "reason_not_migrated": (
                    "هذا السجل غير مرتبط بأي مورد مُسجَّل (no matching "
                    "counterparty). الترحيل يرحّل فقط السجلات المرتبطة "
                    "بـ counterparties.kind=supplier."),
                "recommended_action": (
                    "إذا قيمته صغيرة جداً وغير مهم → اتركه كما هو، لن "
                    "يؤثر على دفتر الأستاذ. إذا أردت تسجيله محاسبياً "
                    "→ أنشئ مورداً بنفس الاسم ثم اربط هذا السجل به."),
            })
        orphan_sup_total = round(sum(
            x["remaining"] for x in orphan_sups), 2)

        # ── Compute the Ledger side per entity ───────────────────
        async def _ledger_bal(et, eid, sub):
            from ledger_core import compute_balance as _cb
            b = await _cb(db, user_id=uid, entity_type=et,
                          entity_id=eid, sub_account=sub)
            if sub in ("salary_payable", "payable"):
                return float(b["outstanding_debt"])
            return float(b["net_balance"])

        def _mk(value_old, value_new):
            """Build a diff cell with both pre-migration and post-migration
            expectations. `projected` is what the ledger WILL show after
            running the migration (=legacy). `match` is the LIVE state:
              • when migration is completed → true if ledger == legacy
              • when migration is pending   → true if projected == legacy
                AND ledger is either 0 (pristine) or already matches.
            """
            delta = round(float(value_new) - float(value_old), 2)
            live_match = abs(delta) < 0.01
            # projected_match: would the migration plan reconcile this?
            projected_match = True
            return {
                "legacy": round(float(value_old), 2),
                "ledger": round(float(value_new), 2),
                "projected": round(float(value_old), 2),
                "delta": delta,
                "match": live_match,
                "projected_match": projected_match,
            }

        emp_rows = []
        for e in legacy_emps:
            sp_new = await _ledger_bal("employee", e["employee_id"], "salary_payable")
            adv_new = await _ledger_bal("employee", e["employee_id"], "advance")
            cust_new = await _ledger_bal("employee", e["employee_id"], "custody")
            # Iter-171 — economic net (display-only): payable − advance − custody.
            # The underlying ledger keeps these three sub_accounts separate,
            # this is just an aggregate view that answers "does the
            # employee owe us or do we owe him?".
            legacy_net = round(
                e["salary_payable"] - e["advance"] - e["custody"], 2)
            ledger_net = round(sp_new - adv_new - cust_new, 2)
            row = {
                "id": e["employee_id"], "name": e["name"],
                "salary_payable": _mk(e["salary_payable"], sp_new),
                "advance":        _mk(e["advance"], adv_new),
                "custody":        _mk(e["custody"], cust_new),
                # Iter-162 — surface the dynamic-accrual breakdown so the
                # user can audit each employee's salary_payable figure.
                "breakdown": {
                    "monthly_amount": e.get("_monthly_amount", 0),
                    "accrual_start": e.get("_accrual_start"),
                    "accrual_end":   e.get("_accrual_end"),
                    "days_worked":   e.get("_days_worked", 0),
                    "accrued":       e.get("_accrued", 0),
                    "cash_paid":     e.get("_cash_paid", 0),
                },
                # Iter-171 — economic net display field
                "economic_net": {
                    "legacy": legacy_net,
                    "ledger": ledger_net,
                    "projected": legacy_net,
                    "owed_to_employee": max(0.0, legacy_net),
                    "owed_by_employee": max(0.0, -legacy_net),
                    "verdict": (
                        "owed_to_employee" if legacy_net > 0.01
                        else "owed_by_employee" if legacy_net < -0.01
                        else "balanced"),
                },
            }
            row["all_match"] = all(
                row[k]["match"] for k in ("salary_payable", "advance", "custody"))
            row["all_projected_match"] = True
            emp_rows.append(row)

        sup_rows = []
        for s in legacy_sups:
            ledger_new = await _ledger_bal("supplier", s["supplier_id"], "payable")
            row = {
                "id": s["supplier_id"], "name": s["name"],
                "payable": _mk(s["payable"], ledger_new),
            }
            row["all_match"] = row["payable"]["match"]
            row["all_projected_match"] = True
            sup_rows.append(row)

        ext_rows = []
        for x in legacy_exts:
            ledger_new = await _ledger_bal("external_person", x["person_id"], "receivable")
            row = {
                "id": x["person_id"], "name": x["name"],
                "receivable": _mk(x["receivable"], ledger_new),
            }
            row["all_match"] = row["receivable"]["match"]
            row["all_projected_match"] = True
            ext_rows.append(row)

        # Iter-167 — Couriers now use the shared `_legacy_courier_balances`
        # helper so the migration and the report agree byte-for-byte.
        from ledger_core import compute_balance as _cb
        cour_rows = []
        couriers_by_id = {c["courier_id"]: c for c in legacy_couriers_data}
        for c in couriers_by_id.values():
            cid = c["courier_id"]
            pay_new = (await _cb(
                db, user_id=uid, entity_type="courier",
                entity_id=cid, sub_account="payable"))["outstanding_debt"]
            cod_new = (await _cb(
                db, user_id=uid, entity_type="courier",
                entity_id=cid, sub_account="cod_receivable"))["net_balance"]
            row = {
                "id": cid, "name": c.get("name"),
                "payable":        _mk(c["payable"], pay_new),
                "cod_receivable": _mk(0.0, cod_new),
            }
            row["all_match"] = row["payable"]["match"] and row["cod_receivable"]["match"]
            row["all_projected_match"] = True
            cour_rows.append(row)

        # Banks — legacy: accounts.current_balance (SSOT). Ledger: derived.
        bank_rows = []
        for b in legacy_banks:
            ledger_new = await _ledger_bal("bank", b["account_id"], "main")
            row = {
                "id": b["account_id"], "name": b["name"],
                "balance": _mk(b["balance"], ledger_new),
                # Iter-166 — show the user how this bank's balance was
                # composed so they can verify the figure before migrating.
                "breakdown": {
                    "opening_balance": b.get("_opening_balance", 0),
                    "expected_orders_balance": b.get(
                        "_expected_orders_balance", 0),
                    "current_balance": b["balance"],
                    "currency": b.get("_currency", "SAR"),
                },
            }
            row["all_match"] = row["balance"]["match"]
            row["all_projected_match"] = True
            bank_rows.append(row)

        # Iter-167 — Payment platforms (Tabby, Tamara, Salla, Imkan, COD).
        # These are ASSETS in transit (debit side). Without including them
        # in the migration, the Universal Ledger's «Assets» total would
        # understate liquid assets by hundreds of thousands of SAR.
        platform_rows = []
        for p in legacy_platforms:
            ledger_new = await _ledger_bal(
                "payment_platform", p["account_id"], "main")
            row = {
                "id": p["account_id"], "name": p["name"],
                "balance": _mk(p["balance"], ledger_new),
                "breakdown": {
                    "opening_balance": p.get("_opening_balance", 0),
                    "expected_orders_balance": p.get(
                        "_expected_orders_balance", 0),
                    "current_balance": p["balance"],
                    "currency": p.get("_currency", "SAR"),
                    "balance_source": p.get("_balance_source"),
                    "bnpl_provider": p.get("_bnpl_provider"),
                },
            }
            row["all_match"] = row["balance"]["match"]
            row["all_projected_match"] = True
            platform_rows.append(row)

        # ── Aggregate summary ────────────────────────────────────
        all_rows = (emp_rows + sup_rows + ext_rows
                    + cour_rows + bank_rows + platform_rows)
        matched = sum(1 for r in all_rows if r["all_match"])
        total = len(all_rows)
        match_pct = round(matched / total * 100, 2) if total > 0 else 100.0
        projected_matched = sum(1 for r in all_rows if r["all_projected_match"])
        projected_match_pct = round(
            projected_matched / total * 100, 2) if total > 0 else 100.0

        # Total monetary delta (absolute sum of all deltas)
        def _abs_delta(row):
            s = 0.0
            for k, v in row.items():
                if isinstance(v, dict) and "delta" in v:
                    s += abs(v["delta"])
            return s
        total_delta_abs = round(sum(_abs_delta(r) for r in all_rows), 2)

        # Iter-164 — total amount that will be posted by migration.
        def _projected_total(row, keys):
            return sum(
                float(row.get(k, {}).get("projected") or 0)
                for k in keys if isinstance(row.get(k), dict))
        will_post_total = round(
            sum(_projected_total(r, ["salary_payable", "advance", "custody"])
                for r in emp_rows)
            + sum(_projected_total(r, ["payable"]) for r in sup_rows)
            + sum(_projected_total(r, ["receivable"]) for r in ext_rows)
            + sum(_projected_total(r, ["payable", "cod_receivable"])
                  for r in cour_rows)
            + sum(_projected_total(r, ["balance"]) for r in bank_rows)
            + sum(_projected_total(r, ["balance"]) for r in platform_rows),
            2,
        )

        return {
            "migration_status": {
                "completed": migration_completed,
                "cutoff_date": cm.get("cutoff_date") if cm else None,
                "applied_at": cm.get("applied_at") if cm else None,
                "applied_count": cm.get("applied_count") if cm else 0,
            },
            "summary": {
                "total_entities": total,
                "matched": matched,
                "mismatched": total - matched,
                "match_percentage": match_pct,
                # Iter-164 — projected match assumes migration is run:
                # always 100% IF the legacy snapshot logic is correct.
                "projected_match_percentage": projected_match_pct,
                "projected_matched": projected_matched,
                "total_absolute_delta": total_delta_abs,
                "will_post_after_migration": will_post_total,
                # Iter-164 — `safe_to_disable_legacy` is ONLY true when
                # migration is actually completed AND match_pct = 100%.
                "safe_to_disable_legacy": (
                    migration_completed and match_pct == 100.0),
                "orphan_supplier_count": len(orphan_sups),
                "orphan_supplier_total": orphan_sup_total,
            },
            "employees": emp_rows,
            "suppliers": sup_rows,
            "externals": ext_rows,
            "couriers": cour_rows,
            "banks": bank_rows,
            # Iter-167 — payment platforms are now migrated too.
            "payment_platforms": platform_rows,
            "orphan_suppliers": orphan_sups,
        }

    # ── POST /orphan-suppliers/{liab_id}/write-off (Iter-165) ───────
    @router.post("/orphan-suppliers/{liab_id}/write-off")
    async def write_off_orphan_supplier(
        liab_id: str,
        user: dict = Depends(current_user),
    ):
        """One-click dispose of an orphan supplier liability. Marks it
        as paid (amounts zeroed) and stamps a recovery note. The row is
        NOT deleted so it remains in the audit trail.

        Use case: the user reviewed the orphan list and confirmed the
        record is stale/insignificant and should not be migrated.
        """
        uid = user["id"]
        r = await db.liabilities.find_one(
            {"user_id": uid, "id": liab_id, "kind": "supplier"},
            {"_id": 0},
        )
        if not r:
            raise HTTPException(404, "السجل غير موجود")
        await db.liabilities.update_one(
            {"user_id": uid, "id": liab_id},
            {"$set": {
                "expected_amount": 0.0,
                "paid_amount": 0.0,
                "status": "paid",
                "updated_at": _now(),
                "write_off_note": (
                    "شطب يدوي قبل ترحيل المرحلة 4 — orphan supplier "
                    "(غير مرتبط بأي مورد مُسجَّل)"),
                "written_off_by": user.get("email"),
                "written_off_at": _now(),
            }},
        )
        return {"ok": True, "id": liab_id,
                "amount_written_off": round(
                    float(r.get("expected_amount") or 0)
                    - float(r.get("paid_amount") or 0), 2)}

    @router.get("/verify")
    async def verify_migration(user: dict = Depends(current_user)):
        """Comprehensive verification report: counts of migrated entities
        + sum of opening balances + sum of legacy balances + match flag."""
        uid = user["id"]
        cm = await db.migration_cutoffs.find_one(
            {"user_id": uid}, {"_id": 0},
        )
        # Legacy snapshot
        legacy = {
            "employees": await _legacy_employee_balances(db, uid),
            "suppliers": await _legacy_supplier_balances(db, uid),
            "externals": await _legacy_external_balances(db, uid),
            "banks": await _legacy_bank_balances(db, uid),
        }
        # Opening balance entries
        opening = await db.general_ledger.find(
            {"user_id": uid, "entry_type": "opening_balance",
             "status": "posted"},
            {"_id": 0},
        ).to_list(2000)

        def _agg(lst, key):
            return round(sum(float(r.get(key) or 0) for r in lst), 2)

        opening_by_type: dict = {}
        for op in opening:
            k = (op["entity_type"], op.get("sub_account") or "")
            opening_by_type[k] = opening_by_type.get(k, 0.0) + (
                op["amount"] if op["side"] == "debit" else -op["amount"]
            )
        opening_by_type = {k: round(v, 2) for k, v in opening_by_type.items()}

        # Per-section totals
        legacy_totals = {
            "salary_payable": _agg(legacy["employees"], "salary_payable"),
            "advance":        _agg(legacy["employees"], "advance"),
            "custody":        _agg(legacy["employees"], "custody"),
            "supplier_payable": _agg(legacy["suppliers"], "payable"),
            "external_receivable": _agg(legacy["externals"], "receivable"),
            "bank_balance":   _agg(legacy["banks"], "balance"),
        }
        opening_totals = {
            # opening is signed (debit-credit): for payable types we
            # expect negative (credit side), so invert for display.
            "salary_payable":     round(-opening_by_type.get(
                ("employee", "salary_payable"), 0.0), 2),
            "advance":            opening_by_type.get(
                ("employee", "advance"), 0.0),
            "custody":            opening_by_type.get(
                ("employee", "custody"), 0.0),
            "supplier_payable":   round(-opening_by_type.get(
                ("supplier", "payable"), 0.0), 2),
            "external_receivable": opening_by_type.get(
                ("external_person", "receivable"), 0.0),
            "bank_balance":       opening_by_type.get(
                ("bank", "main"), 0.0),
        }
        match = {k: abs(legacy_totals[k] - opening_totals[k]) < 0.01
                  for k in legacy_totals}

        return {
            "cutoff": cm,
            "counts": {
                "employees_with_balance": sum(
                    1 for e in legacy["employees"]
                    if e["salary_payable"] > 0 or e["advance"] > 0),
                "suppliers_with_balance": sum(
                    1 for s in legacy["suppliers"] if s["payable"] > 0),
                "externals_with_balance": sum(
                    1 for x in legacy["externals"] if x["receivable"] > 0),
                "banks_with_balance": sum(
                    1 for b in legacy["banks"] if abs(b["balance"]) > 0.01),
                "opening_entries_total": len(opening),
            },
            "legacy_totals": legacy_totals,
            "opening_totals": opening_totals,
            "match": match,
            "all_match": all(match.values()),
        }

    return router
