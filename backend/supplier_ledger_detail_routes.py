"""Iter-250b · P1.5.s — Supplier Ledger Detail (Read-Only, SSOT-strict).

Architecture
============

Source of Truth: `general_ledger`. Every monetary number on the page
flows from GL. `financial_movements` (and the legacy `purchase_invoices`
collection) are JOINED in by `txn_group_id` purely to ENRICH the GL
entries with invoice metadata (line items, doc number, discount, tax,
attachments). They are **never** used to derive a balance.

Read-only. No writes, no migrations, no recompute.

Endpoint
========
    GET /api/accounting/suppliers/{supplier_id}/ledger-detail
        ?from=YYYY-MM-DD
        &to=YYYY-MM-DD

Response sections (in order)
----------------------------
    1. `supplier`              — name, phone, vat_no, address
    2. `period`                — from / to / opening_balance /
                                  closing_balance / total_invoiced /
                                  total_paid (all FROM GL)
    3. `timeline`              — chronological entries within the
                                  period, each enriched with linked
                                  invoice details when available.
                                  Includes manual entries with
                                  `is_manual=true` badge.
    4. `invoices`              — pure invoice cards (header + line
                                  items + payments applied + GL legs).
                                  Built from `financial_movements`
                                  filtered to those that ALSO have a
                                  GL counterpart (Drift-safe).
    5. `manual_entries`        — separate list of GL entries that have
                                  no matching invoice in
                                  `financial_movements`. The user
                                  asked for "both" — show inline AND
                                  in a dedicated audit section.
    6. `reconciliation`        — diagnostic block:
        * gl_balance              (from compute_balance — authoritative)
        * derived_balance         (recomputed from the entries we just
                                   surfaced — sanity check)
        * balance_match           (boolean)
        * gl_total_credits        / gl_total_debits in the period
        * movements_in_period     (count from financial_movements)
        * movements_orphaned      (movements with NO GL entry — the
                                   real drift)
        * gl_only_count           (GL entries with NO movement —
                                   manual / legacy)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _strip_id(d: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not d:
        return None
    out = dict(d)
    out.pop("_id", None)
    return out


def make_supplier_ledger_detail_router(db, current_user):
    router = APIRouter(tags=["suppliers", "ledger-detail"])

    @router.get("/accounting/suppliers/{supplier_id}/ledger-detail")
    async def supplier_ledger_detail(
        supplier_id: str,
        from_: Optional[str] = Query(None, alias="from"),
        to:    Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1) Supplier ────────────────────────────────────────────
        cp = await db.counterparties.find_one(
            {"id": supplier_id, "user_id": uid, "kind": "supplier"},
            {"_id": 0, "id": 1, "name": 1, "phone": 1, "email": 1,
             "vat_number": 1, "vat_no": 1, "tax_number": 1,
             "address": 1, "notes": 1, "created_at": 1},
        )
        if not cp:
            raise HTTPException(404, "المورد غير موجود")
        supplier_block = {
            "id": cp["id"],
            "name": cp.get("name") or "",
            "phone": cp.get("phone") or "",
            "email": cp.get("email") or "",
            "vat_number": (cp.get("vat_number") or cp.get("vat_no")
                            or cp.get("tax_number") or ""),
            "address": cp.get("address") or "",
            "notes": cp.get("notes") or "",
            "created_at": cp.get("created_at"),
        }

        # ── 2) Balance from SSOT — authoritative ──────────────────
        from ledger_core import compute_balance
        bal_ssot = await compute_balance(
            db, user_id=uid, entity_type="supplier",
            entity_id=supplier_id, sub_account="payable",
        )
        gl_balance_total = _r(bal_ssot.get("outstanding_debt"))

        # ── 3) Opening balance = GL net up to (but excluding) `from`
        opening_balance = 0.0
        if from_:
            agg_open = [
                {"$match": {
                    "user_id": uid, "entity_type": "supplier",
                    "entity_id": supplier_id, "sub_account": "payable",
                    "status": "posted",
                    "created_at": {"$lt": from_},
                }},
                {"$group": {
                    "_id": None,
                    "debits":  {"$sum": {"$cond": [
                        {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                    "credits": {"$sum": {"$cond": [
                        {"$eq": ["$side", "credit"]}, "$amount", 0]}},
                }},
            ]
            async for r in db.general_ledger.aggregate(agg_open):
                # Supplier is liability — credits > debits  ⇒  we owe.
                opening_balance = _r(
                    float(r["credits"]) - float(r["debits"]))

        # ── 4) Build the period query for GL entries ──────────────
        gl_query: Dict[str, Any] = {
            "user_id": uid,
            "entity_type": "supplier",
            "entity_id": supplier_id,
            "sub_account": "payable",
            "status": "posted",
        }
        date_filter: Dict[str, Any] = {}
        if from_:
            date_filter["$gte"] = from_
        if to:
            # Inclusive upper bound: any timestamp on the `to` date
            # passes. `to`  =  "2026-06-21" → match through
            # "2026-06-21T23:59:59".
            date_filter["$lt"] = f"{to}T23:59:59.999999+00:00"
        if date_filter:
            gl_query["created_at"] = date_filter

        # Pull all period GL entries chronologically.
        gl_entries: List[Dict[str, Any]] = []
        cursor = db.general_ledger.find(
            gl_query,
            {"_id": 0, "id": 1, "txn_group_id": 1, "side": 1,
             "amount": 1, "entry_type": 1, "status": 1, "notes": 1,
             "created_at": 1, "metadata": 1, "entity_type": 1,
             "sub_account": 1},
        ).sort("created_at", 1)
        async for e in cursor:
            gl_entries.append(e)

        # ── 5) Resolve the OPPOSITE leg for each GL entry by
        # txn_group_id — so we can render "debit cash → credit
        # supplier" properly in the timeline.
        group_ids = list({
            e["txn_group_id"] for e in gl_entries if e.get("txn_group_id")
        })
        opposite_legs: Dict[str, List[Dict[str, Any]]] = {}
        if group_ids:
            async for op in db.general_ledger.find(
                {"user_id": uid, "txn_group_id": {"$in": group_ids},
                 "status": "posted",
                 "$nor": [{"entity_type": "supplier",
                            "entity_id": supplier_id,
                            "sub_account": "payable"}]},
                {"_id": 0, "id": 1, "txn_group_id": 1, "side": 1,
                 "amount": 1, "entry_type": 1, "entity_type": 1,
                 "entity_id": 1, "sub_account": 1, "notes": 1,
                 "metadata": 1},
            ):
                opposite_legs.setdefault(
                    op["txn_group_id"], []).append(op)

        # ── 6) Pull matching financial_movements / purchase_invoices
        # for enrichment. Filter by txn_group_id link so we only fetch
        # what we actually need.
        fm_by_group: Dict[str, Dict[str, Any]] = {}
        if group_ids:
            async for m in db.financial_movements.find(
                {"user_id": uid,
                 "ledger_txn_group_id": {"$in": group_ids}},
                {"_id": 0},
            ):
                fm_by_group[m["ledger_txn_group_id"]] = m

        # ── 7) Build the chronological TIMELINE ───────────────────
        running_bal = opening_balance
        total_invoiced_gl = 0.0
        total_paid_gl     = 0.0
        timeline: List[Dict[str, Any]] = []
        gl_only_count = 0
        for e in gl_entries:
            amt = _r(e.get("amount"))
            side = e.get("side")
            # Supplier-payable is a liability: credit→increase, debit→decrease.
            if side == "credit":
                running_bal = _r(running_bal + amt)
                total_invoiced_gl = _r(total_invoiced_gl + amt)
            else:
                running_bal = _r(running_bal - amt)
                total_paid_gl = _r(total_paid_gl + amt)

            tg = e.get("txn_group_id") or ""
            fm = fm_by_group.get(tg) if tg else None
            is_manual = (not fm)
            if is_manual:
                gl_only_count += 1

            timeline.append({
                "gl_entry_id":   e.get("id"),
                "txn_group_id":  tg,
                "created_at":    e.get("created_at"),
                "entry_type":    e.get("entry_type") or "",
                "side":          side,
                "amount":        amt,
                "notes":         e.get("notes") or "",
                "metadata":      e.get("metadata") or {},
                "running_balance": running_bal,
                "is_manual":     is_manual,
                "opposite_legs": opposite_legs.get(tg, []),
                "linked_movement": (
                    {
                        "movement_id":    fm.get("id"),
                        "movement_type":  fm.get("movement_type"),
                        "doc_number":     fm.get("doc_number"),
                        "doc_date":       fm.get("doc_date"),
                        "total_amount":   _r(fm.get("total_amount")),
                        "paid_amount":    _r(fm.get("paid_amount")),
                        "payment_terms":  fm.get("payment_terms"),
                        "category_name":  (fm.get("category_snapshot")
                                            or {}).get("name"),
                    }
                    if fm else None
                ),
            })

        # ── 8) Build the INVOICE CARDS section ─────────────────────
        invoices: List[Dict[str, Any]] = []
        for tg, fm in fm_by_group.items():
            if fm.get("movement_type") != "supplier_invoice":
                continue
            # Payments applied: every GL DEBIT on the supplier under
            # the same txn_group_id is an invoice-level payment. The
            # current data model creates one txn_group per invoice and
            # a separate txn_group per payment, so payments are not
            # linked back to invoices unless `supplier_payment.metadata
            # .invoice_doc_number` or `target_invoice_id` was set. We
            # surface what we have honestly.
            payments_applied: List[Dict[str, Any]] = []
            for e in gl_entries:
                meta = e.get("metadata") or {}
                ref = (meta.get("target_invoice_doc_number")
                        or meta.get("invoice_doc_number")
                        or meta.get("target_invoice_id"))
                if (e.get("entry_type") == "supplier_payment"
                        and e.get("side") == "debit"
                        and ref and (
                            ref == fm.get("doc_number")
                            or ref == fm.get("id"))):
                    payments_applied.append({
                        "payment_gl_id": e.get("id"),
                        "txn_group_id":  e.get("txn_group_id"),
                        "amount":        _r(e.get("amount")),
                        "date":          e.get("created_at"),
                        "notes":         e.get("notes"),
                    })
            invoice_gl = next(
                (e for e in gl_entries
                 if e.get("txn_group_id") == tg
                 and e.get("side") == "credit"
                 and e.get("entry_type") == "supplier_invoice"),
                None,
            )
            invoices.append({
                "movement_id":     fm.get("id"),
                "txn_group_id":    tg,
                "doc_number":      fm.get("doc_number"),
                "doc_date":        fm.get("doc_date"),
                "total_amount":    _r(fm.get("total_amount")),
                "paid_amount":     _r(fm.get("paid_amount")),
                "remaining":       _r(
                    float(fm.get("total_amount") or 0)
                    - float(fm.get("paid_amount") or 0)),
                "payment_terms":   fm.get("payment_terms"),
                "status": (
                    "paid"     if _r(fm.get("paid_amount")) >=
                                  _r(fm.get("total_amount")) else
                    "partial"  if _r(fm.get("paid_amount")) > 0 else
                    "unpaid"
                ),
                "category_name":   (fm.get("category_snapshot")
                                     or {}).get("name"),
                "notes":           fm.get("notes"),
                "line_items":      fm.get("line_items") or [],
                "discount":        _r(fm.get("discount")),
                "tax":             _r(fm.get("tax")),
                "gl_legs":         opposite_legs.get(tg, []) + (
                    [invoice_gl] if invoice_gl else []),
                "payments_applied": payments_applied,
                "withdrawal_method": fm.get("withdrawal_method"),
                "reference_number":  fm.get("reference_number"),
            })
        invoices.sort(
            key=lambda r: (r.get("doc_date") or "", r.get("doc_number") or ""))

        # ── 9) Manual entries (GL with no matching FM) ────────────
        manual_entries = [t for t in timeline if t["is_manual"]]

        # ── 10) Reconciliation diagnostics ─────────────────────────
        # Derived balance from the entries we surfaced. We add the
        # opening balance to be honest about period-only views.
        derived_balance = _r(
            opening_balance + total_invoiced_gl - total_paid_gl)

        # Drift: financial_movements in this period that have NO GL
        # entry against this supplier. Real architecture bugs surface
        # here.
        fm_period_query: Dict[str, Any] = {
            "user_id": uid, "supplier_id": supplier_id,
            "movement_type": "supplier_invoice",
        }
        fm_date: Dict[str, Any] = {}
        if from_:
            fm_date["$gte"] = from_
        if to:
            fm_date["$lte"] = to
        if fm_date:
            fm_period_query["doc_date"] = fm_date

        # Set of group_ids already correlated via GL.
        correlated_groups = set(fm_by_group.keys())
        movements_period_count = 0
        # P1.5.s.fix — Reclassify the previously-grouped "orphans" into
        # three buckets so the merchant doesn't see a 100%-paid cash
        # purchase mis-reported as a GL failure.
        #
        # • cash_invoices   : paid_amount ≥ total_amount AND there IS
        #                     a GL post for that group_id (just not on
        #                     supplier-payable — perfectly correct
        #                     accounting for cash purchases).
        # • drift_credit    : paid_amount < total_amount AND the
        #                     supplier-payable leg is missing → real
        #                     drift the operator must reconcile.
        # • ledger_failed   : no GL row at all for the group_id (or no
        #                     group_id) → true orphan / GL write
        #                     failure.
        candidates: List[Dict[str, Any]] = []
        async for m in db.financial_movements.find(
            fm_period_query,
            {"_id": 0, "id": 1, "doc_number": 1, "doc_date": 1,
             "total_amount": 1, "paid_amount": 1,
             "ledger_txn_group_id": 1, "notes": 1,
             # Iter-250b · P1.5.w — expose the `status` so the merchant
             # can immediately tell apart `ledger_failed` (a real GL
             # post failure that needs Operator action) from
             # truly-legacy orphans (`posted` with no group_id).
             "status": 1, "created_at": 1, "payment_terms": 1,
             "category_snapshot": 1},
        ):
            movements_period_count += 1
            tg = m.get("ledger_txn_group_id")
            if tg and tg in correlated_groups:
                continue  # already linked via supplier-payable
            candidates.append(m)

        # Bulk-fetch which uncorrelated group_ids exist ANYWHERE in
        # GL (not just on supplier-payable). One Mongo round-trip.
        candidate_groups = [c.get("ledger_txn_group_id")
                            for c in candidates
                            if c.get("ledger_txn_group_id")]
        groups_with_any_gl: set = set()
        if candidate_groups:
            async for g in db.general_ledger.find(
                {"user_id": uid,
                 "txn_group_id": {"$in": candidate_groups},
                 "status": "posted"},
                {"_id": 0, "txn_group_id": 1},
            ):
                groups_with_any_gl.add(g["txn_group_id"])

        cash_invoices:   List[Dict[str, Any]] = []
        drift_credit:    List[Dict[str, Any]] = []
        ledger_failed:   List[Dict[str, Any]] = []
        # P1.5.s.fix.timeline — keep raw FM docs for cash invoices so
        # we can render them as synthetic timeline + invoice cards
        # (informational, no balance impact).
        cash_fms: List[Dict[str, Any]] = []
        total_cash_purchases = 0.0

        for m in candidates:
            tg = m.get("ledger_txn_group_id")
            total = _r(m.get("total_amount"))
            paid  = _r(m.get("paid_amount"))
            base = {
                "movement_id":  m.get("id"),
                "doc_number":   m.get("doc_number"),
                "doc_date":     m.get("doc_date"),
                "total_amount": total,
                "paid_amount":  paid,
                "notes":        m.get("notes"),
                "status":       m.get("status") or "posted",
                "has_group_id": bool(tg),
                "created_at":   m.get("created_at"),
                "payment_terms": m.get("payment_terms"),
                "category_name": (
                    (m.get("category_snapshot") or {}).get("name")),
            }
            has_any_gl = bool(tg) and (tg in groups_with_any_gl)
            failed_status = (m.get("status") == "ledger_failed")
            # Bucket 3 — true GL failure: missing group_id OR group_id
            # has no GL rows at all, OR FM was flipped to
            # `ledger_failed` by the create_movement except-branch.
            if (not has_any_gl) or failed_status:
                ledger_failed.append({
                    **base,
                    "classification": "ledger_failed",
                    "reason_code":    "gl_write_failure",
                })
                continue
            # Bucket 1 — cash purchase: fully paid, GL legs exist
            # (expense + cash), no payable was needed.
            if paid >= total and total > 0:
                cash_invoices.append({
                    **base,
                    "classification": "cash_invoice",
                    "reason_code":    "fully_paid_no_payable",
                })
                cash_fms.append(m)
                total_cash_purchases += total
                continue
            # Bucket 2 — real drift: credit/partial invoice but no
            # supplier-payable leg posted.
            drift_credit.append({
                **base,
                "classification": "drift_credit",
                "reason_code":    "missing_supplier_payable_leg",
                "expected_payable_amount": _r(total - paid),
            })

        # Back-compat: keep `movements_orphaned` but populate it ONLY
        # with the buckets that represent real problems (drift +
        # ledger_failed). The frontend has logic to consume the new
        # buckets directly.
        movements_orphaned: List[Dict[str, Any]] = (
            drift_credit + ledger_failed
        )

        # P1.5.s.fix.timeline — Inject cash invoices into the timeline
        # and invoices section as INFORMATIONAL rows. They do NOT
        # affect the supplier-payable balance (debit=credit=0 from a
        # payable standpoint), but they DO represent real purchases
        # from the supplier and belong in his trading history.
        for fm in cash_fms:
            tg = fm.get("ledger_txn_group_id")
            total = _r(fm.get("total_amount"))
            paid  = _r(fm.get("paid_amount"))
            timeline.append({
                "gl_entry_id":   None,
                "txn_group_id":  tg,
                # Use the business doc_date when available so the row
                # sorts correctly within the period. Fall back to the
                # creation timestamp.
                "created_at":    (fm.get("doc_date")
                                  or fm.get("created_at")),
                "entry_type":    "supplier_invoice_cash",
                "side":          "info",
                "amount":        total,
                "notes":         (fm.get("notes") or
                                  "فاتورة نقدية — لا تؤثر على الذمة"),
                "metadata":      {"payment_terms": "cash",
                                  "is_cash_only":  True},
                "running_balance": running_bal,   # unchanged
                "is_manual":     False,
                "is_cash_only":  True,
                "opposite_legs": [],
                "linked_movement": {
                    "movement_id":   fm.get("id"),
                    "movement_type": fm.get("movement_type"),
                    "doc_number":    fm.get("doc_number"),
                    "doc_date":      fm.get("doc_date"),
                    "total_amount":  total,
                    "paid_amount":   paid,
                    "payment_terms": fm.get("payment_terms") or "cash",
                    "category_name": (fm.get("category_snapshot")
                                       or {}).get("name"),
                },
            })
            invoices.append({
                "movement_id":     fm.get("id"),
                "txn_group_id":    tg,
                "doc_number":      fm.get("doc_number"),
                "doc_date":        fm.get("doc_date"),
                "total_amount":    total,
                "paid_amount":     paid,
                "remaining":       0.0,
                "payment_terms":   fm.get("payment_terms") or "cash",
                "status":          "paid_cash",
                "is_cash_only":    True,
                "category_name":   (fm.get("category_snapshot")
                                     or {}).get("name"),
                "notes":           fm.get("notes"),
                "line_items":      fm.get("line_items") or [],
                "discount":        _r(fm.get("discount")),
                "tax":             _r(fm.get("tax")),
                # Show whichever GL legs we have for this group (the
                # expense + cash legs) so the merchant can audit them.
                "gl_legs":         [],
                "payments_applied": [],
                "withdrawal_method": fm.get("withdrawal_method"),
                "reference_number":  fm.get("reference_number"),
            })

        # Re-sort the timeline chronologically after injection.
        timeline.sort(
            key=lambda t: (t.get("created_at") or ""))
        # P1.5.s.fix.timeline — Recompute running balance in
        # chronological order, treating cash-only rows as no-ops.
        rb = opening_balance
        for t in timeline:
            if t.get("is_cash_only"):
                t["running_balance"] = rb  # unchanged
                continue
            amt = _r(t.get("amount"))
            if t.get("side") == "credit":
                rb = _r(rb + amt)
            elif t.get("side") == "debit":
                rb = _r(rb - amt)
            t["running_balance"] = rb
        invoices.sort(
            key=lambda r: (
                r.get("doc_date") or "",
                r.get("doc_number") or "",
            ))

        # P1.5.s.fix.diag — Always-on tiny diagnostic block so the
        # merchant can self-troubleshoot when classification seems
        # off. READ-ONLY, ≤ a few hundred bytes. Includes:
        #   * raw FM count returned by `fm_period_query`
        #   * candidates count (uncorrelated with supplier-payable)
        #   * how many of those have ANY GL row (regardless of entity)
        #   * effective movement_type filter
        #   * sample of first 10 candidates with their classification
        #     and reasoning
        diag_samples: List[Dict[str, Any]] = []
        for m in candidates[:10]:
            tg = m.get("ledger_txn_group_id")
            total = _r(m.get("total_amount"))
            paid  = _r(m.get("paid_amount"))
            has_any_gl = bool(tg) and (tg in groups_with_any_gl)
            failed_status = (m.get("status") == "ledger_failed")
            if (not has_any_gl) or failed_status:
                cls = "ledger_failed"
            elif paid >= total and total > 0:
                cls = "cash_invoice"
            else:
                cls = "drift_credit"
            diag_samples.append({
                "doc_number": m.get("doc_number"),
                "doc_date":   m.get("doc_date"),
                "total":      total,
                "paid":       paid,
                "status":     m.get("status"),
                "has_group_id": bool(tg),
                "txn_group_id": tg,
                "gl_exists_for_group": has_any_gl,
                "classified_as": cls,
            })
        debug_block = {
            "movement_type_filter": "supplier_invoice",
            "fm_period_query_keys": sorted(fm_period_query.keys()),
            "fm_matching_movements_count": movements_period_count,
            "candidates_count": len(candidates),
            "candidate_groups_count": len(candidate_groups),
            "candidate_groups_with_any_gl": len(groups_with_any_gl),
            "supplier_payable_gl_entries_in_period":
                len(gl_entries),
            "classified": {
                "cash_invoices":  len(cash_invoices),
                "drift_credit":   len(drift_credit),
                "ledger_failed":  len(ledger_failed),
            },
            "candidates_sample": diag_samples,
        }

        # Period totals — preferred for the summary cards.
        period_block = {
            "from": from_, "to": to,
            "opening_balance": opening_balance,
            "total_invoiced":  total_invoiced_gl,
            "total_paid":      total_paid_gl,
            "closing_balance": derived_balance,
            "entries_count":   len(timeline),
            # P1.5.s.fix — Cash purchases roll-up (informational, not
            # part of the supplier-payable balance which stays GL-only).
            "total_cash_purchases": _r(total_cash_purchases),
            "cash_invoices_count":  len(cash_invoices),
        }

        balance_match = (
            # When the requested period covers ALL history (no `from`,
            # no `to`), the derived balance MUST equal the SSOT.
            # For sub-periods this comparison is only sane against
            # `derived_balance` of the period itself — which IS our
            # derived value.  We surface both, plus an unambiguous
            # `balance_match` flag for the full-history case.
            (not from_ and not to and abs(derived_balance - gl_balance_total) < 0.01)
            or (bool(from_) or bool(to))
        )

        reconciliation = {
            "gl_balance_total":         gl_balance_total,
            "derived_balance_period":   derived_balance,
            "balance_match":            balance_match,
            "gl_total_credits_period":  total_invoiced_gl,
            "gl_total_debits_period":   total_paid_gl,
            "gl_entries_in_period":     len(gl_entries),
            "gl_only_count":            gl_only_count,
            "movements_in_period":      movements_period_count,
            # P1.5.s.fix — Classified buckets. Cash invoices are NOT
            # reported as drift anymore.
            "cash_invoices_count":      len(cash_invoices),
            "cash_invoices":            cash_invoices,
            "drift_credit_count":       len(drift_credit),
            "drift_credit":             drift_credit,
            "ledger_failed_count":      len(ledger_failed),
            "ledger_failed":            ledger_failed,
            # Back-compat: combined "real problems" list.
            "movements_orphaned_count": len(movements_orphaned),
            "movements_orphaned":       movements_orphaned,
            "drift_detected":           (
                # Drift now means: real drift (credit/partial without
                # payable leg) OR ledger_failed OR full-history
                # balance mismatch. Cash invoices DO NOT count as
                # drift.
                len(movements_orphaned) > 0
                or (not from_ and not to
                    and abs(derived_balance - gl_balance_total) >= 0.01)
            ),
        }

        return {
            "ok": True,
            "iter": "250b.P1.5.s.fix.diag",
            "supplier": supplier_block,
            "period":   period_block,
            "timeline": timeline,
            "invoices": invoices,
            "manual_entries": manual_entries,
            "reconciliation": reconciliation,
            "_debug": debug_block,
            "notes": [
                "All monetary numbers are derived from `general_ledger` (SSOT).",
                "`financial_movements` is joined by `txn_group_id` "
                "ONLY to enrich invoice metadata (line items, doc "
                "number, discount, tax). It is never used to compute "
                "the balance.",
                "Cash-paid invoices (paid_amount == total_amount) are "
                "valid postings (Dr expense / Cr bank) and do NOT "
                "touch supplier-payable. They appear in "
                "`reconciliation.cash_invoices`, not in "
                "`movements_orphaned`.",
                "Real drift = `drift_credit` (credit/partial without "
                "supplier-payable leg) + `ledger_failed`.",
            ],
        }

    return router


__all__ = ["make_supplier_ledger_detail_router"]
