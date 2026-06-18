"""Iter-246p — Tamara Fix Plan Dry-Run (READ-ONLY).

Previews ALL four proposed fixes WITHOUT writing anything:

  Fix #1: Re-sync candidate orders whose local `status` is
          out-of-date vs Tamara's live API.
  Fix #2: For every payment_refund row whose `refunded_at` matches
          its capture-date (i.e. synthesised fallback), propose the
          true refund timestamp extracted from Tamara live data.
  Fix #3: For every payment_transaction whose `effective_settlement_date`
          has been drifted forward by Tamara's API while a historical
          `settlement_entries.settlement_date` exists EARLIER for the
          same order_number → propose pinning the attribution back to
          the earliest historical settlement_date.
  Fix #4: Simulates the post-fix forensic compute for the requested
          window so the merchant can see the expected gross / refunds
          / commission / VAT / net AFTER all fixes are applied.

ZERO writes.  Calling this endpoint never mutates Mongo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


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


def _extract_refund_timestamp_from_live(raw: Dict[str, Any]) -> Optional[str]:
    """Best-effort extraction of an actual refund timestamp from a
    Tamara live order payload.  Priority:

      1) transactions[] entry of type 'refund' / 'partial_refund'
         ordered by created_at desc → first.created_at.
      2) refunds[] / refund_orders[] first.created_at.
      3) top-level updated_at.
      4) top-level settlement_date.
    """
    if not isinstance(raw, dict):
        return None
    # 1) transactions[]
    txns = raw.get("transactions") or []
    refund_txns = []
    if isinstance(txns, list):
        for t in txns:
            if not isinstance(t, dict):
                continue
            t_type = str(t.get("type") or t.get("transaction_type") or "").lower()
            if "refund" in t_type:
                refund_txns.append(t)
    refund_txns.sort(
        key=lambda x: str(x.get("created_at") or ""), reverse=True,
    )
    if refund_txns:
        ts = refund_txns[0].get("created_at") or refund_txns[0].get("date")
        if ts:
            return str(ts)
    # 2) refunds[] / refund_orders[]
    for key in ("refunds", "refund_orders"):
        arr = raw.get(key) or []
        if isinstance(arr, list) and arr:
            first = arr[0]
            if isinstance(first, dict):
                ts = (first.get("created_at") or first.get("refunded_at")
                      or first.get("date"))
                if ts:
                    return str(ts)
    # 3) updated_at / 4) settlement_date
    return _safe(raw.get("updated_at") or raw.get("settlement_date"))


def make_tamara_fix_plan_dryrun_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "tamara"])

    @router.get("/tamara-fix-plan-dryrun")
    async def tamara_fix_plan_dryrun(
        user: dict = Depends(current_user),
        date_from: str = Query(..., min_length=10, max_length=10),
        date_to: str = Query(..., min_length=10, max_length=10),
        # Optional explicit candidates for Fix #1 (5 orders the user
        # already identified).  If omitted, the endpoint auto-detects
        # by comparing Tamara live status with local status.
        explicit_resync_candidates: str = Query(
            "",
            description="Comma-separated order_numbers to force-include "
                        "in Fix #1 scan.",
        ),
        probe_tamara_api: bool = Query(
            True,
            description="Hit Tamara API to compute the live-state diff. "
                        "Required for Fix #1/#2 accuracy.",
        ),
        # Iter-246p2 — raw payload dump for surgical inspection.
        dump_raw_for_order_numbers: str = Query(
            "",
            description="Comma-separated order_numbers whose FULL raw "
                        "Tamara payload (transactions/refunds/dates) "
                        "should be included for forensic inspection.",
        ),
    ):
        """READ-ONLY dry-run for the 4-part Tamara reconciliation
        fix plan.  Returns a structured diff per fix and a simulated
        post-fix forensic compute."""
        uid = user["id"]
        from bnpl.settlements_service import (
            _local_date_window_utc, _merchant_fee_rates,
        )
        utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)
        rates = await _merchant_fee_rates(db, uid, "tamara")
        commission_rate = float(rates.get("commission_pct") or 0) / 100.0
        vat_rate = float(rates.get("vat_pct") or 0) / 100.0
        fixed_fee_per_order = float(rates.get("fixed_fee_per_order") or 0)

        # Init Tamara client if probing.
        client = None
        client_init_error: Optional[str] = None
        if probe_tamara_api:
            try:
                from bnpl.clients.tamara import TamaraClient
                from bnpl.config_store import DEFAULTS, get_raw_secrets
                secrets = await get_raw_secrets(db, uid, "tamara")
                if not secrets.get("api_token"):
                    client_init_error = "Tamara api_token not set"
                else:
                    client = TamaraClient(
                        api_token=secrets["api_token"],
                        base_url=(secrets.get("api_base_url")
                                  or DEFAULTS["tamara"]["api_base_url"]),
                    )
            except Exception as exc:  # noqa: BLE001
                client_init_error = (
                    f"{type(exc).__name__}: {str(exc)[:200]}")
                client = None

        # ── Discover all candidates in the window ──────────────
        # Every payment_transaction with effective_settlement_date in
        # the window, plus explicit-resync targets.
        in_window_match: Dict[str, Any] = {
            "user_id": uid, "provider": "tamara",
            "effective_settlement_date": {
                **({"$gte": utc_gte} if utc_gte else {}),
                **({"$lte": utc_lte} if utc_lte else {}),
            },
            "is_pre_accounting": {"$ne": True},
        }
        in_window_txns: List[Dict[str, Any]] = (
            await db.payment_transactions.find(
                in_window_match,
                {"_id": 0, "raw_payload": 0},
            ).to_list(5000)
        )

        # Bring in explicit candidates that may NOT be in the window
        # (e.g. user wants to inspect a specific order).
        explicit = [
            x.strip() for x in (explicit_resync_candidates or "").split(",")
            if x.strip()
        ]
        if explicit:
            extra = await db.payment_transactions.find(
                {"user_id": uid, "provider": "tamara",
                 "order_number": {"$in": explicit}},
                {"_id": 0, "raw_payload": 0},
            ).to_list(500)
            existing_keys = {
                t.get("order_number") for t in in_window_txns
            }
            for e in extra:
                if e.get("order_number") not in existing_keys:
                    in_window_txns.append(e)

        # ── Fix #1 — Resync candidates ─────────────────────────
        fix1_rows: List[Dict[str, Any]] = []
        fix1_summary = {
            "candidates_scanned": 0,
            "would_update_count": 0,
            "would_update_refunded_amount_total": 0.0,
            "skipped_no_live_data": 0,
            "skipped_status_already_matches": 0,
        }

        for t in in_window_txns:
            fix1_summary["candidates_scanned"] += 1
            onum = t.get("order_number")
            pid = (t.get("provider_id") or "").strip()
            local_status = t.get("status")
            local_refunded = _r(t.get("refunded_amount") or 0)
            live = None
            live_error = None
            if client is not None and pid:
                try:
                    from bnpl.clients.tamara import TamaraError
                    live = await client.get_order_by_id(pid)
                except TamaraError as exc:
                    live_error = (
                        f"TamaraError {exc.status}: {exc.detail[:200]}")
                except Exception as exc:  # noqa: BLE001
                    live_error = (
                        f"{type(exc).__name__}: {str(exc)[:200]}")
            if not isinstance(live, dict):
                fix1_summary["skipped_no_live_data"] += 1
                continue

            live_status = live.get("status")
            live_refunded = _extract_amount(live.get("refunded_amount"))
            live_captured = _extract_amount(live.get("captured_amount"))
            live_total = _extract_amount(live.get("total_amount"))

            needs_update = (
                (live_status and live_status != local_status)
                or (abs(live_refunded - local_refunded) >= 0.01)
            )
            if not needs_update:
                fix1_summary["skipped_status_already_matches"] += 1
                continue

            proposed_refunded_at = _extract_refund_timestamp_from_live(live)
            delta_refunded = _r(live_refunded - local_refunded)
            fix1_summary["would_update_count"] += 1
            fix1_summary["would_update_refunded_amount_total"] += delta_refunded

            fix1_rows.append({
                "order_number": _safe(onum),
                "provider_id": _safe(pid),
                "amount": _r(t.get("amount") or 0),
                "before": {
                    "status": local_status,
                    "refunded_amount": local_refunded,
                    "captured_amount": _r(t.get("captured_amount") or 0),
                    "updated_at_provider": _safe(t.get("updated_at_provider")),
                },
                "after_proposed": {
                    "status": live_status,
                    "refunded_amount": _r(live_refunded),
                    "captured_amount": _r(live_captured),
                    "total_amount": _r(live_total),
                    "updated_at_provider_proposed":
                        _safe(live.get("updated_at")),
                },
                "would_create_or_update_payment_refund": {
                    "provider_refund_id_synthetic":
                        f"synthetic:{pid}" if pid else None,
                    "proposed_amount": _r(live_refunded),
                    "proposed_refunded_at": proposed_refunded_at,
                    "proposed_status": live_status,
                },
                "live_error": live_error,
            })
        fix1_summary["would_update_refunded_amount_total"] = _r(
            fix1_summary["would_update_refunded_amount_total"])

        # ── Fix #2 — refunded_at corrections ───────────────────
        # Every existing payment_refunds row whose `refunded_at`
        # equals the original capture's `created_at_provider` is a
        # synthesised-fallback we need to repoint to the real refund
        # timestamp from Tamara live data.
        fix2_rows: List[Dict[str, Any]] = []
        fix2_summary = {
            "candidates_scanned": 0,
            "would_correct_count": 0,
            "would_keep_count": 0,
            "skipped_no_live_data": 0,
        }

        # Touch only refund rows linked to the txns we care about
        # (in-window + explicit) to keep API calls bounded.
        relevant_pids = {
            (t.get("provider_id") or "").strip() for t in in_window_txns
            if t.get("provider_id")
        }
        relevant_pids.discard("")
        if relevant_pids:
            async for rf in db.payment_refunds.find(
                {"user_id": uid, "provider": "tamara",
                 "provider_payment_id": {"$in": list(relevant_pids)},
                 "is_pre_accounting": {"$ne": True}},
                {"_id": 0, "raw_payload": 0},
            ):
                fix2_summary["candidates_scanned"] += 1
                pid = rf.get("provider_payment_id")
                cur_ts = rf.get("refunded_at")

                # Find the matching local capture to know its
                # created_at_provider — if refunded_at equals that, the
                # row is a synthesised fallback.
                local_cap = next(
                    (t for t in in_window_txns
                     if (t.get("provider_id") or "") == pid),
                    None,
                )
                capture_ts = (local_cap or {}).get("created_at_provider")
                is_synth_fallback = (
                    bool(rf.get("synthesised"))
                    or (cur_ts and capture_ts and str(cur_ts) == str(capture_ts))
                )

                live = None
                live_error = None
                if client is not None and pid and is_synth_fallback:
                    try:
                        from bnpl.clients.tamara import TamaraError
                        live = await client.get_order_by_id(pid)
                    except TamaraError as exc:
                        live_error = (
                            f"TamaraError {exc.status}: {exc.detail[:200]}")
                    except Exception as exc:  # noqa: BLE001
                        live_error = (
                            f"{type(exc).__name__}: {str(exc)[:200]}")

                proposed_ts = None
                if isinstance(live, dict):
                    proposed_ts = _extract_refund_timestamp_from_live(live)

                if not is_synth_fallback:
                    fix2_summary["would_keep_count"] += 1
                    continue

                if not proposed_ts:
                    fix2_summary["skipped_no_live_data"] += 1
                    continue

                if str(proposed_ts) == str(cur_ts):
                    fix2_summary["would_keep_count"] += 1
                    continue

                fix2_summary["would_correct_count"] += 1
                fix2_rows.append({
                    "order_number":
                        _safe((local_cap or {}).get("order_number")),
                    "provider_refund_id":
                        _safe(rf.get("provider_refund_id")),
                    "provider_payment_id": _safe(pid),
                    "amount": _r(rf.get("amount") or 0),
                    "before": {
                        "refunded_at": _safe(cur_ts),
                        "synthesised": bool(rf.get("synthesised")),
                        "reason": rf.get("reason"),
                    },
                    "after_proposed": {
                        "refunded_at": _safe(proposed_ts),
                    },
                    "live_error": live_error,
                })

        # ── Fix #3 — Lock effective_settlement_date ────────────
        # For each in-window txn, look up the EARLIEST historical
        # settlement_entries.settlement_date for the same order_number.
        # If that date is strictly < current effective_settlement_date,
        # propose moving attribution back to the historical date.
        fix3_rows: List[Dict[str, Any]] = []
        fix3_summary = {
            "candidates_scanned": 0,
            "would_pin_count": 0,
            "would_keep_count": 0,
            "no_historical_entry_count": 0,
            "gross_amount_moved_out_of_window": 0.0,
        }
        for t in in_window_txns:
            fix3_summary["candidates_scanned"] += 1
            onum = t.get("order_number")
            cur_esd = _safe(t.get("effective_settlement_date"))
            cur_esd_date = (cur_esd or "")[:10]
            earliest = None
            async for s in db.settlement_entries.find(
                {"user_id": uid, "provider": "tamara",
                 "order_number": onum,
                 "event_type": {"$ne": "refund"},
                 "is_pre_accounting": {"$ne": True}},
                {"_id": 0, "settlement_date": 1, "event_type": 1,
                 "file_hash": 1, "actual_gross_amount": 1},
            ).sort([("settlement_date", 1)]).limit(1):
                earliest = s
            if not earliest:
                fix3_summary["no_historical_entry_count"] += 1
                continue
            hist_date = str(earliest.get("settlement_date") or "")[:10]
            if not hist_date:
                fix3_summary["no_historical_entry_count"] += 1
                continue
            if hist_date >= cur_esd_date:
                fix3_summary["would_keep_count"] += 1
                continue

            amt = _r(t.get("amount") or 0)
            fix3_summary["would_pin_count"] += 1
            fix3_summary["gross_amount_moved_out_of_window"] += amt
            fix3_rows.append({
                "order_number": _safe(onum),
                "provider_id": _safe(t.get("provider_id")),
                "amount": amt,
                "before": {
                    "effective_settlement_date": cur_esd,
                    "settlement_source": t.get("settlement_source"),
                    "provider_settlement_id": t.get("provider_settlement_id"),
                    "provider_settlement_date":
                        _safe(t.get("provider_settlement_date")),
                },
                "after_proposed": {
                    "effective_settlement_date": hist_date,
                    "settlement_source": "settlement_entries_historical",
                    "source_file_hash": earliest.get("file_hash"),
                },
                "moves_out_of_current_window": True,
            })
        fix3_summary["gross_amount_moved_out_of_window"] = _r(
            fix3_summary["gross_amount_moved_out_of_window"])

        # ── Simulated post-fix forensic compute ────────────────
        # Compute what gross/refunds/commission would be AFTER fixes
        # #1 + #3 are applied to the window.  Refunds in this window =
        # current synthesised refunds whose corrected refunded_at would
        # fall in window + Fix #1 new refunds whose proposed
        # refunded_at falls in window.
        pinned_pids = {r["order_number"] for r in fix3_rows}
        post_gross = 0.0
        post_orders_count = 0
        for t in in_window_txns:
            if t.get("order_number") in pinned_pids:
                continue  # moved out of window by Fix #3
            post_gross += float(t.get("amount") or 0)
            post_orders_count += 1
        post_commission = (
            post_gross * commission_rate
            + fixed_fee_per_order * post_orders_count
        )
        post_vat = post_commission * vat_rate

        # Refunds: take all refunds whose proposed refunded_at falls in
        # the window (Fix #2) plus Fix #1 newly synthesised.
        from_window_lo = (utc_gte or "")[:10]
        from_window_hi = (utc_lte or "")[:10]

        def _in_window(d: Optional[str]) -> bool:
            if not d:
                return False
            return from_window_lo <= str(d)[:10] <= from_window_hi

        post_refunds = 0.0
        # Fix #2 corrected ones falling in window.
        for r in fix2_rows:
            if _in_window(r["after_proposed"]["refunded_at"]):
                post_refunds += float(r.get("amount") or 0)
        # Already-correct refunds in payment_refunds with refunded_at
        # naturally in window.
        async for rf in db.payment_refunds.find(
            {"user_id": uid, "provider": "tamara",
             "is_pre_accounting": {"$ne": True}},
            {"_id": 0, "provider_payment_id": 1, "amount": 1,
             "refunded_at": 1, "synthesised": 1},
        ):
            # Skip ones we already counted via fix2 to avoid duplicates.
            pid = rf.get("provider_payment_id")
            if any(r.get("provider_payment_id") == pid for r in fix2_rows):
                continue
            if _in_window(rf.get("refunded_at")):
                post_refunds += float(rf.get("amount") or 0)
        # Fix #1 newly proposed refunds.
        for r in fix1_rows:
            blob = r.get("would_create_or_update_payment_refund") or {}
            if _in_window(blob.get("proposed_refunded_at")):
                post_refunds += float(blob.get("proposed_amount") or 0)

        post_net_sales = post_gross - post_refunds
        post_net_payable = (
            post_net_sales - post_commission - post_vat
        )

        # ── Raw payload dump (read-only inspection) ────────────
        # For each requested order_number, hit Tamara API and return
        # the FULL raw transactions[]/refunds[]/refund_orders[] arrays
        # plus every date-looking top-level field so the merchant can
        # locate the refund timestamp under a non-standard field name.
        DATE_FIELDS = (
            "created_at", "updated_at", "captured_at", "refunded_at",
            "processed_at", "settled_at", "transaction_date",
            "event_date", "completed_at", "authorized_at",
            "cancelled_at", "expired_at", "delivered_at",
        )

        raw_dump_requested = [
            x.strip() for x in (dump_raw_for_order_numbers or "").split(",")
            if x.strip()
        ]
        raw_dump_rows: List[Dict[str, Any]] = []
        for onum in raw_dump_requested:
            txn_doc = await db.payment_transactions.find_one(
                {"user_id": uid, "provider": "tamara",
                 "$or": [
                     {"order_number": onum},
                     {"order_reference_id": onum},
                 ]},
                {"_id": 0, "provider_id": 1, "order_number": 1,
                 "order_reference_id": 1, "status": 1, "amount": 1},
            )
            if not txn_doc:
                raw_dump_rows.append({
                    "order_number_query": onum,
                    "error": "not_found_in_payment_transactions",
                })
                continue
            pid = (txn_doc.get("provider_id") or "").strip()
            live_raw = None
            live_error = None
            if client is not None and pid:
                try:
                    from bnpl.clients.tamara import TamaraError
                    live_raw = await client.get_order_by_id(pid)
                except TamaraError as exc:
                    live_error = (
                        f"TamaraError {exc.status}: {exc.detail[:200]}")
                except Exception as exc:  # noqa: BLE001
                    live_error = (
                        f"{type(exc).__name__}: {str(exc)[:200]}")
            if not isinstance(live_raw, dict):
                raw_dump_rows.append({
                    "order_number_query": onum,
                    "local_status": txn_doc.get("status"),
                    "provider_id": pid,
                    "live_error": live_error,
                    "raw_dump": None,
                })
                continue

            # Extract every date-looking top-level field as-is.
            top_level_dates: Dict[str, Any] = {}
            for k in DATE_FIELDS:
                if k in live_raw:
                    top_level_dates[k] = live_raw.get(k)

            # Surface the FULL transactions/refunds/refund_orders so
            # we can spot any hidden refund timestamp.
            raw_dump_rows.append({
                "order_number_query": onum,
                "provider_id": pid,
                "local_status": txn_doc.get("status"),
                "local_amount": _r(txn_doc.get("amount") or 0),
                "live_status": live_raw.get("status"),
                "live_refunded_amount": live_raw.get("refunded_amount"),
                "live_captured_amount": live_raw.get("captured_amount"),
                "live_settlement_date": live_raw.get("settlement_date"),
                "live_settlement_status":
                    live_raw.get("settlement_status"),
                "top_level_date_fields": top_level_dates,
                "top_level_keys_present": sorted(live_raw.keys()),
                "raw_transactions": live_raw.get("transactions"),
                "raw_refunds": live_raw.get("refunds"),
                "raw_refund_orders": live_raw.get("refund_orders"),
                "raw_processing": live_raw.get("processing"),
                "live_error": live_error,
            })

        # ── Final payload ──────────────────────────────────────
        return {
            "ok": True,
            "iter": "iter246p",
            "provider": "tamara",
            "read_only": True,
            "dry_run": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {
                "from": date_from, "to": date_to,
                "utc_window": {"gte": utc_gte, "lte": utc_lte},
            },
            "rates_in_use": rates,
            "tamara_client": {
                "probe_tamara_api": probe_tamara_api,
                "init_error": client_init_error,
            },
            "fix_1_resync_status_and_refunded_amount": {
                "summary": fix1_summary,
                "rows": fix1_rows,
            },
            "fix_2_correct_refunded_at": {
                "summary": fix2_summary,
                "rows": fix2_rows,
            },
            "fix_3_lock_effective_settlement_date": {
                "summary": fix3_summary,
                "rows": fix3_rows,
                "rule": (
                    "If an order has a settlement_entries.settlement_date "
                    "EARLIER than its current effective_settlement_date, "
                    "pin attribution to the earliest historical "
                    "settlement_date.  Forward-only after that — "
                    "Tamara API drift can never move it again."
                ),
            },
            "fix_4_hardening": {
                "planned": [
                    "Add a cron that polls Tamara API daily for any "
                    "payment_transactions whose status is non-terminal "
                    "in the last 30 days, refreshing local status + "
                    "refunds.",
                    "Apply lock-rule from Fix #3 inside the existing "
                    "attribution engine so any future settlement_entries "
                    "upload immediately becomes the authoritative date.",
                    "Add pytest coverage that simulates the exact bug "
                    "(API moves settlement_date forward after a refund) "
                    "and verifies the attribution engine NO LONGER moves "
                    "the order forward.",
                ],
            },
            "post_fix_simulated_forensic_compute": {
                "gross_sales": _r(post_gross),
                "orders_count": post_orders_count,
                "refunds": _r(post_refunds),
                "net_sales": _r(post_net_sales),
                "commission": _r(post_commission),
                "commission_vat": _r(post_vat),
                "net_payable": _r(post_net_payable),
            },
            "raw_tamara_payloads_dump": {
                "queried_order_numbers": raw_dump_requested,
                "rows": raw_dump_rows,
                "purpose": (
                    "READ-ONLY inspection of Tamara API raw payload "
                    "fields for the queried orders. Use to locate any "
                    "non-standard refund timestamp field (e.g. inside "
                    "transactions[] or refunds[]) before deciding the "
                    "refunded_at policy for Fix #1/#2."
                ),
            },
            "notes": [
                "READ-ONLY: this endpoint does not write to ANY "
                "collection.  Calling it leaves Mongo untouched.",
                "Fix #1 candidates are detected by comparing live "
                "Tamara `status` and `refunded_amount` against local.",
                "Fix #2 candidates are payment_refunds rows whose "
                "`refunded_at` equals the capture timestamp (the "
                "synthesised fallback) — we read the true refund "
                "timestamp from Tamara `transactions[]` or `refunds[]`.",
                "Fix #3 candidates are any in-window txn whose "
                "settlement_entries history has an EARLIER "
                "settlement_date — these were already settled by "
                "Tamara in a previous week and must stay there.",
                "Fix #4 is forward-looking hardening; the dry-run "
                "lists the planned guardrails but does not implement "
                "them.",
                "The simulated forensic compute below applies Fix #1 "
                "+ Fix #2 + Fix #3 mentally and shows the expected "
                "totals.  Compare against the merchant's official "
                "Tamara invoice numbers.",
            ],
        }

    return router
