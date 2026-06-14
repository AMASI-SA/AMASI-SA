"""Iter-176 — COD Source Diagnostic Endpoint.

Purpose: Give the merchant a precise, transparent breakdown of how the
"الدفع عند الاستلام" (COD) account balance was computed BEFORE they
execute the Phase 4 Closeout migration.

Reports:
1. Total COD orders in unified_orders.
2. Whether the current logic uses Confirmed-only OR Delivered-only.
3. Total COD per order_status_policy bucket
   (confirmed/delivered/pending/cancelled/refunded).
4. Total COD per shipping_company (SMSA, iMile, others, unknown).
5. Sum of manual bank transfers FROM the COD account.
6. The final balance reconciliation.

This is a READ-ONLY endpoint — it never modifies any data.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from typing import Optional

from balances import _is_cod_method
from payment_methods import normalize_payment_method


def _is_cod_robust(raw: str) -> bool:
    """Robust COD detection that handles Arabic hamza variants
    (e.g. 'دفع عند الإستلام' vs 'الدفع عند الاستلام') by going
    through the canonical payment_methods normalizer first."""
    if not raw:
        return False
    if _is_cod_method(raw):
        return True
    try:
        sub_key, _disp, _parent = normalize_payment_method(raw)
        return sub_key == "cash_on_delivery"
    except Exception:  # noqa: BLE001
        return False


def make_cod_diagnostic_router(db, current_user):
    router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

    @router.get("/cod-source")
    async def cod_source_breakdown(user: dict = Depends(current_user)):
        uid = user["id"]

        # ── 1) Locate the user's COD account in `accounts`.
        cod_account = None
        async for a in db.accounts.find(
            {"user_id": uid, "account_type": "payment_platform"},
            {"_id": 0},
        ):
            name = (a.get("name") or "")
            normalized = (a.get("normalized_payment_method") or "")
            if (
                _is_cod_method(name)
                or normalized in ("cod", "cash_on_delivery")
            ):
                cod_account = a
                break

        if not cod_account:
            return {
                "found_cod_account": False,
                "message": (
                    "لم يتم العثور على حساب من نوع payment_platform "
                    "مرتبط بـ COD لهذا المستخدم."
                ),
            }

        cod_account_id = cod_account.get("id")
        current_balance = float(cod_account.get("current_balance") or 0)
        expected_orders_balance = float(
            cod_account.get("expected_orders_balance") or 0
        )
        orders_count_on_account = int(
            cod_account.get("orders_count") or 0
        )
        last_synced_at_raw = cod_account.get("last_synced_at")
        # Normalize last_synced_at to a comparable datetime (UTC aware).
        from datetime import datetime, timezone
        last_synced_dt = None
        if isinstance(last_synced_at_raw, datetime):
            last_synced_dt = last_synced_at_raw
        elif isinstance(last_synced_at_raw, str) and last_synced_at_raw:
            try:
                last_synced_dt = datetime.fromisoformat(
                    last_synced_at_raw.replace("Z", "+00:00")
                )
            except Exception:  # noqa: BLE001
                last_synced_dt = None
        if last_synced_dt and last_synced_dt.tzinfo is None:
            last_synced_dt = last_synced_dt.replace(tzinfo=timezone.utc)

        # ── 2) Order-status policy used by the central metrics engine.
        # This is what determines whether the system uses Confirmed/
        # Delivered/Pending/Cancelled/Refunded as the basis.
        try:
            from order_status_policy import get_policy_map, resolve_category
            policy_overrides = await get_policy_map(db, uid)
        except Exception:  # noqa: BLE001
            policy_overrides = {}
            resolve_category = None  # type: ignore

        # ── 3) Walk unified_orders for COD only, bucket by policy
        #    category AND by shipping_company.
        per_category: dict[str, dict] = {
            "confirmed": {"count": 0, "gross": 0.0},
            "pending": {"count": 0, "gross": 0.0},
            "cancelled": {"count": 0, "gross": 0.0},
            "refunded": {"count": 0, "gross": 0.0},
            "_unknown": {"count": 0, "gross": 0.0},
        }
        per_status_raw: dict[str, dict] = {}  # raw status from DB
        per_company: dict[str, dict] = {}
        delivered_keywords = (
            "تم التوصيل", "delivered", "تم التسليم", "completed",
        )
        delivered_per_company: dict[str, dict] = {}

        total_orders = 0
        # Iter-180 — Orders that arrived AFTER the COD account was last
        # synced. These explain any drift between current_balance (cached)
        # and the live walk total (which includes them).
        post_sync_orders: list[dict] = []
        post_sync_total_gross = 0.0
        post_sync_total_confirmed = 0.0

        async for o in db.unified_orders.find(
            {"user_id": uid, "is_pre_accounting": {"$ne": True}},
            {
                "_id": 0,
                "id": 1,
                "order_id": 1,
                "reference_id": 1,
                "payment_method": 1,
                "actual_payment_method": 1,
                "order_status": 1,
                "order_status_slug": 1,
                "shipping_company": 1,
                "total_amount": 1,
                "received_at": 1,
                "created_at": 1,
                "order_date": 1,
            },
        ):
            method = (
                o.get("actual_payment_method")
                or o.get("payment_method")
                or ""
            )
            if not _is_cod_robust(method):
                continue
            total_orders += 1
            gross = float(o.get("total_amount") or 0)
            raw_status = (o.get("order_status") or "").strip() or "—"
            company = (o.get("shipping_company") or "").strip() or "غير محدد"

            # Policy-based category
            if resolve_category:
                cat = resolve_category(raw_status, policy_overrides)
            else:
                cat = None
            cat_key = cat if cat in per_category else "_unknown"
            per_category[cat_key]["count"] += 1
            per_category[cat_key]["gross"] += gross

            # Raw status bucket (so merchant sees exact distribution)
            sbucket = per_status_raw.setdefault(
                raw_status, {"status": raw_status, "count": 0, "gross": 0.0}
            )
            sbucket["count"] += 1
            sbucket["gross"] += gross

            # Per-company bucket — ALL COD orders
            cbucket = per_company.setdefault(
                company, {"company": company, "count": 0, "gross": 0.0}
            )
            cbucket["count"] += 1
            cbucket["gross"] += gross

            # Iter-180 — flag orders that arrived after the cached
            # current_balance was computed. These are the ones causing
            # any walk-vs-cache drift.
            if last_synced_dt is not None:
                recv = o.get("received_at") or o.get("created_at")
                recv_dt = None
                if isinstance(recv, datetime):
                    recv_dt = recv
                elif isinstance(recv, str) and recv:
                    try:
                        recv_dt = datetime.fromisoformat(
                            recv.replace("Z", "+00:00")
                        )
                    except Exception:  # noqa: BLE001
                        recv_dt = None
                if recv_dt and recv_dt.tzinfo is None:
                    recv_dt = recv_dt.replace(tzinfo=timezone.utc)
                if recv_dt and recv_dt > last_synced_dt:
                    is_confirmed_after = cat_key == "confirmed"
                    post_sync_orders.append({
                        "order_id": (
                            o.get("order_id")
                            or o.get("reference_id")
                            or o.get("id")
                            or "—"
                        ),
                        "received_at": recv_dt.isoformat(),
                        "order_status": raw_status,
                        "shipping_company": company,
                        "gross": round(gross, 2),
                        "policy_category": cat_key,
                        "counted_in_confirmed": is_confirmed_after,
                    })
                    post_sync_total_gross += gross
                    if is_confirmed_after:
                        post_sync_total_confirmed += gross

            # Per-company DELIVERED-only bucket (manual heuristic)
            is_delivered = any(
                k.lower() in raw_status.lower() for k in delivered_keywords
            )
            if is_delivered:
                dbucket = delivered_per_company.setdefault(
                    company,
                    {"company": company, "count": 0, "gross": 0.0},
                )
                dbucket["count"] += 1
                dbucket["gross"] += gross

        # ── 4) Manual transactions on the COD account.
        tx_in_total = 0.0
        tx_in_count = 0
        tx_out_total = 0.0
        tx_out_count = 0
        recent_out = []

        async for t in db.account_transactions.find(
            {"user_id": uid, "account_id": cod_account_id},
            {"_id": 0, "amount": 1, "direction": 1, "transaction_date": 1,
             "description": 1, "category": 1},
        ).sort([("transaction_date", -1)]):
            amt = float(t.get("amount") or 0)
            if t.get("direction") == "in":
                tx_in_total += amt
                tx_in_count += 1
            else:
                tx_out_total += amt
                tx_out_count += 1
                if len(recent_out) < 10:
                    recent_out.append({
                        "date": t.get("transaction_date"),
                        "amount": round(amt, 2),
                        "category": t.get("category"),
                        "description": (t.get("description") or "")[:80],
                    })

        # ── 5) Reconciliation math.
        # current_balance = expected_orders_balance + IN - OUT
        # So derived = expected - OUT + IN
        derived_balance = round(
            expected_orders_balance + tx_in_total - tx_out_total, 2
        )
        reconciliation_diff = round(derived_balance - current_balance, 2)

        def _r(d):
            return {k: round(v, 2) if isinstance(v, float) else v
                    for k, v in d.items()}

        # Sort status raw by gross desc for readability.
        per_status_sorted = sorted(
            (_r(v) for v in per_status_raw.values()),
            key=lambda x: -x["gross"],
        )
        per_company_sorted = sorted(
            (_r(v) for v in per_company.values()),
            key=lambda x: -x["gross"],
        )
        delivered_sorted = sorted(
            (_r(v) for v in delivered_per_company.values()),
            key=lambda x: -x["gross"],
        )

        # ── 6) Construct the verdict on basis (Confirmed-only vs Delivered)
        #    The CURRENT engine uses Confirmed (per code review).
        basis_explanation = (
            "النظام يحتسب رصيد COD حالياً بناءً على الطلبات "
            "المُصنَّفة 'Confirmed' حسب سياسة order_status_policy. "
            "تشمل عادةً 'مكتمل' / 'تم التوصيل' / 'تم الدفع' "
            "(حسب تعريفك). الـ Cancelled و Pending مُستبعدان من "
            "expected_orders_balance."
        )

        # ── 6.5) Sort post-sync orders newest-first for display.
        post_sync_orders.sort(key=lambda x: x["received_at"], reverse=True)

        # Walk-vs-cache drift summary. The "walk" = live aggregation of
        # unified_orders we just performed; the "cache" = the value on
        # the account doc. A non-zero `walk_vs_cache_diff` is normal and
        # simply means orders arrived after the last sync — it is NOT
        # a corruption.
        walk_confirmed_total = round(per_category["confirmed"]["gross"], 2)
        walk_confirmed_count = int(per_category["confirmed"]["count"])
        # Iter-180 — Amount comparison: walk Confirmed gross vs the
        # cached current_balance (which excludes IN/OUT here for purity
        # because the merchant's typical case has no manual IN/OUT yet).
        amount_diff_confirmed_vs_balance = round(
            walk_confirmed_total - current_balance, 2
        )
        # Count comparison: walk's TOTAL cod orders vs the account's
        # cached orders_count (both should track ALL cod orders, not
        # just Confirmed).
        count_diff_total_vs_cache = total_orders - orders_count_on_account

        return {
            "found_cod_account": True,
            "account": {
                "id": cod_account_id,
                "name": cod_account.get("name"),
                "current_balance": round(current_balance, 2),
                "expected_orders_balance": round(expected_orders_balance, 2),
                "orders_count_on_account": orders_count_on_account,
                "last_synced_at": (
                    last_synced_dt.isoformat() if last_synced_dt else None
                ),
            },
            "basis": {
                "current_logic": "Confirmed (per order_status_policy)",
                "explanation": basis_explanation,
                "uses_delivered_only": False,
            },
            "total_cod_orders_in_db": total_orders,
            "by_policy_category": {k: _r(v) for k, v in per_category.items()},
            "by_raw_status": per_status_sorted,
            "by_shipping_company_all": per_company_sorted,
            "by_shipping_company_delivered_only": delivered_sorted,
            "manual_transactions": {
                "in_total": round(tx_in_total, 2),
                "in_count": tx_in_count,
                "out_total": round(tx_out_total, 2),
                "out_count": tx_out_count,
                "recent_out_sample": recent_out,
            },
            "reconciliation": {
                "formula": "current_balance = expected_orders_balance + IN - OUT",
                "expected_orders_balance": round(expected_orders_balance, 2),
                "in_total": round(tx_in_total, 2),
                "out_total": round(tx_out_total, 2),
                "derived_balance": derived_balance,
                "actual_current_balance": round(current_balance, 2),
                "diff_should_be_zero": reconciliation_diff,
                "interpretation": (
                    "إذا كان diff_should_be_zero = 0 فالحساب متطابق. "
                    "أي فرق يشير لتعديل يدوي خارج الحركات أو "
                    "إعادة مزامنة الطلبات بعد التحويلات."
                ),
            },
            # Iter-180 — explains any discrepancy between the live walk
            # (what the diagnostic shows) and the cached current_balance.
            "post_sync_drift": {
                "last_synced_at": (
                    last_synced_dt.isoformat() if last_synced_dt else None
                ),
                "post_sync_orders_count": len(post_sync_orders),
                "post_sync_total_gross": round(post_sync_total_gross, 2),
                "post_sync_total_confirmed": round(post_sync_total_confirmed, 2),
                "walk_confirmed_total": walk_confirmed_total,
                "walk_confirmed_count": walk_confirmed_count,
                "walk_total_orders_count": total_orders,
                "cache_orders_count": orders_count_on_account,
                "cache_current_balance": round(current_balance, 2),
                "cache_vs_walk_amount_diff": amount_diff_confirmed_vs_balance,
                "cache_vs_walk_count_diff": count_diff_total_vs_cache,
                "orders": post_sync_orders[:25],  # cap UI list
                "interpretation": (
                    "هذه الطلبات وصلت إلى النظام بعد آخر مزامنة "
                    "لحساب COD. لذلك تظهر في «المحسوب الآن» (Walk) "
                    "لكنها غير موجودة في current_balance (Cache). "
                    "هذا سلوك طبيعي ولا يؤثر على الترحيل لأن COD "
                    "مُستبعد من Phase 4 أصلاً."
                ) if last_synced_dt else (
                    "الحساب لا يحتوي على last_synced_at — لا يمكن "
                    "تحديد طلبات ما بعد المزامنة. شغّل مزامنة "
                    "للحساب أو راجع expected_orders_balance يدوياً."
                ),
            },
        }

    return router
