"""Eligible Orders — Read-Only Audit Endpoint (Iter-001, 2026-07-01).

Purpose
────────
Surfaces every Salla order (from `unified_orders`) that is billable
per its status BUT hasn't reached قيود as a real invoice yet, PLUS
those that made it there ("already_sent") for count summaries.

Read-Only Contract
──────────────────
    • NO Qoyod API calls.
    • NO writes on ANY collection.
    • NO approve, send, one-shot, or selective-live-send actions.
    • Respects `production_writes_locked` and `selective_live_send_enabled` gates.

Data Sources (6 collections, ALL read-only)
    1. `unified_orders`               — universe of Salla orders
    2. `integration_inbox`            — pipeline entry check
    3. `qoyod_invoices`               — invoice-sent check
    4. `qoyod_customers_mapping`      — customer resolution check
    5. `qoyod_products_mapping`       — product resolution check
    6. `qoyod_settings`               — invoice_trigger_statuses / gates

Classifications (9)
    ready_for_preview
    ready_for_manual_approval
    already_sent
    blocked_customer
    blocked_product
    blocked_bank_transfer_routing
    blocked_status
    totals_mismatch
    missing_from_pipeline    ← Iter-001 addition (per user directive)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional


# ── Eligible statuses (subset of the unified set per user directive) ─
# Per Iter-001 request: ONLY completed / delivered / shipping.
# Explicitly excludes: waiting, pending, cancelled, refunded, deleted,
# and even shipped/processing/in_progress (kept out of THIS report).
ELIGIBLE_STATUSES: frozenset[str] = frozenset({
    "completed", "delivered", "shipping",
    "تم التنفيذ", "تم التوصيل", "جاري التوصيل",
})

# BNPL family — allowed since Iter-293.5-rev3.
_BNPL = frozenset({
    "tabby", "tabby_installment", "tabby_installments",
    "tamara", "tamara_installment", "tamara_installments",
    "emkan", "emkan_installment",
})
_PREPAID = frozenset({
    "mada", "apple_pay", "applepay", "stc_pay", "stcpay",
    "credit_card", "creditcard", "visa", "mastercard",
    "master_card", "amex", "american_express",
})
_COD = frozenset({"cod", "cash_on_delivery", "cashondelivery"})
_BANK = frozenset({"bank_transfer", "banktransfer"})

_TOLERANCE = 0.01  # SAR


def _is_real_invoice_id(v: Any) -> bool:
    """قيود invoice id that is a real one — not DRY:/PREVIEW: sentinel."""
    if v is None or v == "":
        return False
    s = str(v)
    if s.startswith("DRY:") or s.startswith("PREVIEW:"):
        return False
    return True


def _normalise_phone(raw: Any) -> Optional[str]:
    """Match `customer_resolver._normalize_phone_for_lookup` E.164 rules."""
    if not raw:
        return None
    import re
    s = re.sub(r"[^\d+]", "", str(raw))
    if s.startswith("+"):
        return s
    if s.startswith("00966"):
        return "+" + s[2:]
    if s.startswith("966"):
        return "+" + s
    if s.startswith("0") and len(s) == 10:
        return "+966" + s[1:]
    if s.startswith("5") and len(s) == 9:
        return "+966" + s
    return s


async def _check_customer(db, user_id: str, order: dict) -> dict:
    """Look up customer mapping. Returns {resolved, qoyod_id, reason}."""
    customer = order.get("customer") or {}
    phone = _normalise_phone(customer.get("phone") or customer.get("mobile"))
    email = (customer.get("email") or "").strip().lower() or None
    lookup_key = phone or email
    if not lookup_key:
        return {"resolved": False, "qoyod_id": None,
                "reason": "no phone or email on order"}
    mapping = await db.qoyod_customers_mapping.find_one(
        {"user_id": user_id, "lookup_key": lookup_key},
        {"_id": 0, "qoyod_customer_id": 1, "dry_run_only": 1})
    if not mapping:
        return {"resolved": False, "qoyod_id": None,
                "reason": f"no mapping for {lookup_key}"}
    if mapping.get("dry_run_only"):
        return {"resolved": False,
                "qoyod_id": mapping.get("qoyod_customer_id"),
                "reason": "customer mapping is dry_run_only"}
    cid = mapping.get("qoyod_customer_id")
    if not cid or str(cid).startswith("DRY:"):
        return {"resolved": False, "qoyod_id": cid,
                "reason": "customer_id is a DRY sentinel"}
    return {"resolved": True, "qoyod_id": cid, "reason": None}


async def _check_products(db, user_id: str, order: dict) -> dict:
    """Look up product mappings. Returns
    {resolved, resolved_count, dry_run_only, missing, first_blocker}."""
    items = order.get("items") or []
    resolved_count = 0
    dry_count = 0
    missing = []
    first_blocker = None
    for it in items:
        sku = (it.get("sku") or "").strip()
        if not sku:
            missing.append("(item has no SKU)")
            if first_blocker is None:
                first_blocker = "an item has no SKU"
            continue
        m = await db.qoyod_products_mapping.find_one(
            {"user_id": user_id, "sku": sku},
            {"_id": 0, "qoyod_product_id": 1, "dry_run_only": 1})
        if not m:
            missing.append(sku)
            if first_blocker is None:
                first_blocker = f"SKU '{sku}' has no mapping"
            continue
        pid = m.get("qoyod_product_id")
        if m.get("dry_run_only") or str(pid).startswith("DRY:") \
                or str(pid).startswith("PREVIEW:") or pid is None:
            dry_count += 1
            if first_blocker is None:
                first_blocker = f"SKU '{sku}' is dry_run_only / unresolved"
            continue
        resolved_count += 1
    return {
        "resolved": (dry_count == 0 and len(missing) == 0
                     and resolved_count > 0),
        "resolved_count": resolved_count,
        "dry_run_only": dry_count,
        "missing": missing,
        "first_blocker": first_blocker,
    }


def _check_totals(order: dict) -> dict:
    """Validate line-item + shipping + tax ≈ order total."""
    total = float(order.get("total_amount") or 0)
    items = order.get("items") or []
    items_sum = 0.0
    for it in items:
        qty = float(it.get("quantity") or 1)
        price = float(it.get("unit_price") or it.get("price") or 0)
        items_sum += qty * price
    shipping = float(order.get("shipping_amount") or 0)
    tax = float(order.get("tax_amount") or 0)
    expected = round(items_sum + shipping + tax, 2)
    diff = round(total - expected, 2)
    return {
        "valid": abs(diff) <= _TOLERANCE,
        "total": total,
        "expected": expected,
        "diff": diff,
    }


def _classify(
    order: dict,
    inbox_row: Optional[dict],
    invoice: Optional[dict],
    customer_check: dict,
    products_check: dict,
    totals_check: dict,
    receiving_bank_configured: bool,
) -> dict:
    """Apply the 9-way classifier per Iter-001 rules.

    Order of evaluation (first match wins):
        1. already_sent           — real qoyod_invoice_id exists
        2. totals_mismatch        — line items != total
        3. blocked_bank_transfer_routing — bank_transfer w/o receiving bank
        4. blocked_status         — unsupported payment method
        5. blocked_customer       — customer not resolved
        6. blocked_product        — any SKU dry_run_only / missing
        7. missing_from_pipeline  — no inbox row at all
        8. ready_for_manual_approval — inbox row exists but stalled
        9. ready_for_preview      — everything green
    """
    pm = str(order.get("payment_method") or "").strip().lower()

    # 1. Already sent?
    if invoice and _is_real_invoice_id(invoice.get("qoyod_invoice_id")):
        return {
            "classification": "already_sent",
            "blocker_reason": None,
            "posting_mode": invoice.get("posting_mode"),
            "recommended_next_action":
                "لا حاجة لإجراء — الفاتورة موجودة في قيود.",
        }

    # 2. Totals mismatch?
    if not totals_check["valid"]:
        return {
            "classification": "totals_mismatch",
            "blocker_reason":
                f"total={totals_check['total']} vs expected="
                f"{totals_check['expected']} (diff={totals_check['diff']})",
            "posting_mode": None,
            "recommended_next_action":
                "افحص أسعار البنود والشحن والضريبة على الطلب.",
        }

    # 3. Bank transfer routing?
    if pm in _BANK:
        if not receiving_bank_configured:
            return {
                "classification": "blocked_bank_transfer_routing",
                "blocker_reason":
                    "لا يوجد بنك مستلم مُحدَّد في إعدادات قيود",
                "posting_mode": "credit_invoice_only",
                "recommended_next_action":
                    "بانتظار Iter-294 (توجيه سندات التحويل البنكي).",
            }
        return {
            "classification": "blocked_bank_transfer_routing",
            "blocker_reason":
                "التحويل البنكي مؤجَّل حتى إكمال Iter-294",
            "posting_mode": "credit_invoice_only",
            "recommended_next_action": "بانتظار Iter-294.",
        }

    # 4. Unsupported payment method?
    if pm and pm not in _COD and pm not in _PREPAID and pm not in _BNPL:
        return {
            "classification": "blocked_status",
            "blocker_reason":
                f"طريقة دفع غير مدعومة: '{pm}'",
            "posting_mode": None,
            "recommended_next_action":
                "أضف طريقة الدفع لقائمة السماح أو تجاهل الطلب.",
        }

    # 5. Customer not resolved?
    if not customer_check["resolved"]:
        return {
            "classification": "blocked_customer",
            "blocker_reason": customer_check["reason"],
            "posting_mode": None,
            "recommended_next_action":
                "أنشئ العميل في قيود ثم استخدم "
                "`POST /api/integrations/qoyod/customers/adopt`.",
        }

    # 6. Products not resolved?
    if not products_check["resolved"]:
        return {
            "classification": "blocked_product",
            "blocker_reason": products_check["first_blocker"],
            "posting_mode": None,
            "recommended_next_action":
                "اعتمد المنتجات عبر "
                "`POST /api/integrations/qoyod/products/adopt`.",
        }

    # Determine posting_mode for the ready paths.
    posting_mode = (
        "credit_invoice_only" if pm in _COD
        else "paid_receipt" if (pm in _PREPAID or pm in _BNPL)
        else None
    )

    # 7. Missing from pipeline?
    if inbox_row is None:
        return {
            "classification": "missing_from_pipeline",
            "blocker_reason":
                "الطلب في سلة لكنه لم يدخل خط المعالجة (webhook مفقود أو pause)",
            "posting_mode": posting_mode,
            "recommended_next_action":
                "أعد إرسال webhook الطلب من سلة أو أعِد تشغيل الـ pipeline.",
        }

    # 8. In inbox but not yet sent → manual approval candidate.
    stage = inbox_row.get("pipeline_stage") or ""
    if stage not in ("COMPLETED", "COMPLETED_WITH_ROUNDING_WARNING"):
        return {
            "classification": "ready_for_manual_approval",
            "blocker_reason":
                f"الطلب متوقف في المرحلة '{stage}' بعد اكتمال الفحوصات",
            "posting_mode": posting_mode,
            "recommended_next_action":
                "افحص الطلب في صفحة 'طلبات قيود المعلقة' ثم اعتمده يدوياً.",
        }

    # 9. Green path.
    return {
        "classification": "ready_for_preview",
        "blocker_reason": None,
        "posting_mode": posting_mode,
        "recommended_next_action":
            "يمكن تشغيل Preview عبر `POST /admin/preview-reprocess`.",
    }


async def build_eligible_orders_report(
    db,
    *,
    user_id: str,
    since_days: int = 90,
    limit: int = 200,
    show_already_sent: bool = False,
) -> dict:
    """Assemble the full Read-Only report.

    Args
    ────
    db                : Mongo database handle.
    user_id           : tenant id.
    since_days        : lookback window; default 90 days.
    limit             : max rows in the `items` list (counts always full).
    show_already_sent : include `already_sent` rows in `items` (they're
                        always counted in `counts`).

    Returns a dict conforming to the design's response schema.
    """
    limit = max(1, min(int(limit), 500))
    since_days = max(1, min(int(since_days), 365))
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    since_iso = since_dt.isoformat()

    # 1. Gates
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    receiving_bank = settings.get("bank_transfer_receiving_account_id")
    receiving_bank_configured = bool(receiving_bank)

    # 2. Fetch candidate orders — cap at 5× limit to survive filtering.
    fetch_cap = max(limit * 5, 500)
    orders = await db.unified_orders.find(
        {
            "user_id": user_id,
            "$or": [
                {"status": {"$in": list(ELIGIBLE_STATUSES)}},
                {"status_slug": {"$in": list(ELIGIBLE_STATUSES)}},
            ],
            "created_at": {"$gte": since_iso},
        },
        {"_id": 0},
    ).sort([("created_at", -1)]).to_list(length=fetch_cap)

    # 3. Classify each order.
    counts: dict[str, int] = {
        "ready_for_preview":            0,
        "ready_for_manual_approval":    0,
        "already_sent":                 0,
        "blocked_customer":             0,
        "blocked_product":              0,
        "blocked_bank_transfer_routing": 0,
        "blocked_status":               0,
        "totals_mismatch":              0,
        "missing_from_pipeline":        0,
    }
    items: list[dict] = []

    for order in orders:
        order_id = str(order.get("order_id") or order.get("id") or "")
        order_number = str(order.get("order_number") or order_id)

        inbox_row = await db.integration_inbox.find_one(
            {"user_id": user_id,
             "$or": [{"salla_order_id": order_id},
                     {"salla_order_number": order_number}]},
            {"_id": 0, "pipeline_stage": 1, "trace_id": 1,
             "qoyod_invoice_id": 1},
            sort=[("received_at", -1)])
        invoice = await db.qoyod_invoices.find_one(
            {"user_id": user_id,
             "$or": [{"salla_order_id": order_id},
                     {"salla_order_number": order_number}]},
            {"_id": 0, "qoyod_invoice_id": 1, "posting_mode": 1,
             "status": 1},
            sort=[("created_at", -1)])
        customer_check = await _check_customer(db, user_id, order)
        products_check = await _check_products(db, user_id, order)
        totals_check   = _check_totals(order)
        verdict = _classify(
            order, inbox_row, invoice,
            customer_check, products_check, totals_check,
            receiving_bank_configured,
        )
        cls = verdict["classification"]
        counts[cls] = counts.get(cls, 0) + 1

        # Hide `already_sent` from `items` unless requested — but ALWAYS
        # count them in `counts`.
        if cls == "already_sent" and not show_already_sent:
            continue

        if len(items) >= limit:
            continue

        items.append({
            "order_number":            order_number,
            "salla_order_id":          order_id or None,
            "latest_trace_id":         (inbox_row or {}).get("trace_id"),
            "status":                  order.get("status"),
            "status_slug":             order.get("status_slug"),
            "payment_method":          order.get("payment_method"),
            "total_amount":            round(float(order.get(
                "total_amount") or 0), 2),
            "created_at":              order.get("created_at"),
            "completed_at":            order.get("completed_at"),
            "delivered_at":            order.get("delivered_at"),
            "existing_qoyod_invoice_id":
                (invoice or {}).get("qoyod_invoice_id") if invoice else None,
            "idempotency_status":
                "sent" if (invoice and _is_real_invoice_id(
                    (invoice or {}).get("qoyod_invoice_id"))) else
                "pending",
            "customer_status": {
                "resolved":  customer_check["resolved"],
                "qoyod_id":  customer_check["qoyod_id"],
                "reason":    customer_check["reason"],
            },
            "products_status": {
                "resolved":       products_check["resolved"],
                "resolved_count": products_check["resolved_count"],
                "dry_run_only":   products_check["dry_run_only"],
                "missing":        products_check["missing"],
            },
            "totals_status": {
                "valid":    totals_check["valid"],
                "total":    totals_check["total"],
                "expected": totals_check["expected"],
                "diff":     totals_check["diff"],
            },
            "posting_mode":            verdict["posting_mode"],
            "classification":          cls,
            "blocker_reason":          verdict["blocker_reason"],
            "recommended_next_action": verdict["recommended_next_action"],
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since_days":   since_days,
        "since_date":   since_iso,
        "total_scanned": len(orders),
        "counts":        counts,
        "gates": {
            "production_writes_locked": bool(
                settings.get("production_writes_locked", True)),
            "selective_live_send_enabled": bool(
                settings.get("selective_live_send_enabled", False)),
            "settlements_write_gate":
                "OPEN (pending disable per P0.1 gate)",
            "receiving_bank_configured": receiving_bank_configured,
        },
        "items":         items,
        "notes": [
            "READ-ONLY REPORT — لا استدعاء لـ Qoyod، لا كتابة على DB.",
            "Statuses included: completed / delivered / shipping "
            "(Arabic natives accepted).",
            "cancelled / refunded / deleted / waiting / pending "
            "مستبعدة من الاستعلام.",
        ],
    }
