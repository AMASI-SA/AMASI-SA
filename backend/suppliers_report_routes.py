"""Iter-246k — Suppliers analytical report.

Aggregates across 3 collections so the merchant gets ONE
ground-truth view of every supplier:

  • `suppliers`               (Iter-244) — primary catalog
  • `counterparties` (kind=supplier) — legacy fallback
  • `financial_movements` where movement_type=supplier_invoice
       → invoice count, gross total, paid sum,
         last invoice date.
  • `general_ledger` where entity_type=supplier
       → outstanding_debt (== SSOT, identical to
         `/api/ledger/balance`).  Crucial for partial / credit
         invoices to surface real liability.

Filters:
  q                  — substring on name (case-insensitive)
  status             — active / inactive / all (default: all)
  category_id        — limits to suppliers linked to that category
                       (leaf or any ancestor up the tree)
  with_debt_only     — only return outstanding_debt > 0
  date_from / date_to — restrict the invoice aggregates window
                       (inclusive ISO date strings YYYY-MM-DD)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_suppliers_report_router(db, current_user):
    router = APIRouter(prefix="/reports/suppliers",
                       tags=["reports", "suppliers"])

    @router.get("")
    async def suppliers_report(
        user: dict = Depends(current_user),
        q: Optional[str] = Query(None, min_length=1, max_length=80),
        status: str = Query("all"),
        category_id: Optional[str] = Query(None),
        with_debt_only: bool = Query(False),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
    ):
        uid = user["id"]

        # 1) Build supplier catalog from BOTH sources (deduped by id).
        catalog: dict[str, dict] = {}
        async for s in db.suppliers.find(
            {"user_id": uid},
            {"_id": 0, "id": 1, "company_name": 1, "contact_person": 1,
             "phone": 1, "email": 1, "status": 1, "category_ids": 1},
        ):
            catalog[s["id"]] = {
                "id": s["id"],
                "name": s.get("company_name"),
                "contact_person": s.get("contact_person"),
                "phone": s.get("phone"),
                "email": s.get("email"),
                "status": s.get("status", "active"),
                "category_ids": s.get("category_ids") or [],
                "source": "suppliers",
            }
        async for c in db.counterparties.find(
            {"user_id": uid, "kind": "supplier"},
            {"_id": 0, "id": 1, "name": 1, "contact_person": 1,
             "phone": 1, "email": 1, "status": 1},
        ):
            if c["id"] in catalog:
                continue
            catalog[c["id"]] = {
                "id": c["id"],
                "name": c.get("name"),
                "contact_person": c.get("contact_person"),
                "phone": c.get("phone"),
                "email": c.get("email"),
                "status": c.get("status", "active"),
                "category_ids": [],
                "source": "counterparty",
            }

        # 2) Apply text + status + category filters BEFORE the heavy
        #    per-supplier ledger / movement aggregation so we don't pay
        #    for rows the merchant doesn't see.
        def _matches(row: dict) -> bool:
            if q:
                name = (row.get("name") or "").lower()
                if q.lower() not in name:
                    return False
            if status != "all" and row.get("status") != status:
                return False
            if category_id and category_id not in row.get(
                    "category_ids") or []:
                return False
            return True

        # Category filter — if `category_id` is a non-leaf, expand to
        # include all leaves under it so a supplier linked to «منتجات»
        # surfaces under a search for «تكاليف المنتجات» too.
        cat_match_ids: Optional[set] = None
        if category_id:
            cat = await db.expense_category_tree.find_one(
                {"id": category_id, "user_id": uid},
                {"_id": 0, "id": 1, "path_ids": 1},
            )
            if cat:
                # All descendants share `category_id` in their path_ids.
                desc_ids = set()
                async for c in db.expense_category_tree.find(
                    {"user_id": uid,
                     "path_ids": category_id},
                    {"_id": 0, "id": 1},
                ):
                    desc_ids.add(c["id"])
                desc_ids.add(category_id)
                cat_match_ids = desc_ids

        def _category_ok(row: dict) -> bool:
            if not category_id:
                return True
            return bool(set(row.get("category_ids") or [])
                        & (cat_match_ids or {category_id}))

        filtered = [r for r in catalog.values()
                    if _matches(r) and _category_ok(r)]

        # 3) Compute per-supplier aggregates.
        date_q: dict = {}
        if date_from:
            date_q.setdefault("doc_date", {})["$gte"] = date_from
        if date_to:
            date_q.setdefault("doc_date", {})["$lte"] = date_to

        # Category names cache for the «التصنيفات المرتبط بها» column.
        cat_id_set = {
            cid for r in filtered for cid in (r.get("category_ids") or [])
        }
        cat_names: dict[str, str] = {}
        if cat_id_set:
            async for c in db.expense_category_tree.find(
                {"id": {"$in": list(cat_id_set)}, "user_id": uid},
                {"_id": 0, "id": 1, "name": 1, "path": 1},
            ):
                pth = c.get("path") or [c.get("name") or ""]
                cat_names[c["id"]] = " › ".join(pth)

        # Ledger balance bulk-call for outstanding_debt.
        from ledger_core import compute_balances_bulk
        ids = [r["id"] for r in filtered]
        balances = (await compute_balances_bulk(
            db, user_id=uid, entity_type="supplier",
            entity_ids=ids,
        )) if ids else {}

        # Per-supplier movement aggregates (filtered by date if given).
        out_rows = []
        for row in filtered:
            mvq = {"user_id": uid,
                   "movement_type": "supplier_invoice",
                   "supplier_id": row["id"], **date_q}
            inv_count = 0
            inv_total = 0.0
            inv_paid = 0.0
            last_inv_date: Optional[str] = None
            last_inv_doc: Optional[str] = None
            async for mv in db.financial_movements.find(
                mvq, {"_id": 0, "total_amount": 1, "paid_amount": 1,
                      "doc_date": 1, "doc_number": 1,
                      "created_at": 1},
            ).sort([("doc_date", -1)]):
                inv_count += 1
                inv_total += float(mv.get("total_amount") or 0)
                inv_paid += float(mv.get("paid_amount") or 0)
                if last_inv_date is None:
                    last_inv_date = mv.get("doc_date")
                    last_inv_doc = mv.get("doc_number")
            # Last ledger activity is broader than just invoices —
            # also includes supplier_pay debits.  Cheap query.
            last_ledger = await db.general_ledger.find_one(
                {"user_id": uid, "entity_type": "supplier",
                 "entity_id": row["id"]},
                {"_id": 0, "posted_at": 1},
                sort=[("posted_at", -1)],
            )
            last_activity = (
                last_ledger.get("posted_at") if last_ledger else last_inv_date
            )

            bal = balances.get(row["id"], {})
            outstanding = _r(bal.get("outstanding_debt", 0))

            agg = {
                "id": row["id"],
                "name": row["name"],
                "contact_person": row.get("contact_person"),
                "phone": row.get("phone"),
                "status": row.get("status"),
                "categories": [
                    {"id": cid, "name": cat_names.get(cid, cid)}
                    for cid in (row.get("category_ids") or [])
                ],
                "invoices_count": inv_count,
                "invoices_total": _r(inv_total),
                "paid_total": _r(inv_paid),
                "remaining_total": _r(max(inv_total - inv_paid, 0)),
                "outstanding_debt": outstanding,
                "last_invoice_date": last_inv_date,
                "last_invoice_doc_number": last_inv_doc,
                "last_activity": last_activity,
                "ledger_url": f"/suppliers/{row['id']}/ledger-detail",
                "source": row.get("source"),
            }
            if with_debt_only and outstanding <= 0:
                continue
            out_rows.append(agg)

        # Sort by outstanding desc → invoices_total desc → name asc.
        out_rows.sort(key=lambda r: (
            -r["outstanding_debt"], -r["invoices_total"],
            (r["name"] or "").lower()))

        totals = {
            "suppliers_count": len(out_rows),
            "invoices_count": sum(r["invoices_count"] for r in out_rows),
            "invoices_total": _r(
                sum(r["invoices_total"] for r in out_rows)),
            "paid_total": _r(sum(r["paid_total"] for r in out_rows)),
            "outstanding_debt": _r(
                sum(r["outstanding_debt"] for r in out_rows)),
        }
        return {
            "ok": True,
            "iter": "iter246k",
            "generated_at": _now(),
            "filters": {
                "q": q, "status": status, "category_id": category_id,
                "with_debt_only": with_debt_only,
                "date_from": date_from, "date_to": date_to,
            },
            "totals": totals,
            "suppliers": out_rows,
        }

    return router
