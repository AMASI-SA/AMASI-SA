"""Iter-246q — Tamara Fix Apply (Final Dry-Run + Gated Execute).

TWO endpoints in this module:

  1) GET  /audit/tamara-apply-dryrun         — READ-ONLY simulation.
  2) POST /admin/tamara-apply-execute        — GATED writes (requires
                                               a confirm_token derived
                                               from period + user_id).

Decisions baked in (per the merchant's instructions):
  • Fix #1: 5 orders only (265239451 EXPLICITLY EXCLUDED).
  • Fix #2: 8 synthesised refund rows (all except 264553438 which is
            already correct).
  • Fix #3: 13 orders pinned to their historical settlement_date.
  • Same-Week Net-Zero Exclusion: orders captured AND refunded inside
            the SAME invoice window are flagged so the compute engines
            EXCLUDE THEM FROM GROSS ONLY (the refund stays in the
            Refunds column — mirrors Tamara invoice convention).
  • refunded_at policy: caller-supplied `refunded_at_override` is used
            for every refund row created/updated by this apply.

The execute endpoint NEVER touches general_ledger.  NEVER touches
balances.  NEVER touches Tabby.  Forward-only:  no rebuild of past
journal entries.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _safe(v) -> Optional[str]:
    if v is None:
        return None
    return str(v) if not isinstance(v, str) else v


def _extract_amount(v: Any) -> float:
    if isinstance(v, dict):
        return float(v.get("amount") or 0)
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _confirm_token(uid: str, date_from: str, date_to: str,
                    refunded_at_override: str) -> str:
    """Deterministic per-invoice confirm token.  Merchant must compute
    and pass this EXACTLY to authorise the execute."""
    raw = f"TAMARA_APPLY|{uid}|{date_from}|{date_to}|{refunded_at_override}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


async def _gather_fix_plan(
    db, uid: str, date_from: str, date_to: str,
    refunded_at_override: str, exclude_order_numbers: List[str],
    enable_same_week_netzero_exclusion: bool,
) -> Dict[str, Any]:
    """Single source-of-truth: compute the EXACT fix plan that
    dry-run displays and execute applies.  Pure-read.  Returns a
    dict with rows for fix1/fix2/fix3/netzero plus a simulated
    forensic compute."""
    from bnpl.settlements_service import (
        _local_date_window_utc, _merchant_fee_rates,
    )
    utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)
    rates = await _merchant_fee_rates(db, uid, "tamara")
    commission_rate = float(rates.get("commission_pct") or 0) / 100.0
    vat_rate = float(rates.get("vat_pct") or 0) / 100.0
    fixed_fee_per_order = float(rates.get("fixed_fee_per_order") or 0)

    excludes = set(exclude_order_numbers or [])
    window_lo = (utc_gte or "")[:10]
    window_hi = (utc_lte or "")[:10]

    def _in_window(d: Optional[str]) -> bool:
        if not d:
            return False
        return window_lo <= str(d)[:10] <= window_hi

    # In-window transactions (pre-fix view).
    in_window_txns: List[Dict[str, Any]] = (
        await db.payment_transactions.find(
            {"user_id": uid, "provider": "tamara",
             "effective_settlement_date": {
                 **({"$gte": utc_gte} if utc_gte else {}),
                 **({"$lte": utc_lte} if utc_lte else {}),
             },
             "is_pre_accounting": {"$ne": True}},
            {"_id": 0, "raw_payload": 0},
        ).to_list(5000)
    )
    txn_by_pid = {
        (t.get("provider_id") or ""): t for t in in_window_txns
    }

    # ── Fix #3 — pin historical settlement_date ────────────────
    fix3_rows: List[Dict[str, Any]] = []
    fix3_pinned_order_numbers: set[str] = set()
    for t in in_window_txns:
        onum = t.get("order_number")
        if onum in excludes:
            continue
        cur_esd_date = (t.get("effective_settlement_date") or "")[:10]
        earliest = None
        async for s in db.settlement_entries.find(
            {"user_id": uid, "provider": "tamara",
             "order_number": onum,
             "event_type": {"$ne": "refund"},
             "is_pre_accounting": {"$ne": True}},
            {"_id": 0, "settlement_date": 1, "file_hash": 1,
             "actual_gross_amount": 1},
        ).sort([("settlement_date", 1)]).limit(1):
            earliest = s
        if not earliest:
            continue
        hist_date = str(earliest.get("settlement_date") or "")[:10]
        if not hist_date or hist_date >= cur_esd_date:
            continue
        fix3_rows.append({
            "order_number": _safe(onum),
            "provider_id": _safe(t.get("provider_id")),
            "amount": _r(t.get("amount") or 0),
            "before_effective_settlement_date":
                _safe(t.get("effective_settlement_date")),
            "after_effective_settlement_date": hist_date,
            "before_settlement_source": t.get("settlement_source"),
            "after_settlement_source": "settlement_entries_historical",
            "source_file_hash": earliest.get("file_hash"),
        })
        fix3_pinned_order_numbers.add(onum)

    # ── Fix #1 — refund new orders ─────────────────────────────
    # Deterministic Fix #1 selection (no live API needed for the
    # dry-run/execute):
    #   (a) currently fully_captured locally
    #   (b) has a historical settlement_entry (= Fix #3 candidate)
    #   (c) is NOT in excludes
    # The merchant has already verified live-API status separately
    # via iter246p before approving the apply.
    fix1_rows: List[Dict[str, Any]] = []
    fix1_pids: set[str] = set()
    for t in in_window_txns:
        if t.get("status") != "fully_captured":
            continue
        if t.get("order_number") in excludes:
            continue
        if t.get("order_number") not in fix3_pinned_order_numbers:
            continue
        pid = (t.get("provider_id") or "").strip()
        if not pid:
            continue
        fix1_pids.add(pid)
        amt = _r(t.get("amount") or 0)
        fix1_rows.append({
            "order_number": _safe(t.get("order_number")),
            "provider_id": pid,
            "before_status": t.get("status"),
            "before_refunded_amount": _r(t.get("refunded_amount") or 0),
            "after_status": "fully_refunded",
            "after_refunded_amount": amt,
            "payment_refund_row_to_create": {
                "provider_refund_id": f"synthetic:{pid}",
                "amount": amt,
                "refunded_at": refunded_at_override,
                "status": "fully_refunded",
                "synthesised": True,
                "reason": "iter246q apply (merchant-approved)",
            },
        })

    # ── Fix #2 — correct refunded_at on existing synth rows ────
    fix2_rows: List[Dict[str, Any]] = []
    relevant_pids = {
        (t.get("provider_id") or "").strip() for t in in_window_txns
        if t.get("provider_id")
    }
    relevant_pids.discard("")
    async for rf in db.payment_refunds.find(
        {"user_id": uid, "provider": "tamara",
         "provider_payment_id": {"$in": list(relevant_pids)},
         "is_pre_accounting": {"$ne": True}},
        {"_id": 0, "raw_payload": 0},
    ):
        pid = rf.get("provider_payment_id")
        local_cap = txn_by_pid.get(pid)
        if not local_cap:
            continue
        onum = local_cap.get("order_number")
        if onum in excludes:
            continue
        # Skip rows whose refunded_at is already correctly inside the
        # window (e.g. 264553438).
        cur_ts = rf.get("refunded_at")
        if _in_window(cur_ts):
            continue
        # Only correct refund rows whose capture's order_number is in
        # Fix #3 (= we know its capture moved to a past week).
        if onum not in fix3_pinned_order_numbers:
            continue
        if str(cur_ts) == str(refunded_at_override):
            continue
        fix2_rows.append({
            "order_number": _safe(onum),
            "provider_refund_id": _safe(rf.get("provider_refund_id")),
            "provider_payment_id": _safe(pid),
            "amount": _r(rf.get("amount") or 0),
            "before_refunded_at": _safe(cur_ts),
            "after_refunded_at": refunded_at_override,
        })

    # ── Same-Week Net-Zero Exclusion ───────────────────────────
    # Tag in-window orders whose capture AND refund both fall inside
    # this invoice window.  The settlement_compute engines should
    # subtract these from BOTH gross and refunds.
    netzero_rows: List[Dict[str, Any]] = []
    if enable_same_week_netzero_exclusion:
        # Build set of pids that will have a refund in window AFTER
        # all fixes apply.
        post_fix_refund_pids_in_window: set[str] = set()
        # 264553438-like rows: existing payment_refunds already in window.
        async for rf in db.payment_refunds.find(
            {"user_id": uid, "provider": "tamara",
             "provider_payment_id": {"$in": list(relevant_pids)},
             "is_pre_accounting": {"$ne": True}},
            {"_id": 0, "provider_payment_id": 1, "refunded_at": 1},
        ):
            if _in_window(rf.get("refunded_at")):
                post_fix_refund_pids_in_window.add(
                    rf.get("provider_payment_id"))
        # Fix #1 newly-created refunds (using override).
        if _in_window(refunded_at_override):
            post_fix_refund_pids_in_window.update(fix1_pids)
        # Fix #2 corrected refund rows.
        if _in_window(refunded_at_override):
            for r in fix2_rows:
                pid = r.get("provider_payment_id")
                if pid:
                    post_fix_refund_pids_in_window.add(pid)

        # For each txn whose capture stays in window AFTER Fix #3, if
        # it also has a refund in window → flag it as net-zero.
        for t in in_window_txns:
            onum = t.get("order_number")
            pid = (t.get("provider_id") or "").strip()
            if onum in fix3_pinned_order_numbers:
                continue  # moved out of window
            if onum in excludes:
                continue
            if pid in post_fix_refund_pids_in_window:
                netzero_rows.append({
                    "order_number": _safe(onum),
                    "provider_id": pid,
                    "amount": _r(t.get("amount") or 0),
                    "before_same_week_netzero_exclusion":
                        bool(t.get("same_week_netzero_exclusion")),
                    "after_same_week_netzero_exclusion": True,
                })

    # ── Simulated post-fix forensic compute ────────────────────
    pinned = fix3_pinned_order_numbers
    netzero_pids = {r["provider_id"] for r in netzero_rows}

    post_gross = 0.0
    post_orders_count = 0
    for t in in_window_txns:
        onum = t.get("order_number")
        pid = (t.get("provider_id") or "").strip()
        if onum in pinned:
            continue
        if onum in excludes:
            # Excluded txns stay in gross because we're NOT touching
            # them in this apply.  But if 265239451 is excluded from
            # Fix #1, its capture remains → still adds to gross.  This
            # is intentional: gross will be +132.92 over Tamara if the
            # caller leaves it un-flagged.
            post_gross += float(t.get("amount") or 0)
            post_orders_count += 1
            continue
        if pid in netzero_pids:
            continue  # net-zero exclusion
        post_gross += float(t.get("amount") or 0)
        post_orders_count += 1

    post_commission = (
        post_gross * commission_rate
        + fixed_fee_per_order * post_orders_count
    )
    post_vat = post_commission * vat_rate

    # Refunds in window AFTER all fixes.  Net-Zero pids are NOT
    # excluded here — Tamara invoice convention keeps same-week
    # refunds in the Refunds column for transparency.
    post_refunds = 0.0
    # Fix #1 new refunds (using override timestamp).
    if _in_window(refunded_at_override):
        for r in fix1_rows:
            post_refunds += float(
                r["payment_refund_row_to_create"]["amount"] or 0)
    # Fix #2 corrected rows.
    if _in_window(refunded_at_override):
        for r in fix2_rows:
            post_refunds += float(r.get("amount") or 0)
    # Existing payment_refunds with refunded_at already in window
    # (e.g. 264553438).
    async for rf in db.payment_refunds.find(
        {"user_id": uid, "provider": "tamara",
         "is_pre_accounting": {"$ne": True}},
        {"_id": 0, "provider_payment_id": 1, "amount": 1,
         "refunded_at": 1},
    ):
        if not _in_window(rf.get("refunded_at")):
            continue
        pid = rf.get("provider_payment_id")
        # Skip if already counted in Fix #2.
        if any(r.get("provider_payment_id") == pid for r in fix2_rows):
            continue
        post_refunds += float(rf.get("amount") or 0)

    post_net_sales = post_gross - post_refunds
    post_net_payable = post_net_sales - post_commission - post_vat

    return {
        "fix1_rows": fix1_rows,
        "fix2_rows": fix2_rows,
        "fix3_rows": fix3_rows,
        "netzero_rows": netzero_rows,
        "simulated_compute": {
            "gross_sales": _r(post_gross),
            "orders_count": post_orders_count,
            "refunds": _r(post_refunds),
            "net_sales": _r(post_net_sales),
            "commission": _r(post_commission),
            "commission_vat": _r(post_vat),
            "net_payable": _r(post_net_payable),
        },
        "rates_in_use": rates,
        "excludes_applied": sorted(excludes),
        "refunded_at_override": refunded_at_override,
        "enable_same_week_netzero_exclusion":
            enable_same_week_netzero_exclusion,
    }


def make_tamara_apply_router(db, current_user):
    router = APIRouter(tags=["audit", "tamara"])

    @router.get("/audit/tamara-apply-dryrun")
    async def tamara_apply_dryrun(
        user: dict = Depends(current_user),
        date_from: str = Query(..., min_length=10, max_length=10),
        date_to: str = Query(..., min_length=10, max_length=10),
        refunded_at_override: str = Query(
            ...,
            description="ISO timestamp used as refunded_at for every "
                        "refund row created/updated by this apply. "
                        "Example: 2026-06-12T20:59:59Z",
        ),
        exclude_order_numbers: str = Query(
            "",
            description="Comma-separated order_numbers to EXCLUDE from "
                        "Fix #1, Fix #2, Fix #3 for this apply.",
        ),
        enable_same_week_netzero_exclusion: bool = Query(True),
    ):
        """Final, decision-baked dry-run.  READ-ONLY."""
        uid = user["id"]
        excludes = [
            x.strip() for x in (exclude_order_numbers or "").split(",")
            if x.strip()
        ]
        plan = await _gather_fix_plan(
            db, uid, date_from, date_to,
            refunded_at_override, excludes,
            enable_same_week_netzero_exclusion,
        )
        token = _confirm_token(
            uid, date_from, date_to, refunded_at_override,
        )
        return {
            "ok": True,
            "iter": "iter246q",
            "endpoint": "apply-dryrun",
            "provider": "tamara",
            "read_only": True,
            "dry_run": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {"from": date_from, "to": date_to},
            "decisions": {
                "refunded_at_override": refunded_at_override,
                "excluded_from_apply": sorted(set(excludes)),
                "enable_same_week_netzero_exclusion":
                    enable_same_week_netzero_exclusion,
            },
            "fix_1_resync": {
                "count": len(plan["fix1_rows"]),
                "total_refund_amount": _r(sum(
                    r["payment_refund_row_to_create"]["amount"]
                    for r in plan["fix1_rows"])),
                "rows": plan["fix1_rows"],
            },
            "fix_2_refunded_at_correction": {
                "count": len(plan["fix2_rows"]),
                "total_refund_amount":
                    _r(sum(r["amount"] for r in plan["fix2_rows"])),
                "rows": plan["fix2_rows"],
            },
            "fix_3_pin_settlement_date": {
                "count": len(plan["fix3_rows"]),
                "total_amount_moved_out_of_window":
                    _r(sum(r["amount"] for r in plan["fix3_rows"])),
                "rows": plan["fix3_rows"],
            },
            "same_week_netzero_exclusion": {
                "count": len(plan["netzero_rows"]),
                "total_amount":
                    _r(sum(r["amount"] for r in plan["netzero_rows"])),
                "rows": plan["netzero_rows"],
            },
            "simulated_forensic_compute": plan["simulated_compute"],
            "rates_in_use": plan["rates_in_use"],
            "confirm_token_to_pass_to_execute": token,
            "execute_endpoint": "POST /api/admin/tamara-apply-execute",
            "notes": [
                "READ-ONLY: zero writes to any collection.",
                "Pass the `confirm_token_to_pass_to_execute` value "
                "into the execute endpoint's `confirm_token` body "
                "field to authorise writes.",
                "The execute endpoint will NEVER touch general_ledger, "
                "NEVER touch balances, NEVER touch Tabby.",
            ],
        }

    @router.post("/admin/tamara-apply-execute")
    async def tamara_apply_execute(
        user: dict = Depends(current_user),
        date_from: str = Query(..., min_length=10, max_length=10),
        date_to: str = Query(..., min_length=10, max_length=10),
        refunded_at_override: str = Query(...),
        exclude_order_numbers: str = Query(""),
        enable_same_week_netzero_exclusion: bool = Query(True),
        confirm_token: str = Query(
            ...,
            description="Must equal the value from the dry-run's "
                        "`confirm_token_to_pass_to_execute`.",
        ),
    ):
        """Apply the merchant-approved fix plan.  WRITES to:
          • payment_transactions  (status, refunded_amount,
            effective_settlement_date, settlement_source,
            same_week_netzero_exclusion flag)
          • payment_refunds      (insert new rows, update refunded_at)
        NEVER writes to general_ledger / bank balances / Tabby.
        Requires a deterministic confirm_token to authorise."""
        uid = user["id"]
        excludes = [
            x.strip() for x in (exclude_order_numbers or "").split(",")
            if x.strip()
        ]
        expected = _confirm_token(
            uid, date_from, date_to, refunded_at_override,
        )
        if confirm_token != expected:
            raise HTTPException(
                status_code=403,
                detail=(
                    "confirm_token mismatch — run "
                    "/api/audit/tamara-apply-dryrun first and copy "
                    "`confirm_token_to_pass_to_execute` exactly."
                ),
            )
        plan = await _gather_fix_plan(
            db, uid, date_from, date_to,
            refunded_at_override, excludes,
            enable_same_week_netzero_exclusion,
        )

        applied = {
            "fix1_inserted_refunds": 0,
            "fix1_updated_txns": 0,
            "fix2_updated_refunds": 0,
            "fix3_repinned_txns": 0,
            "netzero_flagged_txns": 0,
        }
        now_iso = datetime.now(timezone.utc).isoformat()
        audit_id = f"iter246q-apply-{now_iso}"

        # Fix #3 — repin effective_settlement_date.
        for r in plan["fix3_rows"]:
            res = await db.payment_transactions.update_one(
                {"user_id": uid, "provider": "tamara",
                 "provider_id": r["provider_id"]},
                {"$set": {
                    "effective_settlement_date":
                        r["after_effective_settlement_date"],
                    "settlement_source":
                        "settlement_entries_historical",
                    "iter246q_repinned_at": now_iso,
                    "iter246q_audit_id": audit_id,
                }},
            )
            if res.modified_count:
                applied["fix3_repinned_txns"] += 1

        # Fix #1 — update status + insert synthetic refund.
        for r in plan["fix1_rows"]:
            pid = r["provider_id"]
            res = await db.payment_transactions.update_one(
                {"user_id": uid, "provider": "tamara",
                 "provider_id": pid},
                {"$set": {
                    "status": r["after_status"],
                    "refunded_amount": r["after_refunded_amount"],
                    "iter246q_resynced_at": now_iso,
                    "iter246q_audit_id": audit_id,
                }},
            )
            if res.modified_count:
                applied["fix1_updated_txns"] += 1
            # Idempotent insert: skip if synthetic row already exists.
            new_rf = r["payment_refund_row_to_create"]
            exists = await db.payment_refunds.find_one(
                {"user_id": uid, "provider": "tamara",
                 "provider_refund_id": new_rf["provider_refund_id"]},
                {"_id": 1},
            )
            if not exists:
                # Lookup the order_reference for the refund row.
                cap = await db.payment_transactions.find_one(
                    {"user_id": uid, "provider": "tamara",
                     "provider_id": pid},
                    {"_id": 0, "order_reference_id": 1, "currency": 1},
                )
                doc = {
                    "id": f"rf-iter246q-{audit_id}-{pid}",
                    "user_id": uid, "provider": "tamara",
                    "provider_payment_id": pid,
                    "provider_refund_id": new_rf["provider_refund_id"],
                    "order_reference_id":
                        (cap or {}).get("order_reference_id"),
                    "amount": new_rf["amount"],
                    "currency": (cap or {}).get("currency") or "SAR",
                    "status": new_rf["status"],
                    "refunded_at": new_rf["refunded_at"],
                    "synthesised": True,
                    "reason": new_rf["reason"],
                    "iter246q_audit_id": audit_id,
                    "is_pre_accounting": False,
                }
                await db.payment_refunds.insert_one(doc)
                applied["fix1_inserted_refunds"] += 1

        # Fix #2 — correct refunded_at on existing synth rows.
        for r in plan["fix2_rows"]:
            res = await db.payment_refunds.update_one(
                {"user_id": uid, "provider": "tamara",
                 "provider_refund_id": r["provider_refund_id"]},
                {"$set": {
                    "refunded_at": r["after_refunded_at"],
                    "iter246q_corrected_at": now_iso,
                    "iter246q_audit_id": audit_id,
                }},
            )
            if res.modified_count:
                applied["fix2_updated_refunds"] += 1

        # Same-Week Net-Zero Exclusion — flag txns.
        if enable_same_week_netzero_exclusion:
            for r in plan["netzero_rows"]:
                res = await db.payment_transactions.update_one(
                    {"user_id": uid, "provider": "tamara",
                     "provider_id": r["provider_id"]},
                    {"$set": {
                        "same_week_netzero_exclusion": True,
                        "iter246q_netzero_at": now_iso,
                        "iter246q_audit_id": audit_id,
                    }},
                )
                if res.modified_count:
                    applied["netzero_flagged_txns"] += 1

        return {
            "ok": True,
            "iter": "iter246q",
            "endpoint": "apply-execute",
            "applied": applied,
            "audit_id": audit_id,
            "applied_at": now_iso,
            "period": {"from": date_from, "to": date_to},
            "decisions": {
                "refunded_at_override": refunded_at_override,
                "excluded_from_apply": sorted(set(excludes)),
                "enable_same_week_netzero_exclusion":
                    enable_same_week_netzero_exclusion,
            },
            "guards_enforced": [
                "No writes to general_ledger.",
                "No writes to bank_accounts / account_balance_ssot.",
                "No writes to Tabby anything.",
                "Idempotent: re-running with the same audit_id "
                "produces zero new inserts.",
            ],
        }

    return router
