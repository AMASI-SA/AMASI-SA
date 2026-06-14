"""Iter-197 — Reconciliation Forensic Audit (Read-Only).

Diagnostic endpoint that breaks down every mismatch in
`GET /api/migration/reconciliation` into:

    • the exact legacy formula value
    • the exact ledger composition (by entry_type × sub_account × side)
    • the source classification of the diff:
        - no_ledger_entries        → migration was never run for this
                                      entity (ledger = 0, legacy > 0)
        - no_legacy_data           → legacy = 0 but ledger has rows
                                      (rare, indicates manual posts)
        - opening_only             → ledger has only `opening_balance`
                                      rows; the diff is in the opening
                                      figure itself
        - migration_iter161_only   → only rows tagged with
                                      `metadata.migration_iter = 'iter161'`
        - post_cutoff_ops          → entries dated after the cutoff
        - mixed                    → both migration + post-cutoff
        - legacy_formula_drift     → ledger sums match legacy method
                                      but the legacy snapshot returned
                                      a different number (= bug in
                                      legacy formula or a recent
                                      legacy mutation)

Strictly READ-ONLY. No mutations.
"""
from __future__ import annotations

from collections import defaultdict
from fastapi import APIRouter, Depends


def _classify_diff(ledger_rows: list, delta: float) -> str:
    """Heuristic classification of a non-zero delta.

    Args:
        ledger_rows: every general_ledger row for the (entity, sub).
        delta: ledger − legacy (already rounded).
    """
    if abs(delta) < 0.01:
        return "balanced"
    if not ledger_rows:
        return "no_ledger_entries"
    types = {(r.get("entry_type") or "") for r in ledger_rows}
    has_iter161 = any(
        (r.get("metadata") or {}).get("migration_iter") == "iter161"
        for r in ledger_rows
    )
    only_opening = types == {"opening_balance"}
    has_opening = "opening_balance" in types
    has_post_cutoff = any(
        (r.get("entry_type") or "") != "opening_balance"
        for r in ledger_rows
    )
    if only_opening:
        if has_iter161:
            return "migration_iter161_only"
        return "opening_only"
    if has_opening and has_post_cutoff:
        return "mixed"
    if has_post_cutoff and not has_opening:
        # If ledger has post-cutoff data only but no opening, this is
        # either pure post-cutoff drift (operations) or migration was
        # never run for the entity.
        return "post_cutoff_ops"
    return "legacy_formula_drift"


def make_reconciliation_forensic_router(db, current_user):
    """Iter-197 — Deep forensic dive on the reconciliation deltas."""
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/reconciliation-forensic")
    async def reconciliation_forensic(
        user: dict = Depends(current_user),
    ):
        from migration_routes import (
            _legacy_employee_balances,
            _legacy_bank_balances,
            _legacy_payment_platform_balances,
            _legacy_supplier_balances,
            _legacy_external_balances,
            _legacy_courier_balances,
        )
        from ledger_core import compute_balance

        uid = user["id"]

        # Migration cut-off marker (sets the post/pre boundary)
        cm = await db.migration_cutoffs.find_one(
            {"user_id": uid}, {"_id": 0},
        )
        cutoff_date = cm.get("cutoff_date") if cm else None
        migration_completed = bool(
            cm and cm.get("status") == "completed")

        # ── helper: load all ledger rows for entity+sub ─────────
        async def _ledger_rows(et: str, eid: str, sub: str):
            return await db.general_ledger.find(
                {"user_id": uid,
                 "entity_type": et,
                 "entity_id": eid,
                 "sub_account": sub,
                 "status": "posted"},
                {"_id": 0, "id": 1, "entry_type": 1, "side": 1,
                 "amount": 1, "metadata": 1, "notes": 1,
                 "txn_group_id": 1, "created_at": 1, "posted_at": 1},
            ).to_list(2000)

        def _aggregate_rows(rows: list) -> dict:
            """Roll up rows by (entry_type, side) and a tag breakdown."""
            by_type: dict = defaultdict(
                lambda: {"debit": 0.0, "credit": 0.0, "count": 0})
            tagged_iter161 = {"debit": 0.0, "credit": 0.0, "count": 0}
            post_cutoff = {"debit": 0.0, "credit": 0.0, "count": 0}
            for r in rows:
                et = r.get("entry_type") or "_unknown"
                side = r.get("side") or "debit"
                amt = float(r.get("amount") or 0)
                by_type[et][side] += amt
                by_type[et]["count"] += 1
                md = r.get("metadata") or {}
                if md.get("migration_iter") == "iter161":
                    tagged_iter161[side] += amt
                    tagged_iter161["count"] += 1
                else:
                    # Treat anything not tagged as post-cutoff /
                    # operational. The cutoff_date is informational.
                    post_cutoff[side] += amt
                    post_cutoff["count"] += 1
            net = round(
                sum(v["debit"] for v in by_type.values())
                - sum(v["credit"] for v in by_type.values()),
                2,
            )
            return {
                "row_count": len(rows),
                "by_entry_type": [
                    {"entry_type": k,
                     "debit": round(v["debit"], 2),
                     "credit": round(v["credit"], 2),
                     "net": round(v["debit"] - v["credit"], 2),
                     "count": v["count"]}
                    for k, v in by_type.items()
                ],
                "iter161_tagged": {
                    "debit": round(tagged_iter161["debit"], 2),
                    "credit": round(tagged_iter161["credit"], 2),
                    "net": round(
                        tagged_iter161["debit"]
                        - tagged_iter161["credit"], 2),
                    "count": tagged_iter161["count"],
                },
                "post_cutoff_or_untagged": {
                    "debit": round(post_cutoff["debit"], 2),
                    "credit": round(post_cutoff["credit"], 2),
                    "net": round(
                        post_cutoff["debit"]
                        - post_cutoff["credit"], 2),
                    "count": post_cutoff["count"],
                },
                "ledger_net": net,
            }

        async def _ledger_balance(et: str, eid: str, sub: str) -> float:
            b = await compute_balance(
                db, user_id=uid, entity_type=et,
                entity_id=eid, sub_account=sub,
            )
            if sub in ("salary_payable", "payable"):
                return float(b["outstanding_debt"])
            return float(b["net_balance"])

        # ── EMPLOYEES (3 sub-accounts each) ─────────────────────
        legacy_emps = await _legacy_employee_balances(db, uid)
        emp_details = []
        emp_total_abs_delta = 0.0
        for e in legacy_emps:
            for sub_key in ("salary_payable", "advance", "custody"):
                legacy_val = round(float(e.get(sub_key) or 0), 2)
                ledger_val = round(
                    await _ledger_balance(
                        "employee", e["employee_id"], sub_key,
                    ),
                    2,
                )
                delta = round(ledger_val - legacy_val, 2)
                if abs(delta) < 0.01 and legacy_val == 0 \
                        and ledger_val == 0:
                    continue  # nothing to report
                rows = await _ledger_rows(
                    "employee", e["employee_id"], sub_key,
                )
                agg = _aggregate_rows(rows)
                cls = _classify_diff(rows, delta)
                emp_details.append({
                    "employee_id": e["employee_id"],
                    "employee_name": e["name"],
                    "sub_account": sub_key,
                    "legacy": legacy_val,
                    "ledger": ledger_val,
                    "delta": delta,
                    "classification": cls,
                    "ledger_breakdown": agg,
                    "ledger_sample_first_5": rows[:5],
                })
                emp_total_abs_delta += abs(delta)

        # ── BANKS ───────────────────────────────────────────────
        legacy_banks = await _legacy_bank_balances(db, uid)
        bank_details = []
        bank_total_abs_delta = 0.0
        for b in legacy_banks:
            legacy_val = round(float(b.get("balance") or 0), 2)
            ledger_val = round(
                await _ledger_balance("bank", b["account_id"], "main"),
                2,
            )
            delta = round(ledger_val - legacy_val, 2)
            rows = await _ledger_rows("bank", b["account_id"], "main")
            agg = _aggregate_rows(rows)
            cls = _classify_diff(rows, delta)
            bank_details.append({
                "account_id": b["account_id"],
                "name": b["name"],
                "legacy": legacy_val,
                "ledger": ledger_val,
                "delta": delta,
                "classification": cls,
                "opening_balance_field": round(
                    float(b.get("_opening_balance") or 0), 2),
                "expected_orders_balance": round(
                    float(b.get("_expected_orders_balance") or 0), 2),
                "ledger_breakdown": agg,
                "ledger_sample_first_5": rows[:5],
            })
            bank_total_abs_delta += abs(delta)

        # ── PAYMENT PLATFORMS ───────────────────────────────────
        legacy_plats = await _legacy_payment_platform_balances(db, uid)
        plat_details = []
        plat_total_abs_delta = 0.0
        for p in legacy_plats:
            legacy_val = round(float(p.get("balance") or 0), 2)
            ledger_val = round(
                await _ledger_balance(
                    "payment_platform", p["account_id"], "main",
                ),
                2,
            )
            delta = round(ledger_val - legacy_val, 2)
            rows = await _ledger_rows(
                "payment_platform", p["account_id"], "main",
            )
            agg = _aggregate_rows(rows)
            cls = _classify_diff(rows, delta)
            plat_details.append({
                "account_id": p["account_id"],
                "name": p["name"],
                "bnpl_provider": p.get("_bnpl_provider"),
                "balance_source": p.get("_balance_source"),
                "legacy": legacy_val,
                "ledger": ledger_val,
                "delta": delta,
                "classification": cls,
                "opening_balance_field": round(
                    float(p.get("_opening_balance") or 0), 2),
                "expected_orders_balance": round(
                    float(p.get("_expected_orders_balance") or 0), 2),
                "ledger_breakdown": agg,
                "ledger_sample_first_5": rows[:5],
            })
            plat_total_abs_delta += abs(delta)

        # ── SUPPLIERS / EXTERNALS / COURIERS (summary only) ─────
        legacy_sups = await _legacy_supplier_balances(db, uid)
        sup_total_abs = 0.0
        sup_mismatches = []
        for s in legacy_sups:
            lv = round(float(s.get("payable") or 0), 2)
            lg = round(
                await _ledger_balance(
                    "supplier", s["supplier_id"], "payable",
                ),
                2,
            )
            d = round(lg - lv, 2)
            sup_total_abs += abs(d)
            if abs(d) >= 0.01:
                rows = await _ledger_rows(
                    "supplier", s["supplier_id"], "payable",
                )
                sup_mismatches.append({
                    "supplier_id": s["supplier_id"],
                    "name": s["name"],
                    "legacy": lv, "ledger": lg, "delta": d,
                    "classification": _classify_diff(rows, d),
                })

        legacy_exts = await _legacy_external_balances(db, uid)
        ext_total_abs = 0.0
        ext_mismatches = []
        for x in legacy_exts:
            lv = round(float(x.get("receivable") or 0), 2)
            lg = round(
                await _ledger_balance(
                    "external_person", x["person_id"], "receivable",
                ),
                2,
            )
            d = round(lg - lv, 2)
            ext_total_abs += abs(d)
            if abs(d) >= 0.01:
                rows = await _ledger_rows(
                    "external_person", x["person_id"], "receivable",
                )
                ext_mismatches.append({
                    "person_id": x["person_id"],
                    "name": x["name"],
                    "legacy": lv, "ledger": lg, "delta": d,
                    "classification": _classify_diff(rows, d),
                })

        legacy_cours = await _legacy_courier_balances(db, uid)
        cour_total_abs = 0.0
        cour_mismatches = []
        for c in legacy_cours:
            lv_pay = round(float(c.get("payable") or 0), 2)
            lg_pay = round(
                await _ledger_balance(
                    "courier", c["courier_id"], "payable",
                ),
                2,
            )
            d_pay = round(lg_pay - lv_pay, 2)
            lg_cod = round(
                await _ledger_balance(
                    "courier", c["courier_id"], "cod_receivable",
                ),
                2,
            )
            d_cod = round(lg_cod - 0.0, 2)
            cour_total_abs += abs(d_pay) + abs(d_cod)
            if abs(d_pay) >= 0.01 or abs(d_cod) >= 0.01:
                cour_mismatches.append({
                    "courier_id": c["courier_id"],
                    "name": c.get("name"),
                    "payable_legacy": lv_pay,
                    "payable_ledger": lg_pay,
                    "payable_delta": d_pay,
                    "cod_receivable_ledger": lg_cod,
                    "cod_receivable_delta": d_cod,
                })

        # ── Aggregate verdict ───────────────────────────────────
        grand_total_abs_delta = round(
            emp_total_abs_delta
            + bank_total_abs_delta
            + plat_total_abs_delta
            + sup_total_abs
            + ext_total_abs
            + cour_total_abs,
            2,
        )

        # Classification tallies — which source dominates?
        verdict_tally: dict = defaultdict(
            lambda: {"count": 0, "abs_delta": 0.0})
        for row in emp_details + bank_details + plat_details:
            verdict_tally[row["classification"]]["count"] += 1
            verdict_tally[row["classification"]]["abs_delta"] += \
                abs(row["delta"])
        verdict_breakdown = [
            {"classification": k,
             "count": v["count"],
             "abs_delta": round(v["abs_delta"], 2)}
            for k, v in sorted(
                verdict_tally.items(),
                key=lambda x: -x[1]["abs_delta"],
            )
        ]

        return {
            "report_type": "reconciliation_forensic_v1",
            "read_only": True,
            "user_id": uid,
            "migration_status": {
                "completed": migration_completed,
                "cutoff_date": cutoff_date,
                "applied_at": cm.get("applied_at") if cm else None,
            },
            "grand_total_abs_delta": grand_total_abs_delta,
            "by_section_abs_delta": {
                "employees": round(emp_total_abs_delta, 2),
                "banks": round(bank_total_abs_delta, 2),
                "payment_platforms": round(plat_total_abs_delta, 2),
                "suppliers": round(sup_total_abs, 2),
                "externals": round(ext_total_abs, 2),
                "couriers": round(cour_total_abs, 2),
            },
            "verdict_breakdown": verdict_breakdown,
            "employees": {
                "rows_count": len(emp_details),
                "rows": emp_details,
            },
            "banks": {
                "rows_count": len(bank_details),
                "rows": bank_details,
            },
            "payment_platforms": {
                "rows_count": len(plat_details),
                "rows": plat_details,
            },
            "suppliers": {
                "mismatch_count": len(sup_mismatches),
                "mismatches": sup_mismatches[:50],
            },
            "externals": {
                "mismatch_count": len(ext_mismatches),
                "mismatches": ext_mismatches[:50],
            },
            "couriers": {
                "mismatch_count": len(cour_mismatches),
                "mismatches": cour_mismatches[:50],
            },
            "guidance": (
                "هذا التقرير قراءة فقط — لا يُجرى أي تعديل. "
                "verdict_breakdown يكشف أي تصنيف يولّد الفروقات "
                "الأكبر. classification لكل صف يربط الفرق بمصدره "
                "(opening_only / migration_iter161_only / "
                "post_cutoff_ops / mixed / legacy_formula_drift / "
                "no_ledger_entries / no_legacy_data)."
            ),
        }

    return router
