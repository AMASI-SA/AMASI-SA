"""Iter-246m / Iter-246n — Tamara settlement forensic (READ-ONLY).

Diagnostic endpoint that reproduces the auto-computed settlement
figures for a Tamara cycle SIDE-BY-SIDE with:

    • Per-order breakdown (every order inside the window)
    • Per-refund breakdown (every refund inside the window)
    • Provider-side settlement_entries (if the merchant uploaded the
      official Tamara settlement file)
    • The merchant's baseline numbers (passed via query string)
    • A clear cause analysis: missing/extra orders, wrong dates,
      wrong commission rate, orphan refunds, fixed-fee mismatch.

STRICTLY READ-ONLY.  Never writes.  Never adjusts historical data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _safe_str(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)


def make_tamara_forensic_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "tamara"])

    @router.get("/tamara-settlement-forensic")
    async def tamara_forensic(
        user: dict = Depends(current_user),
        date_from: str = Query(..., min_length=10, max_length=10),
        date_to: str = Query(..., min_length=10, max_length=10),
        invoice_date: Optional[str] = Query(None),
        # — Merchant-provided baseline (from Tamara's official invoice).
        # Optional; when provided, the response includes a delta block.
        baseline_gross: Optional[float] = Query(None),
        baseline_refunds: Optional[float] = Query(None),
        baseline_commission: Optional[float] = Query(None),
        baseline_vat: Optional[float] = Query(None),
        baseline_settlement_fee: Optional[float] = Query(None),
        baseline_net: Optional[float] = Query(None),
    ):
        """Compute the Tamara settlement for [date_from..date_to] and
        return a full per-order breakdown so the merchant can compare
        the system figures with the provider's official invoice.

        ⚠️ READ-ONLY.  This endpoint NEVER writes to MongoDB and
        NEVER posts journal entries.
        """
        uid = user["id"]

        # 1) Replay the auto-compute math used by `/bnpl/settlements`.
        from bnpl.settlements_service import (
            compute_settlement_for_provider,
            _merchant_fee_rates,
            _local_date_window_utc,
        )
        computed = await compute_settlement_for_provider(
            db, uid, "tamara", date_from=date_from, date_to=date_to,
        )
        rates = await _merchant_fee_rates(db, uid, "tamara")
        commission_rate = float(rates.get("commission_pct") or 0) / 100.0
        vat_rate = float(rates.get("vat_pct") or 0) / 100.0
        fixed_fee_per_order = float(rates.get("fixed_fee_per_order") or 0)

        # 2) Per-order breakdown — payment_transactions in window.
        utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)

        # Tamara groups sales by `effective_settlement_date` (priority:
        # provider_official_captured > billing_eligible > created_at).
        sales_match: Dict[str, Any] = {"user_id": uid, "provider": "tamara"}
        sales_match["effective_settlement_date"] = {
            **({"$gte": utc_gte} if utc_gte else {}),
            **({"$lte": utc_lte} if utc_lte else {}),
        }
        sales_match["is_pre_accounting"] = {"$ne": True}

        orders: List[Dict[str, Any]] = []
        sum_gross = 0.0
        sum_per_order_commission = 0.0
        sum_per_order_vat = 0.0
        provider_ids_in_window: set[str] = set()
        status_breakdown: Dict[str, Dict[str, float]] = {}
        attribution_breakdown: Dict[str, Dict[str, float]] = {}

        async for t in db.payment_transactions.find(
            sales_match,
            {"_id": 0, "id": 1, "amount": 1, "currency": 1,
             "provider_id": 1, "order_reference_id": 1, "order_number": 1,
             "created_at_provider": 1, "billing_eligible_at": 1,
             "effective_settlement_date": 1, "status": 1,
             "settlement_source": 1, "is_pre_accounting": 1},
        ).sort([("effective_settlement_date", 1)]):
            amt = float(t.get("amount") or 0)
            fee = amt * commission_rate + fixed_fee_per_order
            vat = fee * vat_rate
            sum_gross += amt
            sum_per_order_commission += fee
            sum_per_order_vat += vat
            pid = t.get("provider_id")
            if pid:
                provider_ids_in_window.add(pid)
            st = t.get("status") or "unknown"
            src = t.get("settlement_source") or "unknown"
            sb = status_breakdown.setdefault(st, {"count": 0, "sum": 0.0})
            sb["count"] += 1
            sb["sum"] += amt
            ab = attribution_breakdown.setdefault(src, {"count": 0, "sum": 0.0})
            ab["count"] += 1
            ab["sum"] += amt
            orders.append({
                "order_number": _safe_str(t.get("order_number")),
                "order_reference_id": _safe_str(t.get("order_reference_id")),
                "provider_id": _safe_str(pid),
                "amount": _r(amt),
                "currency": t.get("currency") or "SAR",
                "status": st,
                "settlement_source": src,
                "created_at_provider": _safe_str(t.get("created_at_provider")),
                "billing_eligible_at": _safe_str(t.get("billing_eligible_at")),
                "effective_settlement_date":
                    _safe_str(t.get("effective_settlement_date")),
                "commission_calc": _r(fee),
                "vat_calc": _r(vat),
                "in_window": True,
                "source_date_field": "effective_settlement_date",
            })

        # Round status/attribution sums for clean JSON output.
        for d in (status_breakdown, attribution_breakdown):
            for k in d:
                d[k]["sum"] = _r(d[k]["sum"])

        # 3) Per-refund breakdown — payment_refunds in window.
        refund_match: Dict[str, Any] = {"user_id": uid, "provider": "tamara"}
        refund_match["refunded_at"] = {
            **({"$gte": utc_gte} if utc_gte else {}),
            **({"$lte": utc_lte} if utc_lte else {}),
        }
        refund_match["is_pre_accounting"] = {"$ne": True}

        refunds_list: List[Dict[str, Any]] = []
        sum_refunds = 0.0
        orphan_refunds = 0
        orphan_refunds_sum = 0.0
        es_outside_count = 0
        # Iter-234 orphan recovery (mirrors compute engine).
        recovered_orders: List[Dict[str, Any]] = []
        recovered_amt = 0.0
        recovered_commission = 0.0
        recovered_vat = 0.0

        async for r in db.payment_refunds.find(
            refund_match,
            {"_id": 0, "id": 1, "provider_refund_id": 1,
             "provider_payment_id": 1, "order_reference_id": 1,
             "amount": 1, "refunded_at": 1, "reason": 1, "status": 1},
        ).sort([("refunded_at", 1)]):
            ramt = float(r.get("amount") or 0)
            sum_refunds += ramt
            pp = r.get("provider_payment_id")
            ref = r.get("order_reference_id")

            # Lookup original capture.
            orig = None
            if pp:
                orig = await db.payment_transactions.find_one(
                    {"user_id": uid, "provider": "tamara",
                     "provider_id": pp},
                    {"_id": 0, "amount": 1, "provider_id": 1,
                     "order_reference_id": 1, "order_number": 1,
                     "effective_settlement_date": 1,
                     "created_at_provider": 1, "is_pre_accounting": 1},
                )
            if not orig and ref:
                orig = await db.payment_transactions.find_one(
                    {"user_id": uid, "provider": "tamara",
                     "order_reference_id": ref},
                    {"_id": 0, "amount": 1, "provider_id": 1,
                     "order_reference_id": 1, "order_number": 1,
                     "effective_settlement_date": 1,
                     "created_at_provider": 1, "is_pre_accounting": 1},
                )

            link_status = "linked"
            orig_in_window = False
            if not orig:
                orphan_refunds += 1
                orphan_refunds_sum += ramt
                link_status = "orphan_no_original_found"
            else:
                if orig.get("is_pre_accounting"):
                    link_status = "linked_but_pre_accounting"
                orig_pid = orig.get("provider_id")
                if orig_pid and orig_pid in provider_ids_in_window:
                    orig_in_window = True
                else:
                    # Original capture lives OUTSIDE the window — this
                    # is the typical Tamara "captured + refunded same
                    # week but billing_eligible drifted" scenario.
                    es_outside_count += 1
                    if (not orig.get("is_pre_accounting")
                            and orig_pid not in provider_ids_in_window):
                        amt = float(orig.get("amount") or 0)
                        if amt > 0:
                            fee = amt * commission_rate + fixed_fee_per_order
                            vat = fee * vat_rate
                            recovered_amt += amt
                            recovered_commission += fee
                            recovered_vat += vat
                            if orig_pid:
                                provider_ids_in_window.add(orig_pid)
                            recovered_orders.append({
                                "order_number":
                                    _safe_str(orig.get("order_number")),
                                "order_reference_id":
                                    _safe_str(orig.get("order_reference_id")),
                                "provider_id": _safe_str(orig_pid),
                                "amount": _r(amt),
                                "commission_calc": _r(fee),
                                "vat_calc": _r(vat),
                                "created_at_provider": _safe_str(
                                    orig.get("created_at_provider")),
                                "effective_settlement_date": _safe_str(
                                    orig.get("effective_settlement_date")),
                                "reason":
                                    "orphan_refund_recovery_iter234",
                            })
                    link_status = "linked_capture_outside_window"

            refunds_list.append({
                "provider_refund_id":
                    _safe_str(r.get("provider_refund_id")),
                "provider_payment_id": _safe_str(pp),
                "order_reference_id": _safe_str(ref),
                "order_number": (
                    _safe_str(orig.get("order_number"))
                    if orig else None
                ),
                "refund_amount": _r(ramt),
                "refunded_at": _safe_str(r.get("refunded_at")),
                "reason": r.get("reason"),
                "status": r.get("status"),
                "original_capture_amount": (
                    _r(orig.get("amount")) if orig else None
                ),
                "original_capture_date": (
                    _safe_str(orig.get("effective_settlement_date"))
                    or _safe_str(orig.get("created_at_provider"))
                    if orig else None
                ),
                "link_status": link_status,
                "original_in_window": orig_in_window,
            })

        # Apply Iter-234 recovery to gross + commission totals (mirrors
        # what the production engine does).
        gross_with_recovery = sum_gross + recovered_amt
        commission_with_recovery = sum_per_order_commission + recovered_commission
        vat_with_recovery = sum_per_order_vat + recovered_vat

        # 4) Provider-side official settlement_entries — if uploaded.
        official_entries: List[Dict[str, Any]] = []
        official_totals: Dict[str, float] = {
            "sales_count": 0, "refunds_count": 0,
            "gross_sales": 0.0, "total_refunds": 0.0,
            "commission": 0.0, "commission_vat": 0.0,
            "net_payable": 0.0, "fixed_fee_sum": 0.0,
        }
        async for e in db.settlement_entries.find(
            {"user_id": uid, "provider": "tamara",
             "settlement_date": {"$gte": date_from, "$lte": date_to},
             "is_pre_accounting": {"$ne": True}},
            {"_id": 0, "order_number": 1, "event_type": 1,
             "settlement_date": 1, "actual_gross_amount": 1,
             "actual_payment_fee": 1, "actual_payment_vat": 1,
             "actual_net_amount": 1, "actual_refund_amount": 1,
             "actual_partial_refund_amount": 1, "currency": 1,
             "raw_row": 1},
        ).sort([("settlement_date", 1), ("order_number", 1)]):
            ev = (e.get("event_type") or "").lower()
            gross = float(e.get("actual_gross_amount") or 0)
            fee = float(e.get("actual_payment_fee") or 0)
            vat = float(e.get("actual_payment_vat") or 0)
            net = float(e.get("actual_net_amount") or 0)
            rfd = abs(float(e.get("actual_refund_amount")
                            or e.get("actual_partial_refund_amount") or 0))
            if ev == "refund":
                official_totals["refunds_count"] += 1
                official_totals["total_refunds"] += rfd
            else:
                official_totals["sales_count"] += 1
                official_totals["gross_sales"] += gross
                official_totals["commission"] += fee
                official_totals["commission_vat"] += vat
            official_totals["net_payable"] += net
            official_entries.append({
                "order_number": _safe_str(e.get("order_number")),
                "event_type": ev or "sale",
                "settlement_date": _safe_str(e.get("settlement_date")),
                "gross": _r(gross),
                "fee": _r(fee),
                "vat": _r(vat),
                "net": _r(net),
                "refund": _r(rfd) if rfd else 0.0,
                "currency": e.get("currency") or "SAR",
            })
        for k in official_totals:
            if k not in ("sales_count", "refunds_count"):
                official_totals[k] = _r(official_totals[k])

        has_official = (
            official_totals["sales_count"] + official_totals["refunds_count"]
        ) > 0

        # 5) Cross-reference: orders in our DB vs orders in official file.
        official_order_numbers = {
            e["order_number"] for e in official_entries
            if e.get("order_number")
        }
        db_order_numbers = {
            o["order_number"] for o in orders if o.get("order_number")
        }
        in_db_not_official = sorted(
            db_order_numbers - official_order_numbers
        ) if has_official else []
        in_official_not_db = sorted(
            official_order_numbers - db_order_numbers
        ) if has_official else []

        # Refund cross-reference: order numbers from official refund
        # rows that we DON'T have a matching row for in payment_refunds
        # (matched by order_number → order_reference_id → original
        # capture's provider_id).
        db_refund_order_numbers: set[str] = set()
        for rf in refunds_list:
            on = rf.get("order_number") or rf.get("order_reference_id")
            if on:
                db_refund_order_numbers.add(on)
        official_refund_order_numbers = {
            e["order_number"] for e in official_entries
            if e.get("event_type") == "refund" and e.get("order_number")
        }
        missing_refunds_in_db = sorted(
            official_refund_order_numbers - db_refund_order_numbers
        ) if has_official else []
        # Sum the missing refund amounts from the official file.
        missing_refunds_sum = _r(sum(
            e.get("refund", 0.0) for e in official_entries
            if e.get("event_type") == "refund"
            and e.get("order_number") in set(missing_refunds_in_db)
        )) if missing_refunds_in_db else 0.0

        # 6) Build totals comparison block.
        cmp_totals = computed.get("totals", {})
        system_view = {
            "transactions_count": cmp_totals.get("transactions_count", 0),
            "refunds_count": cmp_totals.get("refunds_count", 0),
            "gross_sales": _r(cmp_totals.get("gross_sales", 0)),
            "total_refunds": _r(cmp_totals.get("total_refunds", 0)),
            "net_sales": _r(cmp_totals.get("net_sales", 0)),
            "commission": _r(cmp_totals.get("commission", 0)),
            "commission_vat": _r(cmp_totals.get("commission_vat", 0)),
            "settlement_fee": _r(cmp_totals.get("settlement_fee", 0)),
            "settlement_fee_vat":
                _r(cmp_totals.get("settlement_fee_vat", 0)),
            "net_payable": _r(cmp_totals.get("net_payable", 0)),
        }
        forensic_view = {
            "gross_sales_iterated": _r(gross_with_recovery),
            "refunds_iterated": _r(sum_refunds),
            "commission_iterated": _r(commission_with_recovery),
            "vat_iterated": _r(vat_with_recovery),
            "orders_count_iterated": len(orders) + len(recovered_orders),
            "refunds_count_iterated": len(refunds_list),
            "orphan_refunds_count": orphan_refunds,
            "orphan_refunds_sum": _r(orphan_refunds_sum),
            "captures_outside_window_count": es_outside_count,
            "recovered_orders_count": len(recovered_orders),
            "recovered_orders_sum": _r(recovered_amt),
        }

        baseline: Optional[Dict[str, Any]] = None
        delta_vs_baseline: Optional[Dict[str, Any]] = None
        if any(v is not None for v in (
            baseline_gross, baseline_refunds, baseline_commission,
            baseline_vat, baseline_settlement_fee, baseline_net,
        )):
            baseline = {
                "gross_sales": _r(baseline_gross) if baseline_gross is not None else None,
                "total_refunds": _r(baseline_refunds) if baseline_refunds is not None else None,
                "commission": _r(baseline_commission) if baseline_commission is not None else None,
                "commission_vat": _r(baseline_vat) if baseline_vat is not None else None,
                "settlement_fee": _r(baseline_settlement_fee) if baseline_settlement_fee is not None else None,
                "net_payable": _r(baseline_net) if baseline_net is not None else None,
            }
            delta_vs_baseline = {}
            for key in ("gross_sales", "total_refunds", "commission",
                        "commission_vat", "settlement_fee", "net_payable"):
                bv = baseline.get(key)
                sv = system_view.get(key)
                if bv is None or sv is None:
                    continue
                delta_vs_baseline[key] = {
                    "baseline": bv,
                    "system": sv,
                    "delta_system_minus_baseline": _r(sv - bv),
                }

        # If official file exists, also compute delta system vs official.
        delta_vs_official: Optional[Dict[str, Any]] = None
        if has_official:
            delta_vs_official = {
                "gross_sales": {
                    "official": official_totals["gross_sales"],
                    "system": system_view["gross_sales"],
                    "delta": _r(system_view["gross_sales"]
                                - official_totals["gross_sales"]),
                },
                "total_refunds": {
                    "official": official_totals["total_refunds"],
                    "system": system_view["total_refunds"],
                    "delta": _r(system_view["total_refunds"]
                                - official_totals["total_refunds"]),
                },
                "commission": {
                    "official": official_totals["commission"],
                    "system": system_view["commission"],
                    "delta": _r(system_view["commission"]
                                - official_totals["commission"]),
                },
                "commission_vat": {
                    "official": official_totals["commission_vat"],
                    "system": system_view["commission_vat"],
                    "delta": _r(system_view["commission_vat"]
                                - official_totals["commission_vat"]),
                },
                "net_payable": {
                    "official": official_totals["net_payable"],
                    "system": system_view["net_payable"],
                    "delta": _r(system_view["net_payable"]
                                - official_totals["net_payable"]),
                },
            }

        # 7) Cause analysis — Arabic narrative for the merchant.
        causes: List[str] = []

        # Compare against baseline if provided.
        if baseline:
            if baseline.get("gross_sales") is not None:
                d = system_view["gross_sales"] - baseline["gross_sales"]
                if abs(d) >= 0.5:
                    sign = "زائد" if d > 0 else "ناقص"
                    causes.append(
                        f"إجمالي المبيعات في النظام مختلف بمقدار "
                        f"{_r(abs(d))} ر.س ({sign}) عن فاتورة تمارا "
                        f"({system_view['gross_sales']} مقابل "
                        f"{baseline['gross_sales']}). "
                        f"تحقّق من الطلبات التالية: "
                        + (
                            "طلبات موجودة في النظام وغير موجودة في الفاتورة "
                            + ", ".join(in_db_not_official[:10])
                            if d > 0 and in_db_not_official
                            else (
                                "طلبات في فاتورة تمارا وغير موجودة في النظام "
                                + ", ".join(in_official_not_db[:10])
                                if d < 0 and in_official_not_db
                                else "لا يوجد فرق على مستوى رقم الطلب"
                            )
                        )
                    )
            if baseline.get("total_refunds") is not None:
                d = system_view["total_refunds"] - baseline["total_refunds"]
                if abs(d) >= 0.5:
                    sign = "زائد" if d > 0 else "ناقص"
                    causes.append(
                        f"إجمالي المرتجعات مختلف بمقدار "
                        f"{_r(abs(d))} ر.س ({sign})."
                    )
            if baseline.get("commission") is not None:
                d = system_view["commission"] - baseline["commission"]
                if abs(d) >= 0.5:
                    causes.append(
                        f"العمولة المحسوبة {system_view['commission']} ر.س "
                        f"بينما الفاتورة {baseline['commission']} ر.س "
                        f"(فرق {_r(d)} ر.س). "
                        f"النسبة الحالية: commission_pct="
                        f"{rates.get('commission_pct')}%, "
                        f"fixed_fee_per_order="
                        f"{rates.get('fixed_fee_per_order')} ر.س. "
                        f"راجع إعدادات BNPL إن كانت نسبة العمولة الحقيقية "
                        f"التي يخصمها تمارا مختلفة."
                    )
            if baseline.get("commission_vat") is not None:
                d = system_view["commission_vat"] - baseline["commission_vat"]
                if abs(d) >= 0.5:
                    causes.append(
                        f"ضريبة القيمة المضافة على العمولة "
                        f"{system_view['commission_vat']} ر.س بينما "
                        f"الفاتورة {baseline['commission_vat']} ر.س "
                        f"(فرق {_r(d)} ر.س)."
                    )
            if baseline.get("net_payable") is not None:
                d = system_view["net_payable"] - baseline["net_payable"]
                if abs(d) >= 0.5:
                    causes.append(
                        f"صافي التحويل المحسوب "
                        f"{system_view['net_payable']} ر.س "
                        f"مقابل {baseline['net_payable']} ر.س في الفاتورة "
                        f"(فرق {_r(d)} ر.س)."
                    )

        # Generic structural causes.
        if orphan_refunds > 0:
            causes.append(
                f"{orphan_refunds} مسترجَع يتيم بمجموع "
                f"{_r(orphan_refunds_sum)} ر.س — مسترجَعات داخل الفترة "
                f"ولم يُعثر على البيع الأصلي في قاعدة البيانات."
            )
        if missing_refunds_in_db:
            causes.append(
                f"{len(missing_refunds_in_db)} مسترجَع موجود في فاتورة "
                f"تمارا الرسمية بمجموع {missing_refunds_sum} ر.س "
                f"ولم يصل إلى `payment_refunds` (webhook/sync ضائع). "
                f"أرقام الطلبات: "
                + ", ".join(missing_refunds_in_db[:15])
                + (" …" if len(missing_refunds_in_db) > 15 else "")
            )
        non_billable_statuses = {"authorised", "authorized", "pending",
                                 "new", "checkout", "created"}
        non_billable_count = sum(
            v.get("count", 0) for k, v in status_breakdown.items()
            if (k or "").lower() in non_billable_statuses
        )
        non_billable_sum = _r(sum(
            v.get("sum", 0) for k, v in status_breakdown.items()
            if (k or "").lower() in non_billable_statuses
        ))
        if non_billable_count > 0:
            causes.append(
                f"{non_billable_count} طلب بحالة غير billable "
                f"(authorised/pending/…) بمجموع {non_billable_sum} ر.س — "
                f"غالباً تمارا لا تُدرجها في فاتورة هذه الفترة."
            )
        estimated_count = (
            attribution_breakdown.get("estimated", {}).get("count", 0)
        )
        if estimated_count > 0:
            causes.append(
                f"{estimated_count} طلب يعتمد على `settlement_source=estimated` "
                f"(fallback إلى created_at_provider) — قد يدخل في فترة "
                f"خاطئة. الأفضل تشغيل recompute_attribution لرفعها إلى "
                f"`provider_captured` أو `billing_eligible`."
            )
        if es_outside_count > 0:
            causes.append(
                f"{es_outside_count} مسترجَع مرتبط ببيع أصلي خارج "
                f"الفترة (تم استرداده بآلية Iter-234 إن أمكن)."
            )
        fixed_fee_implied = (
            _r(commission_with_recovery
               - (gross_with_recovery * commission_rate))
        )
        if abs(fixed_fee_implied
               - fixed_fee_per_order * (len(orders) + len(recovered_orders))
               ) >= 0.5:
            causes.append(
                f"الرسوم الثابتة المُطبقة في النظام: "
                f"{fixed_fee_per_order} ر.س × "
                f"{len(orders) + len(recovered_orders)} طلب = "
                f"{_r(fixed_fee_per_order * (len(orders) + len(recovered_orders)))} ر.س. "
                f"تحقّق من قيمة fixed_fee_per_order مقابل ما يفرضه تمارا فعلياً."
            )
        if not causes:
            causes.append(
                "لا يوجد فرق مادي بين أرقام النظام والقاعدة المعطاة. "
                f"النسب المعتمدة حالياً: {rates}"
            )

        return {
            "ok": True,
            "iter": "iter246n",
            "provider": "tamara",
            "read_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {
                "from": date_from,
                "to": date_to,
                "invoice_date": invoice_date,
                "utc_window": {"gte": utc_gte, "lte": utc_lte},
            },
            "rates_in_use": rates,
            "baseline_from_user": baseline,
            "system_totals": system_view,
            "forensic_iterated_totals": forensic_view,
            "delta_vs_baseline": delta_vs_baseline,
            "delta_vs_official_file": delta_vs_official,
            "orders": orders,
            "recovered_orders_iter234": recovered_orders,
            "refunds": refunds_list,
            "official_settlement_entries": official_entries,
            "official_totals": official_totals if has_official else None,
            "cross_reference": {
                "orders_in_db_not_in_official": in_db_not_official,
                "orders_in_official_not_in_db": in_official_not_db,
                "refund_order_numbers_in_official_not_in_db":
                    missing_refunds_in_db,
                "missing_refunds_sum_from_official":
                    missing_refunds_sum,
                "official_file_present": has_official,
            },
            "order_status_breakdown": status_breakdown,
            "attribution_source_breakdown": attribution_breakdown,
            "possible_causes_of_gap": causes,
            "raw_compute_dump": computed,
        }

    return router
