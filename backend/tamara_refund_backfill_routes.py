"""Iter-246y — Tamara refund ledger backfill (Dry-Run + Gated Apply).

Tamara has 0 `bnpl_refund` entries in general_ledger while Tabby has
20 (proven by the Iter-246x health endpoint).  This is because the
default `safe_post_refund` flow skips Tamara refunds when their
underlying sale was excluded by `BNPL_BRIDGE_CUTOFF_ISO` — but the
sale ITSELF was nevertheless booked by the bnpl_ledger_bridge as a
DEBIT on `payment_gateway.tamara.receivable`, leaving a phantom
receivable that never closes.

This module exposes:

  GET  /api/audit/tamara-refund-backfill-dry-run
       → lists every Tamara payment_refund not yet in the ledger,
         classifies the reason, and shows the proposed entries.
         READ-ONLY.

  POST /api/admin/tamara-refund-backfill-apply
       → gated by header `X-Apply-Token` (must equal what the
         dry-run printed in `apply_token`).  Posts the proposed
         entries via the SAME `post_bnpl_refund_to_ledger` path
         that Tabby already uses — guaranteeing identical schema /
         metadata / idempotency.

STRICT:
  • No mutations on `bnpl_sale` or settled `bnpl_settlement` rows.
  • Tabby logic untouched.
  • Idempotent: re-runs of apply are no-ops.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _apply_token_for(user_id: str, count: int, total: float) -> str:
    payload = f"iter246y|{user_id}|{count}|{total:.2f}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def make_tamara_refund_backfill_router(db, current_user):
    router = APIRouter(tags=["audit", "tamara"])

    async def _scan_refunds(uid: str) -> List[Dict[str, Any]]:
        """Walk every Tamara payment_refund and classify it."""
        from bnpl.ledger_bridge import _already_posted, _before_cutoff
        out: List[Dict[str, Any]] = []
        async for rfd in db.payment_refunds.find(
            {"user_id": uid, "provider": "tamara"},
            {"_id": 0},
        ):
            refund_id = (rfd.get("provider_refund_id") or "").strip()
            payment_id = (rfd.get("provider_payment_id") or "").strip()
            amount = float(rfd.get("amount") or 0)
            refunded_at = (
                rfd.get("refunded_at")
                or rfd.get("created_at_provider")
            )

            reason: str
            already = False
            sale_present = False

            if not refund_id:
                reason = "missing_refund_id"
            elif amount <= 0:
                reason = "zero_amount"
            elif _before_cutoff(refunded_at):
                reason = "before_bridge_cutoff"
            elif rfd.get("is_pre_accounting"):
                reason = "marked_pre_accounting"
            else:
                idem = f"bnpl_refund:tamara:{refund_id}"
                if await _already_posted(db, uid, idem):
                    already = True
                    reason = "already_in_ledger"
                else:
                    if payment_id:
                        sale_idem = f"bnpl_sale:tamara:{payment_id}"
                        sale_present = await _already_posted(
                            db, uid, sale_idem)
                    if not sale_present:
                        reason = "underlying_sale_not_in_ledger"
                    else:
                        reason = "ready_to_backfill"

            out.append({
                "refund_doc_id": rfd.get("id"),
                "provider_refund_id": refund_id,
                "provider_payment_id": payment_id,
                "order_reference_id": rfd.get("order_reference_id"),
                "order_number": rfd.get("order_number"),
                "amount": _r(amount),
                "refunded_at": refunded_at,
                "already_in_ledger": already,
                "underlying_sale_in_ledger": sale_present,
                "classification": reason,
            })
        return out

    # ── Dry-Run ───────────────────────────────────────────────────
    @router.get("/audit/tamara-refund-backfill-dry-run")
    async def dry_run(user: dict = Depends(current_user)):
        uid = user["id"]
        rows = await _scan_refunds(uid)

        ready = [r for r in rows if r["classification"]
                 == "ready_to_backfill"]
        skipped: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            if r["classification"] != "ready_to_backfill":
                skipped.setdefault(r["classification"], []).append(r)

        total_ready = _r(sum(r["amount"] for r in ready))

        proposed_entries: List[Dict[str, Any]] = []
        for r in ready:
            order_ref = (
                r.get("order_reference_id")
                or r.get("provider_payment_id")
                or r.get("provider_refund_id")
            )
            proposed_entries.append({
                "provider_refund_id": r["provider_refund_id"],
                "order": order_ref,
                "amount": r["amount"],
                "idempotency_key":
                    f"bnpl_refund:tamara:{r['provider_refund_id']}",
                "legs": [
                    {"entity": "revenue.bnpl_sales",
                     "side": "debit", "amount": r["amount"]},
                    {"entity":
                        "payment_gateway.tamara.receivable",
                     "side": "credit", "amount": r["amount"]},
                ],
            })

        return {
            "ok": True,
            "iter": "iter246y",
            "read_only": True,
            "total_refunds_scanned": len(rows),
            "ready_to_backfill_count": len(ready),
            "ready_to_backfill_sum": total_ready,
            "skipped_breakdown": {
                k: {"count": len(v),
                    "sum": _r(sum(x["amount"] for x in v))}
                for k, v in skipped.items()
            },
            "skipped_samples": {
                k: v[:5] for k, v in skipped.items()
            },
            "proposed_entries": proposed_entries,
            "apply_token": _apply_token_for(
                uid, len(ready), total_ready),
            "apply_endpoint":
                "POST /api/admin/tamara-refund-backfill-apply",
        }

    # ── Gated Apply ───────────────────────────────────────────────
    @router.post("/admin/tamara-refund-backfill-apply")
    async def apply(
        user: dict = Depends(current_user),
        x_apply_token: Optional[str] = Header(None,
                                              alias="X-Apply-Token"),
    ):
        uid = user["id"]
        rows = await _scan_refunds(uid)
        ready = [r for r in rows if r["classification"]
                 == "ready_to_backfill"]
        total_ready = _r(sum(r["amount"] for r in ready))
        expected = _apply_token_for(uid, len(ready), total_ready)

        if not x_apply_token or x_apply_token != expected:
            raise HTTPException(
                401,
                "X-Apply-Token header missing or stale.  Re-run the "
                "dry-run endpoint and copy the new `apply_token`. "
                "Note: the token is derived from (ready_count, sum) "
                "so it auto-invalidates when the dataset changes.",
            )

        from bnpl.ledger_bridge import post_bnpl_refund_to_ledger
        applied: List[Dict[str, Any]] = []
        for r in ready:
            rfd = await db.payment_refunds.find_one(
                {"user_id": uid, "id": r["refund_doc_id"]},
                {"_id": 0},
            )
            if not rfd:
                continue
            try:
                res = await post_bnpl_refund_to_ledger(
                    db, user_id=uid, refund=rfd)
                applied.append({
                    "provider_refund_id": r["provider_refund_id"],
                    "amount": r["amount"],
                    "txn_group_id": res.get("txn_group_id"),
                    "skipped": bool(res.get("skipped")),
                    "reason": res.get("reason"),
                })
            except Exception as e:  # noqa: BLE001
                applied.append({
                    "provider_refund_id": r["provider_refund_id"],
                    "amount": r["amount"],
                    "error": f"{type(e).__name__}: {e}",
                })

        return {
            "ok": True,
            "iter": "iter246y",
            "applied_count": sum(
                1 for x in applied
                if not x.get("error") and not x.get("skipped")),
            "applied_sum": _r(sum(
                x["amount"] for x in applied
                if not x.get("error") and not x.get("skipped"))),
            "error_count": sum(1 for x in applied if x.get("error")),
            "skipped_count": sum(
                1 for x in applied if x.get("skipped")),
            "results": applied,
            "note": (
                "Tabby refund flow was NOT modified.  Tamara refunds "
                "now mirror the same `post_bnpl_refund_to_ledger` "
                "path Tabby has always used."
            ),
        }

    return router
