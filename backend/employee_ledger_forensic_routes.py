"""Iter-250b · P1.5.m — Employee Ledger Forensic (READ-ONLY).

Surfaces ALL `general_ledger` rows for one employee — including the
ones currently HIDDEN by the SSOT filters (`entry_type=reversal` and
`metadata.legacy_orphan=True`). Used to diagnose why a previously
visible balance (e.g. an advance) is no longer shown in the
employees table or in `/new-transaction`.

Endpoint::

    GET /api/diagnostics/employee-ledger-forensic
        ?employee_id=<uuid>            REQUIRED
        &include_account_transactions=true   default false

STRICT READ-ONLY · NO writes · NO recompute.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_employee_ledger_forensic_router(db, current_user):
    router = APIRouter(tags=["diagnostics", "employee-ledger-forensic"])

    @router.get("/diagnostics/employee-ledger-forensic")
    async def employee_ledger_forensic(
        employee_id: str = Query(...),
        include_account_transactions: bool = Query(False),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # Iter-250b · P1.5.m.fix — guard the whole body so any failure
        # returns a structured JSON instead of a 500. The merchant
        # cannot debug a blank "Internal Server Error".
        received_params = {
            "employee_id": employee_id,
            "include_account_transactions": include_account_transactions,
        }
        try:
            emp = await db.operating_salaries.find_one(
                {"user_id": uid, "id": employee_id}, {"_id": 0},
            )
            if not emp:
                # Fallback — sometimes the merchant passes a name or
                # a partial UUID. Try matching by name OR by id prefix.
                emp = await db.operating_salaries.find_one(
                    {"user_id": uid,
                     "$or": [
                         {"name": employee_id},
                         {"id": {"$regex":
                                  f"^{employee_id}", "$options": "i"}},
                     ]},
                    {"_id": 0},
                )
                if not emp:
                    return {
                        "ok": False,
                        "error": "employee_not_found",
                        "details": (
                            "No employee matches the given id, name, "
                            "or id prefix."),
                        "received_params": received_params,
                    }
            employee_id = emp["id"]  # canonicalise
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": "employee_lookup_failed",
                "details": repr(e),
                "received_params": received_params,
            }

        # ── 1. Pull EVERY ledger row, no filtering ─────────────────
        all_rows: List[Dict[str, Any]] = []
        try:
            async for r in db.general_ledger.find(
                {"user_id": uid,
                 "entity_type": "employee",
                 "entity_id": employee_id},
                {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
                 "side": 1, "amount": 1, "notes": 1, "posted_at": 1,
                 "created_at": 1, "txn_group_id": 1, "metadata": 1,
                 "status": 1, "reversed_by_entry_id": 1,
                 "reversal_of_entry_id": 1},
            ).sort("posted_at", 1):
                md = r.get("metadata") or {}
                all_rows.append({
                    "id": r.get("id"),
                    "entry_type": r.get("entry_type"),
                    "sub_account": r.get("sub_account"),
                    "side": r.get("side"),
                    "amount": _r(r.get("amount")),
                    "status": r.get("status"),
                    "notes": (r.get("notes") or "")[:120],
                    "posted_at": r.get("posted_at"),
                    "created_at": r.get("created_at"),
                    "txn_group_id": r.get("txn_group_id"),
                    "reversed_by_entry_id":
                        r.get("reversed_by_entry_id"),
                    "reversal_of_entry_id":
                        r.get("reversal_of_entry_id"),
                    "metadata_source": md.get("source"),
                    "metadata_legacy_orphan":
                        bool(md.get("legacy_orphan")),
                    "metadata_keys": list(md.keys()),
                    "ssot_filter_status": (
                        "EXCLUDED_reversal"
                        if (r.get("entry_type") == "reversal")
                        else (
                            "EXCLUDED_legacy_orphan"
                            if md.get("legacy_orphan")
                            else (
                                "EXCLUDED_not_posted"
                                if (r.get("status") != "posted")
                                else "INCLUDED"
                            )
                        )
                    ),
                })
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": "ledger_scan_failed",
                "details": repr(e),
                "received_params": received_params,
                "employee_id": employee_id,
            }

        # ── 2. Aggregate three views ──────────────────────────────
        def _net_by_sub(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            agg: Dict[str, Dict[str, float]] = defaultdict(
                lambda: {"debit": 0.0, "credit": 0.0, "count": 0})
            for r in rows:
                sub = r.get("sub_account") or "_"
                agg[sub][r["side"]] = agg[sub].get(r["side"], 0.0) + r["amount"]
                agg[sub]["count"] += 1
            out = {}
            for sub, v in agg.items():
                net = v["debit"] - v["credit"]
                out[sub] = {
                    "debit": _r(v["debit"]),
                    "credit": _r(v["credit"]),
                    "net": _r(net),
                    "outstanding_debt": _r(-net) if net < 0 else 0.0,
                    "row_count": v["count"],
                }
            return out

        all_included = [r for r in all_rows
                        if r["ssot_filter_status"] == "INCLUDED"]
        excluded_reversal = [r for r in all_rows
                             if r["ssot_filter_status"] == "EXCLUDED_reversal"]
        excluded_orphan = [r for r in all_rows
                           if r["ssot_filter_status"] == "EXCLUDED_legacy_orphan"]
        excluded_not_posted = [r for r in all_rows
                               if r["ssot_filter_status"] == "EXCLUDED_not_posted"]

        # ── 3. Optional: legacy account_transactions ──────────────
        # NOTE: account_transactions stores `account_id` (bank/cash
        # account) and links to employees via `peer_liability_id` →
        # `liabilities.entity_id`. Direct entity_type filter would
        # always return zero rows. We discover the linkage via
        # liabilities first, then pull matching account_transactions.
        legacy_account_txns: List[Dict[str, Any]] = []
        legacy_summary: Optional[Dict[str, Any]] = None
        legacy_error: Optional[str] = None
        if include_account_transactions:
            try:
                liability_ids: List[str] = []
                liabilities_rows = await db.liabilities.find(
                    {"user_id": uid,
                     "$or": [
                         {"entity_id": employee_id},
                         {"employee_id": employee_id},
                     ]},
                    {"_id": 0, "id": 1, "entity_id": 1,
                     "employee_id": 1, "title": 1, "type": 1,
                     "status": 1, "amount": 1, "remaining_amount": 1},
                ).to_list(500)
                for lia in liabilities_rows:
                    if lia.get("id"):
                        liability_ids.append(lia["id"])

                inc = 0.0
                out = 0.0
                n_in = 0
                n_out = 0
                if liability_ids:
                    async for r in db.account_transactions.aggregate([
                        {"$match": {
                            "user_id": uid,
                            "peer_liability_id":
                                {"$in": liability_ids}}},
                        {"$group": {"_id": "$direction",
                                    "total": {"$sum": "$amount"},
                                    "n": {"$sum": 1}}},
                    ]):
                        if r["_id"] == "in":
                            inc = float(r["total"] or 0)
                            n_in = int(r["n"] or 0)
                        elif r["_id"] == "out":
                            out = float(r["total"] or 0)
                            n_out = int(r["n"] or 0)

                    async for t in db.account_transactions.find(
                        {"user_id": uid,
                         "peer_liability_id":
                             {"$in": liability_ids}},
                        {"_id": 0, "id": 1, "account_id": 1,
                         "transaction_type": 1, "amount": 1,
                         "direction": 1, "description": 1,
                         "transaction_date": 1, "created_at": 1,
                         "txn_group_id": 1,
                         "peer_liability_id": 1},
                    ).limit(500):
                        legacy_account_txns.append({
                            "id": t.get("id"),
                            "account_id": t.get("account_id"),
                            "transaction_type":
                                t.get("transaction_type"),
                            "amount": _r(t.get("amount")),
                            "direction": t.get("direction"),
                            "description":
                                (t.get("description") or "")[:120],
                            "transaction_date":
                                str(t.get("transaction_date"))
                                if t.get("transaction_date") else None,
                            "created_at":
                                str(t.get("created_at"))
                                if t.get("created_at") else None,
                            "txn_group_id": t.get("txn_group_id"),
                            "peer_liability_id":
                                t.get("peer_liability_id"),
                        })

                legacy_summary = {
                    "liabilities_linked_count": len(liability_ids),
                    "liability_ids_sample": liability_ids[:5],
                    "in_total": _r(inc),
                    "out_total": _r(out),
                    "net": _r(inc - out),
                    "in_count": n_in,
                    "out_count": n_out,
                }
            except Exception as e:  # noqa: BLE001
                legacy_error = repr(e)

        # ── 4. Build the answer ────────────────────────────────────
        try:
            ssot_view = _net_by_sub(all_included)
            unfiltered_view = _net_by_sub(all_rows)
            orphan_only_view = _net_by_sub(excluded_orphan)
            reversal_only_view = _net_by_sub(excluded_reversal)

            filtered_impact: List[Dict[str, Any]] = []
            for sub in set(list(ssot_view.keys())
                           + list(unfiltered_view.keys())):
                ssot_net = ssot_view.get(sub, {}).get("net", 0.0)
                full_net = unfiltered_view.get(sub, {}).get("net", 0.0)
                delta = _r(full_net - ssot_net)
                if abs(delta) > 0.005:
                    filtered_impact.append({
                        "sub_account": sub,
                        "ssot_net": ssot_net,
                        "unfiltered_net": full_net,
                        "delta_hidden_by_filters": delta,
                        "orphan_net":
                            orphan_only_view.get(sub, {}).get(
                                "net", 0.0),
                        "reversal_net":
                            reversal_only_view.get(sub, {}).get(
                                "net", 0.0),
                    })
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": "aggregation_failed",
                "details": repr(e),
                "received_params": received_params,
                "employee_id": employee_id,
                "row_counts": {"all": len(all_rows)},
            }

        return {
            "ok": True,
            "iter": "iter250b_p1_5_m",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "received_params": received_params,
            "employee": {
                "id": emp["id"],
                "name": emp.get("name"),
                "monthly_amount": _r(emp.get("monthly_amount")),
                "status": emp.get("status"),
            },
            "totals_by_sub_account": {
                "ssot_view": ssot_view,
                "ssot_view_explanation": (
                    "نفس ما تعرضه صفحة الموظفين و /new-transaction."
                ),
                "unfiltered_view": unfiltered_view,
                "unfiltered_view_explanation": (
                    "كل القيود بدون فلاتر — للمقارنة فقط."
                ),
                "filtered_impact": filtered_impact,
            },
            "row_counts": {
                "all": len(all_rows),
                "included_by_ssot": len(all_included),
                "excluded_reversal": len(excluded_reversal),
                "excluded_legacy_orphan": len(excluded_orphan),
                "excluded_not_posted": len(excluded_not_posted),
            },
            "all_rows": all_rows,
            "excluded_legacy_orphan_rows": excluded_orphan,
            "excluded_reversal_rows": excluded_reversal,
            "legacy_account_transactions_summary": legacy_summary,
            "legacy_account_transactions": legacy_account_txns,
            "legacy_account_transactions_error": legacy_error,
            "notes": [
                "READ-ONLY · no writes performed.",
                "ssot_view = ما تعرضه /employees/list و /summary-balance.",
                "filtered_impact = الفجوة بين الفلتر والـ raw — لو وُجد "
                "advance=14700 هنا ⇒ القيد مخفي بسبب legacy_orphan.",
                "account_transactions linked via "
                "liabilities.entity_id = employee_id ⇒ "
                "peer_liability_id (ليس entity_id).",
                "include_account_transactions=true ⇒ يَجلب القيود "
                "الـ legacy المرتبطة بـ liabilities الموظف.",
            ],
        }

    return router


__all__ = ["make_employee_ledger_forensic_router"]
