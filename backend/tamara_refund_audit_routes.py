"""Iter-246o — Tamara refund-sync & old-capture forensic (READ-ONLY).

Two diagnostic tracks, single endpoint, ZERO writes:

  Track A — Missing refund rows:
    For every payment_transaction with status in {fully_refunded,
    partially_refunded, refunded} inside the requested window, report
    whether a matching payment_refunds row exists (by
    provider_payment_id OR `synthetic:<pid>`).  Surface
    `refunded_amount`, `updated_at_provider`, `created_at_provider`
    and any cached refunds from `raw_payload`.

  Track B — Targeted old-capture inspection:
    For each `order_numbers` passed in, dump the FULL local picture
    (payment_transactions doc, payment_refunds rows, every
    settlement_entries row across ALL periods).  Optionally probe
    Tamara API for the live order to compare statuses & settlement
    metadata.

STRICTLY READ-ONLY.  Never writes.  Never adjusts historical data.
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


def make_tamara_refund_audit_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "tamara"])

    @router.get("/tamara-refund-and-old-capture-forensic")
    async def tamara_refund_old_capture_forensic(
        user: dict = Depends(current_user),
        date_from: str = Query(..., min_length=10, max_length=10),
        date_to: str = Query(..., min_length=10, max_length=10),
        # Comma-separated order numbers for Track B.
        order_numbers: str = Query(
            "",
            description="Comma-separated order_numbers to inspect deeply",
        ),
        probe_tamara_api: bool = Query(
            False,
            description="Hit Tamara API for live order details (slow).",
        ),
    ):
        """READ-ONLY. Returns Track A (refund row coverage) +
        Track B (targeted old-capture deep inspection)."""
        uid = user["id"]
        from bnpl.settlements_service import _local_date_window_utc
        utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)

        # ── Track A ────────────────────────────────────────────
        sales_match: Dict[str, Any] = {
            "user_id": uid, "provider": "tamara",
            "effective_settlement_date": {
                **({"$gte": utc_gte} if utc_gte else {}),
                **({"$lte": utc_lte} if utc_lte else {}),
            },
            "status": {"$in": ["fully_refunded", "partially_refunded",
                               "refunded"]},
            "is_pre_accounting": {"$ne": True},
        }

        track_a_rows: List[Dict[str, Any]] = []
        track_a_with_refund = 0
        track_a_without_refund = 0
        sum_missing_refunded_amount = 0.0
        sum_present_refunded_amount = 0.0

        async for t in db.payment_transactions.find(
            sales_match,
            {"_id": 0, "order_number": 1, "order_reference_id": 1,
             "provider_id": 1, "amount": 1, "status": 1,
             "refunded_amount": 1, "captured_amount": 1, "currency": 1,
             "created_at_provider": 1, "updated_at_provider": 1,
             "effective_settlement_date": 1, "billing_eligible_at": 1,
             "raw_payload": 1},
        ).sort([("effective_settlement_date", 1)]):
            pid = (t.get("provider_id") or "").strip()
            ref = (t.get("order_reference_id") or "").strip()
            synth = f"synthetic:{pid}" if pid else None
            existing = None
            if pid:
                existing = await db.payment_refunds.find_one(
                    {"user_id": uid, "provider": "tamara",
                     "$or": [
                         {"provider_payment_id": pid},
                         *([{"provider_refund_id": synth}] if synth else []),
                     ]},
                    {"_id": 0, "id": 1, "amount": 1, "refunded_at": 1,
                     "provider_refund_id": 1, "synthesised": 1,
                     "reason": 1, "status": 1},
                )

            # Pull cached refund hints from raw_payload (Tamara order
            # endpoint payload).  Safe-extract — we don't trust shape.
            raw = t.get("raw_payload") or {}
            total_refunded_amount = None
            tamara_refunds_arr_len = 0
            tamara_refunded_at = None
            if isinstance(raw, dict):
                tra = (raw.get("total_refunded_amount")
                       or raw.get("refunded_amount"))
                if isinstance(tra, dict):
                    total_refunded_amount = _r(tra.get("amount"))
                elif isinstance(tra, (int, float, str)):
                    try:
                        total_refunded_amount = _r(float(tra))
                    except (TypeError, ValueError):
                        total_refunded_amount = None
                tamara_refunds_arr_len = len(raw.get("refunds") or [])
                if tamara_refunds_arr_len:
                    first = (raw.get("refunds") or [{}])[0] or {}
                    tamara_refunded_at = (
                        first.get("created_at") or first.get("refunded_at"))

            ref_amt_local = _r(t.get("refunded_amount") or 0)
            row = {
                "order_number": _safe(t.get("order_number")),
                "order_reference_id": _safe(ref),
                "provider_id": _safe(pid),
                "amount": _r(t.get("amount") or 0),
                "captured_amount": _r(t.get("captured_amount") or 0),
                "refunded_amount_local_field": ref_amt_local,
                "tamara_total_refunded_amount": total_refunded_amount,
                "tamara_refunds_array_len": tamara_refunds_arr_len,
                "tamara_refunded_at": _safe(tamara_refunded_at),
                "status": t.get("status"),
                "currency": t.get("currency") or "SAR",
                "created_at_provider": _safe(t.get("created_at_provider")),
                "updated_at_provider": _safe(t.get("updated_at_provider")),
                "effective_settlement_date":
                    _safe(t.get("effective_settlement_date")),
                "billing_eligible_at": _safe(t.get("billing_eligible_at")),
                "has_payment_refund": bool(existing),
                "payment_refund_row": existing or None,
            }
            if existing:
                track_a_with_refund += 1
                sum_present_refunded_amount += ref_amt_local
            else:
                track_a_without_refund += 1
                sum_missing_refunded_amount += ref_amt_local
            track_a_rows.append(row)

        # ── Track B ────────────────────────────────────────────
        target_numbers = [
            x.strip() for x in (order_numbers or "").split(",")
            if x.strip()
        ]
        track_b_rows: List[Dict[str, Any]] = []
        tamara_probe_errors: List[str] = []
        client = None
        if probe_tamara_api and target_numbers:
            try:
                from bnpl.clients.tamara import TamaraClient, TamaraError
                from bnpl.config_store import DEFAULTS, get_raw_secrets
                secrets = await get_raw_secrets(db, uid, "tamara")
                if secrets.get("api_token"):
                    client = TamaraClient(
                        api_token=secrets["api_token"],
                        base_url=(secrets.get("api_base_url")
                                  or DEFAULTS["tamara"]["api_base_url"]),
                    )
                else:
                    tamara_probe_errors.append(
                        "Tamara api_token not set — skipping live probe.")
            except Exception as exc:  # noqa: BLE001
                tamara_probe_errors.append(
                    f"failed to init Tamara client: {type(exc).__name__}: {exc}")
                client = None

        for onum in target_numbers:
            txn = await db.payment_transactions.find_one(
                {"user_id": uid, "provider": "tamara",
                 "$or": [
                     {"order_number": onum},
                     {"order_reference_id": onum},
                 ]},
                {"_id": 0, "raw_payload": 0},
            )
            refunds_local = await db.payment_refunds.find(
                {"user_id": uid, "provider": "tamara",
                 "$or": [
                     {"order_reference_id": onum},
                     *([{"provider_payment_id": (txn or {}).get("provider_id")}]
                       if (txn or {}).get("provider_id") else []),
                 ]},
                {"_id": 0},
            ).to_list(50)

            # All settlement_entries across ALL periods for this order.
            settlement_rows = await db.settlement_entries.find(
                {"user_id": uid, "provider": "tamara",
                 "order_number": onum},
                {"_id": 0, "order_number": 1, "event_type": 1,
                 "settlement_date": 1, "actual_gross_amount": 1,
                 "actual_payment_fee": 1, "actual_payment_vat": 1,
                 "actual_net_amount": 1, "actual_refund_amount": 1,
                 "actual_partial_refund_amount": 1, "currency": 1,
                 "file_hash": 1, "created_at": 1},
            ).sort([("settlement_date", 1)]).to_list(100)

            # ── Live probe (optional) ────────────────────────
            live = None
            live_error = None
            if client is not None and txn is not None:
                pid = (txn.get("provider_id") or "").strip()
                ref = (txn.get("order_reference_id") or "").strip()
                try:
                    from bnpl.clients.tamara import TamaraError
                    raw = None
                    if pid:
                        raw = await client.get_order_by_id(pid)
                    elif ref:
                        raw = await client.get_order_by_reference(ref)
                    if isinstance(raw, dict):
                        # Extract just the fields useful for diagnosis.
                        live = {
                            "status": raw.get("status"),
                            "total_amount": raw.get("total_amount"),
                            "total_refunded_amount":
                                raw.get("total_refunded_amount"),
                            "refunded_amount": raw.get("refunded_amount"),
                            "settlement_id": raw.get("settlement_id"),
                            "settlement": raw.get("settlement"),
                            "settled_at": raw.get("settled_at"),
                            "captures_count": len(raw.get("captures") or []),
                            "refunds_array_len":
                                len(raw.get("refunds") or []),
                            "refund_orders_array_len":
                                len(raw.get("refund_orders") or []),
                            "updated_at": raw.get("updated_at"),
                            "created_at": raw.get("created_at"),
                            "top_level_keys": sorted(raw.keys()),
                        }
                        # Surface settlement-date hints in captures.
                        cap_settled_dates = []
                        for c in (raw.get("captures") or []):
                            if isinstance(c, dict):
                                cap_settled_dates.append({
                                    "capture_id": c.get("capture_id"),
                                    "settled_at": c.get("settled_at"),
                                    "created_at": c.get("created_at"),
                                    "settlement_id": c.get("settlement_id"),
                                })
                        live["captures_settlement_hints"] = cap_settled_dates
                except TamaraError as exc:
                    live_error = f"TamaraError {exc.status}: {exc.detail[:200]}"
                except Exception as exc:  # noqa: BLE001
                    live_error = f"{type(exc).__name__}: {str(exc)[:200]}"

            row = {
                "order_number_query": onum,
                "found_in_payment_transactions": bool(txn),
                "payment_transaction": (
                    {k: v for k, v in (txn or {}).items()
                     if k != "raw_payload"} if txn else None
                ),
                "payment_refunds_count": len(refunds_local),
                "payment_refunds": refunds_local,
                "settlement_entries_count": len(settlement_rows),
                "settlement_entries_all_periods": settlement_rows,
                "settlement_dates_observed": sorted({
                    s.get("settlement_date") for s in settlement_rows
                    if s.get("settlement_date")
                }),
                "tamara_live_probe": live,
                "tamara_live_probe_error": live_error,
            }
            track_b_rows.append(row)

        # ── Summary ────────────────────────────────────────────
        return {
            "ok": True,
            "iter": "iter246o",
            "provider": "tamara",
            "read_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {
                "from": date_from, "to": date_to,
                "utc_window": {"gte": utc_gte, "lte": utc_lte},
            },
            "track_a_missing_refunds": {
                "total_refunded_status_orders_in_window":
                    len(track_a_rows),
                "with_existing_payment_refund": track_a_with_refund,
                "without_payment_refund": track_a_without_refund,
                "sum_refunded_amount_present":
                    _r(sum_present_refunded_amount),
                "sum_refunded_amount_missing":
                    _r(sum_missing_refunded_amount),
                "rows": track_a_rows,
            },
            "track_b_targeted_orders": {
                "queried_order_numbers": target_numbers,
                "probe_tamara_api": probe_tamara_api,
                "tamara_probe_errors": tamara_probe_errors,
                "rows": track_b_rows,
            },
            "notes": [
                "READ-ONLY: this endpoint does not write to any "
                "collection.",
                "Track A inspects payment_transactions whose `status` "
                "is refunded but checks if a corresponding "
                "payment_refunds row exists (by provider_payment_id or "
                "the 'synthetic:<pid>' fallback id).",
                "Track B dumps EVERY settlement_entries row for the "
                "queried order_numbers across ALL periods — so the "
                "merchant can see if Tamara already included them in a "
                "previous weekly invoice.",
                "When probe_tamara_api=true, the endpoint hits the "
                "live Tamara Merchant API per order — useful to read "
                "the authoritative status, total_refunded_amount and "
                "settlement_id directly from Tamara.",
            ],
        }

    return router
