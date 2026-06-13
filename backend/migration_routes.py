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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(v) -> float:
    return round(float(v or 0), 2)


# ── Legacy balance snapshot ────────────────────────────────────────
async def _legacy_employee_balances(db, user_id: str) -> list[dict]:
    """Per-employee: salary_payable, advance, custody from legacy data.
    Uses liabilities (kind=salary, salary_advance) and operating_salaries."""
    emps = await db.operating_salaries.find(
        {"user_id": user_id}, {"_id": 0},
    ).to_list(500)
    out: list[dict] = []
    for emp in emps:
        # Salary payable = open salary liabilities, expected-paid
        sal_agg = await db.liabilities.aggregate([
            {"$match": {"user_id": user_id, "kind": "salary",
                          "employee_salary_id": emp["id"]}},
            {"$group": {"_id": None,
                          "expected": {"$sum": "$expected_amount"},
                          "paid": {"$sum": "$paid_amount"},
                          "adv_deducted": {"$sum": {"$ifNull": [
                              "$advance_deducted", 0]}}}},
        ]).to_list(1)
        payable = 0.0
        if sal_agg:
            payable = _round(
                (sal_agg[0]["expected"] or 0)
                - (sal_agg[0]["paid"] or 0)
                - (sal_agg[0]["adv_deducted"] or 0)
            )
            if payable < 0:
                payable = 0.0

        # Open advances = paid_amount of open advance rows
        adv_agg = await db.liabilities.aggregate([
            {"$match": {"user_id": user_id, "kind": "salary_advance",
                          "employee_salary_id": emp["id"],
                          "advance_status": "open"}},
            {"$group": {"_id": None,
                          "paid": {"$sum": "$paid_amount"}}}
        ]).to_list(1)
        advance = _round(adv_agg[0]["paid"]) if adv_agg else 0.0

        # Custody = there's no legacy concept — start at 0
        custody = 0.0
        out.append({
            "employee_id": emp["id"], "name": emp.get("name"),
            "salary_payable": payable,
            "advance": advance,
            "custody": custody,
        })
    return out


async def _legacy_supplier_balances(db, user_id: str) -> list[dict]:
    cps = await db.counterparties.find(
        {"user_id": user_id, "kind": "supplier"}, {"_id": 0},
    ).to_list(500)
    out: list[dict] = []
    for cp in cps:
        agg = await db.liabilities.aggregate([
            {"$match": {"user_id": user_id, "kind": "supplier",
                          "counterparty_id": cp["id"]}},
            {"$group": {"_id": None,
                          "expected": {"$sum": "$expected_amount"},
                          "paid": {"$sum": "$paid_amount"}}},
        ]).to_list(1)
        owed = 0.0
        if agg:
            owed = _round((agg[0]["expected"] or 0) - (agg[0]["paid"] or 0))
            if owed < 0:
                owed = 0.0
        out.append({
            "supplier_id": cp["id"], "name": cp.get("name"),
            "payable": owed,
        })
    return out


async def _legacy_external_balances(db, user_id: str) -> list[dict]:
    cps = await db.counterparties.find(
        {"user_id": user_id, "kind": {"$nin": [
            "ad_account", "supplier", "courier"]}},
        {"_id": 0},
    ).to_list(500)
    out: list[dict] = []
    for cp in cps:
        # Receivables (kind=receivable on liabilities, tied via
        # counterparty_id or counterparty_name)
        agg = await db.liabilities.aggregate([
            {"$match": {"user_id": user_id, "kind": "receivable",
                          "$or": [
                              {"counterparty_id": cp["id"]},
                              {"counterparty_name": cp.get("name")},
                          ]}},
            {"$group": {"_id": None,
                          "expected": {"$sum": "$expected_amount"},
                          "paid": {"$sum": "$paid_amount"}}},
        ]).to_list(1)
        owed = 0.0
        if agg:
            owed = _round((agg[0]["expected"] or 0) - (agg[0]["paid"] or 0))
        out.append({
            "person_id": cp["id"], "name": cp.get("name"),
            "receivable": owed,
        })
    return out


async def _legacy_bank_balances(db, user_id: str) -> list[dict]:
    accs = await db.accounts.find(
        {"user_id": user_id, "account_type": "bank"}, {"_id": 0},
    ).to_list(500)
    return [{"account_id": a["id"], "name": a.get("name"),
              "balance": _round(a.get("balance"))}
            for a in accs]


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

        Returns per-entity diff with:
          legacy_balance, ledger_balance, delta, match (bool)
          plus aggregated totals + match_percentage.
        """
        uid = user["id"]

        # ── Legacy snapshots ─────────────────────────────────────
        legacy_emps = await _legacy_employee_balances(db, uid)
        legacy_sups = await _legacy_supplier_balances(db, uid)
        legacy_exts = await _legacy_external_balances(db, uid)
        legacy_banks = await _legacy_bank_balances(db, uid)

        # ── Compute the Ledger side per entity ───────────────────
        async def _ledger_bal(et, eid, sub):
            from ledger_core import compute_balance as _cb
            b = await _cb(db, user_id=uid, entity_type=et,
                          entity_id=eid, sub_account=sub)
            if sub in ("salary_payable", "payable"):
                return float(b["outstanding_debt"])
            return float(b["net_balance"])

        def _mk(value_old, value_new):
            delta = round(float(value_new) - float(value_old), 2)
            match = abs(delta) < 0.01
            return {
                "legacy": round(float(value_old), 2),
                "ledger": round(float(value_new), 2),
                "delta": delta,
                "match": match,
            }

        emp_rows = []
        for e in legacy_emps:
            sp_new = await _ledger_bal("employee", e["employee_id"], "salary_payable")
            adv_new = await _ledger_bal("employee", e["employee_id"], "advance")
            cust_new = await _ledger_bal("employee", e["employee_id"], "custody")
            row = {
                "id": e["employee_id"], "name": e["name"],
                "salary_payable": _mk(e["salary_payable"], sp_new),
                "advance":        _mk(e["advance"], adv_new),
                "custody":        _mk(e["custody"], cust_new),
            }
            row["all_match"] = all(
                row[k]["match"] for k in ("salary_payable", "advance", "custody"))
            emp_rows.append(row)

        sup_rows = []
        for s in legacy_sups:
            ledger_new = await _ledger_bal("supplier", s["supplier_id"], "payable")
            row = {
                "id": s["supplier_id"], "name": s["name"],
                "payable": _mk(s["payable"], ledger_new),
            }
            row["all_match"] = row["payable"]["match"]
            sup_rows.append(row)

        ext_rows = []
        for x in legacy_exts:
            ledger_new = await _ledger_bal("external_person", x["person_id"], "receivable")
            row = {
                "id": x["person_id"], "name": x["name"],
                "receivable": _mk(x["receivable"], ledger_new),
            }
            row["all_match"] = row["receivable"]["match"]
            ext_rows.append(row)

        # Couriers — legacy uses counterparties with kind=courier (paid via
        # liabilities kind=shipping or similar). For now we read directly
        # from existing courier counterparties + their open liabilities.
        from ledger_core import compute_balance as _cb
        couriers = await db.counterparties.find(
            {"user_id": uid, "kind": "courier"}, {"_id": 0},
        ).to_list(500)
        cour_rows = []
        for c in couriers:
            # Legacy: open liabilities tied to this courier id
            agg = await db.liabilities.aggregate([
                {"$match": {"user_id": uid,
                              "kind": {"$in": ["shipping", "courier"]},
                              "counterparty_id": c["id"]}},
                {"$group": {"_id": None,
                              "owed": {"$sum": {"$subtract": [
                                  "$expected_amount", "$paid_amount"]}}}}
            ]).to_list(1)
            legacy_owed = float(agg[0]["owed"]) if agg else 0.0
            if legacy_owed < 0:
                legacy_owed = 0.0
            pay_new = (await _cb(
                db, user_id=uid, entity_type="courier",
                entity_id=c["id"], sub_account="payable"))["outstanding_debt"]
            cod_new = (await _cb(
                db, user_id=uid, entity_type="courier",
                entity_id=c["id"], sub_account="cod_receivable"))["net_balance"]
            # Legacy doesn't track COD; mark legacy_cod = 0
            row = {
                "id": c["id"], "name": c.get("name"),
                "payable":        _mk(legacy_owed, pay_new),
                "cod_receivable": _mk(0.0, cod_new),
            }
            row["all_match"] = row["payable"]["match"] and row["cod_receivable"]["match"]
            cour_rows.append(row)

        # Banks — legacy: accounts.balance (stored). Ledger: derived.
        bank_rows = []
        for b in legacy_banks:
            ledger_new = await _ledger_bal("bank", b["account_id"], "main")
            row = {
                "id": b["account_id"], "name": b["name"],
                "balance": _mk(b["balance"], ledger_new),
            }
            row["all_match"] = row["balance"]["match"]
            bank_rows.append(row)

        # ── Aggregate summary ────────────────────────────────────
        all_rows = emp_rows + sup_rows + ext_rows + cour_rows + bank_rows
        matched = sum(1 for r in all_rows if r["all_match"])
        total = len(all_rows)
        match_pct = round(matched / total * 100, 2) if total > 0 else 100.0

        # Total monetary delta (absolute sum of all deltas)
        def _abs_delta(row):
            s = 0.0
            for k, v in row.items():
                if isinstance(v, dict) and "delta" in v:
                    s += abs(v["delta"])
            return s
        total_delta_abs = round(sum(_abs_delta(r) for r in all_rows), 2)

        return {
            "summary": {
                "total_entities": total,
                "matched": matched,
                "mismatched": total - matched,
                "match_percentage": match_pct,
                "total_absolute_delta": total_delta_abs,
                "safe_to_disable_legacy": match_pct == 100.0,
            },
            "employees": emp_rows,
            "suppliers": sup_rows,
            "externals": ext_rows,
            "couriers": cour_rows,
            "banks": bank_rows,
        }

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
