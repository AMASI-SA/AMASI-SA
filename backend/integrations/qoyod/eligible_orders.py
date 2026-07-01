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

from datetime import datetime, date, timedelta, timezone
from typing import Any, Optional


# ── Iter-001f (2026-02) — Tax-period sync cutoff ───────────────────
# Business decision: MEZAN starts pushing to قيود from Q3-2026 only.
# Any Salla order whose CREATION date is before this cutoff belongs
# to Q2-2026 and must NOT appear in Eligible Orders (nor be sent by
# any downstream mechanism).
#
# The filter uses the order's CREATION date in Salla, NOT `received_at`
# on our side — a legacy order that arrived at our pipeline after
# 2026-07-01 is still Q2 accounting-wise.
QOYOD_SYNC_START_DATE: str = "2026-07-01"          # ISO YYYY-MM-DD
QOYOD_TAX_PERIOD: str      = "Q3-2026"
QOYOD_SYNC_TZ: str         = "Asia/Riyadh"

_SYNC_START_ISO: date = date.fromisoformat(QOYOD_SYNC_START_DATE)


def _parse_iso_date(v: Any) -> Optional[date]:
    """Coerce heterogeneous Salla/Mongo date shapes into a `date`.

    Accepts:
        - `date` object      → returned as-is
        - `datetime` object  → `.date()`
        - `YYYY-MM-DD` str   → `date.fromisoformat`
        - `YYYY-MM-DD HH:…` str → parsed as ISO then `.date()`
        - Full ISO w/ tz     → parsed then `.date()`
        - Anything else / falsy → None
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    # Fast path — plain YYYY-MM-DD
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    # Fallback — full ISO datetime with 'T' or space separator
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00").replace(" ", "T", 1)).date()
    except ValueError:
        return None


def _extract_order_created_at(order: dict) -> Optional[date]:
    """Return the Salla order CREATION date as a `date`, or None.

    Priority (per user directive Iter-001f):
        1. raw Salla `created_at`               (order['created_at'])
        2. `order_date`                          — trusted unless
           `order_date_inferred=True` (would be a guess by Make.com)
        3. inbox raw_payload → data.date.date  (Salla webhook shape)
        4. inbox raw_payload → data.created_at
        5. canonical_payload.order_date / created_at (if surfaced)

    Anything else → None (row will be classified as
    `excluded_missing_order_created_at`).
    """
    # 1. Direct top-level created_at (used by webhook payloads and
    #    manual Excel imports that preserve the Salla timestamp).
    d = _parse_iso_date(order.get("created_at"))
    if d is not None:
        return d
    # 2. Normalized `order_date` — trusted only when NOT flagged as
    #    inferred (Make.com guessed today when Salla omitted the field).
    if not order.get("order_date_inferred"):
        d = _parse_iso_date(order.get("order_date"))
        if d is not None:
            return d
    # 3–4. Inbox fallback — dig into raw_payload we stashed on the
    #    pseudo-order (`_inbox_row.raw_payload`).
    inbox_row = order.get("_inbox_row") or {}
    raw = inbox_row.get("raw_payload") or {}
    if isinstance(raw, dict):
        data = raw.get("data") or raw
        if isinstance(data, dict):
            # Salla webhook shape: {"data": {"date": {"date": "..."}}}
            date_field = data.get("date")
            if isinstance(date_field, dict):
                d = _parse_iso_date(date_field.get("date"))
                if d is not None:
                    return d
            elif date_field is not None:
                d = _parse_iso_date(date_field)
                if d is not None:
                    return d
            d = _parse_iso_date(data.get("created_at"))
            if d is not None:
                return d
    # 5. Last resort — canonical order_date (may exist on some rows).
    d = _parse_iso_date(order.get("order_date")) if \
        order.get("order_date_inferred") is False else None
    return d


# ── Eligible statuses (subset of the unified set per user directive) ─
# Per Iter-001 request: ONLY completed / delivered / shipping.
# Explicitly excludes: waiting, pending, cancelled, refunded, deleted,
# and even shipped/processing/in_progress (kept out of THIS report).
#
# Iter-001e (2026-02) — Status normalization: production data mixes
# space vs underscore separators (`جاري التوصيل` vs `جاري_التوصيل`).
# We store the canonical space form here, then compare via
# `_normalize_status()` which strips + casefolds + `_→space`.
ELIGIBLE_STATUSES: frozenset[str] = frozenset({
    "completed", "delivered", "shipping",
    "تم التنفيذ", "تم التوصيل", "جاري التوصيل",
})

# Statuses that MUST stay excluded (documented for excluded_reason_counts
# transparency; production may write either space or underscore form).
INELIGIBLE_STATUSES: frozenset[str] = frozenset({
    "waiting", "pending", "in_review", "in review",
    "cancelled", "canceled", "refunded", "deleted",
    "بإنتظار الدفع", "بانتظار الدفع",
    "محذوف", "ملغي", "ملغى", "مسترجع",
})


def _normalize_status(s: Any) -> str:
    """Iter-001e canonical form: lowercase, `_`→space, collapsed spaces.

    Guarantees that `جاري_التوصيل`, `جاري التوصيل`, `  جاري  التوصيل  `
    all map to the same key so equality checks work across sources.
    """
    if s is None:
        return ""
    txt = str(s).replace("_", " ").strip()
    # collapse multiple whitespace to single
    txt = " ".join(txt.split())
    # casefold is Unicode-safe (Arabic is unaffected but English lowered)
    return txt.casefold()


_ELIGIBLE_NORMALIZED: frozenset[str] = frozenset(
    _normalize_status(s) for s in ELIGIBLE_STATUSES)
_INELIGIBLE_NORMALIZED: frozenset[str] = frozenset(
    _normalize_status(s) for s in INELIGIBLE_STATUSES)


def _is_eligible_status(s: Any) -> bool:
    """True iff the (normalized) status is in the eligible set."""
    return _normalize_status(s) in _ELIGIBLE_NORMALIZED


def _expand_status_variants(base: frozenset[str]) -> list[str]:
    """Expand a status set into every space/underscore variant for
    MongoDB `$in`. Ensures a doc stored as `جاري_التوصيل` matches even
    though the canonical form is `جاري التوصيل`."""
    out: set[str] = set()
    for s in base:
        out.add(s)
        out.add(s.replace(" ", "_"))
        out.add(s.replace("_", " "))
    return sorted(out)

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
    """Look up customer mapping. Returns {resolved, qoyod_id, reason}.

    Iter-001b fix — `unified_orders` doesn't nest customer under a
    single key; different sources write different top-level fields
    (`customer_mobile`, `customer.phone`, `customer.mobile`,
    `customer_email`). We check ALL known shapes.
    """
    customer = order.get("customer") or {}
    raw_phone = (
        customer.get("phone")
        or customer.get("mobile")
        or order.get("customer_mobile")
        or order.get("customer_phone")
    )
    raw_email = (
        customer.get("email")
        or order.get("customer_email")
    )
    phone = _normalise_phone(raw_phone)
    email = (raw_email or "").strip().lower() or None
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
    """Iter-001k+ (2026-02-27) — Mezan-VAT-15% guard.

    Historical formula `items_sum + shipping + canonical.tax_amount`
    is STRUCTURALLY WRONG because it depends on Salla's per-order
    tax field, which may be missing/zero/8%/anything. The real Qoyod
    invoice builder (`invoice_builder.build_invoice_payload`) uses
    Mezan's fixed 15% VAT via `match_salla_total` policy, which
    reconciles Σ(qoyod_gross) to `salla.total_amount` by construction.

    This guard now uses the SAME Mezan-VAT simulation the real
    payload builder uses. `valid` is decided by
    `simulated_qoyod_diff_vs_salla_total` — NOT by Salla tax.

    Returned keys:
        valid                 — decision boolean (Mezan diff ≤ 0.01)
        total                 — Salla official total (unchanged)
        expected              — Mezan simulated Qoyod gross
        diff                  — Mezan simulated diff vs Salla
        legacy_expected       — old `items+shipping+tax` (diagnostic)
        legacy_diff           — old diff (diagnostic)
        mezan_vat_rate        — 0.15 (Mezan SSOT)
        payload_date_source   — "send_date"
        guard_engine          — "mezan_vat_15_simulation"
    """
    # ── Local import to avoid a circular dep with qoyod_simulation
    #    which imports helpers from order_totals_breakdown.
    from integrations.qoyod.qoyod_simulation import (
        build_qoyod_simulation, MEZAN_VAT_RATE, PAYLOAD_DATE_SOURCE,
    )

    total = float(order.get("total_amount") or 0)

    # ── Legacy formula (diagnostic only — not used for decision) ─
    items_sum = 0.0
    for it in order.get("items") or []:
        qty = float(it.get("quantity") or 1)
        price = float(it.get("unit_price") or it.get("price") or 0)
        items_sum += qty * price
    shipping = float(order.get("shipping_amount") or 0)
    salla_tax_ignored = float(order.get("tax_amount") or 0)
    legacy_expected = round(
        items_sum + shipping + salla_tax_ignored, 2)
    legacy_diff = round(total - legacy_expected, 2)

    # ── Mezan-VAT simulation (SOURCE OF TRUTH for `valid`) ───────
    # The order dict IS the canonical payload the pipeline uses;
    # wrap in an inbox-shaped row for the simulator.
    sim = build_qoyod_simulation(
        inbox_row={"canonical_payload": order})
    mezan_expected = sim.get(
        "simulated_qoyod_total_using_mezan_vat_15") or 0.0
    mezan_diff = sim.get(
        "simulated_qoyod_diff_vs_salla_total") or 0.0

    return {
        "valid":                abs(float(mezan_diff)) <= _TOLERANCE,
        "total":                total,
        "expected":             mezan_expected,
        "diff":                 mezan_diff,
        # ── Diagnostics ─────────────────────────────────────────
        "legacy_expected":      legacy_expected,
        "legacy_diff":          legacy_diff,
        "mezan_vat_rate":       MEZAN_VAT_RATE,
        "payload_date_source":  PAYLOAD_DATE_SOURCE,
        "guard_engine":         "mezan_vat_15_simulation",
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

    # 2. Totals mismatch? (Iter-001k+ — Mezan-VAT-15% guard)
    if not totals_check["valid"]:
        return {
            "classification": "totals_mismatch",
            "blocker_reason":
                f"mezan_vat_15 simulation: "
                f"salla_total={totals_check['total']} vs "
                f"simulated_qoyod={totals_check['expected']} "
                f"(diff={totals_check['diff']}). "
                f"legacy_check diff={totals_check.get('legacy_diff')}"
                f" — diagnostic only.",
            "posting_mode": None,
            "recommended_next_action":
                "افحص canonical.items[].total والشحن — قد يكون "
                "normalizer لم يلتقط item.total من سلة بشكل صحيح.",
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
    debug: bool = False,
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
    debug             : Iter-001b — attach a `_diagnostic` block with raw
                        collection stats and 5-doc samples. Read-only.

    Returns a dict conforming to the design's response schema.
    """
    limit = max(1, min(int(limit), 500))
    since_days = max(1, min(int(since_days), 365))
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    # `unified_orders.order_date` is a normalised `YYYY-MM-DD` string
    # (see orders_db.py:25) — NOT an ISO datetime. We compare with the
    # date component so string ordering works correctly.
    since_date_str = since_dt.date().isoformat()
    since_iso_full = since_dt.isoformat()

    # 1. Gates
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    receiving_bank = settings.get("bank_transfer_receiving_account_id")
    receiving_bank_configured = bool(receiving_bank)

    # 2. Detect data source (Iter-001c: auto-fallback).
    # On some tenants (e.g. Production without Salla-to-unified sync),
    # `unified_orders` is empty and the pipeline data lives ONLY in
    # `integration_inbox`. Switch data source automatically so the
    # report stays useful.
    unified_orders_total = await db.unified_orders.count_documents(
        {"user_id": user_id})

    source_mode: str
    source_reason: str
    orders: list[dict] = []
    inbox_rows_grouped: dict[str, dict] = {}

    fetch_cap = max(limit * 5, 500)

    if unified_orders_total > 0:
        source_mode = "unified_orders"
        source_reason = (
            f"unified_orders has {unified_orders_total} docs for tenant "
            "— using it as the primary source.")
        # `order_date` is a YYYY-MM-DD string; `received_at` is a BSON
        # datetime. Compare each with its own type.
        orders = await db.unified_orders.find(
            {
                "user_id": user_id,
                "$and": [
                    {"$or": [
                        {"order_status":
                         {"$in": _expand_status_variants(ELIGIBLE_STATUSES)}},
                        {"order_status_slug":
                         {"$in": _expand_status_variants(ELIGIBLE_STATUSES)}},
                    ]},
                    {"$or": [
                        {"order_date":    {"$gte": since_date_str}},
                        {"received_at":   {"$gte": since_dt}},
                    ]},
                ],
            },
            {"_id": 0},
        ).sort([("received_at", -1)]).to_list(length=fetch_cap)
    else:
        source_mode = "integration_inbox_fallback"
        source_reason = (
            "unified_orders is empty for this tenant — using "
            "integration_inbox as the fallback source. "
            "`missing_from_pipeline` is unavailable in this mode.")

        # Group by salla_order_number, keep the LATEST trace per order.
        # Filter is on `received_at >= since_dt` (BSON datetime — this
        # is the fix for the Iter-001b bug: was compared with string).
        inbox_cursor = db.integration_inbox.find(
            {
                "user_id": user_id,
                "received_at": {"$gte": since_dt},
            },
            {"_id": 0},
        ).sort([("received_at", -1)])
        seen: set[str] = set()
        async for row in inbox_cursor:
            key = str(row.get("salla_order_number")
                      or row.get("salla_order_id") or row.get("trace_id"))
            if not key or key in seen:
                continue
            seen.add(key)
            inbox_rows_grouped[key] = row
            if len(inbox_rows_grouped) >= fetch_cap:
                break

        # Reshape inbox rows into pseudo-order dicts so the same
        # classifier + item builder works.
        for key, row in inbox_rows_grouped.items():
            canonical = row.get("canonical_payload") or {}
            payloads = row.get("qoyod_payloads") or {}
            inv_payload = payloads.get("invoice") or {}
            recv = row.get("received_at")
            recv_iso = recv.isoformat() if isinstance(recv, datetime) \
                else str(recv) if recv is not None else None
            orders.append({
                # canonical fields the rest of the code expects:
                "user_id":      user_id,
                "order_id":     row.get("salla_order_id"),
                "order_number": row.get("salla_order_number") or key,
                "order_status": canonical.get("order_status")
                              or canonical.get("order_status_native"),
                "order_status_slug": canonical.get("order_status_slug")
                              or canonical.get("order_status"),
                "payment_method": canonical.get("payment_method"),
                "total_amount":   canonical.get("total_amount") or 0,
                "shipping_amount": canonical.get("shipping_amount") or 0,
                "tax_amount":     canonical.get("tax_amount") or 0,
                "items":          canonical.get("items") or [],
                "customer":       canonical.get("customer") or {},
                "customer_mobile": (canonical.get("customer") or {}).get(
                    "phone") or canonical.get("customer_mobile"),
                "order_date":     canonical.get("order_date"),
                "received_at":    recv_iso,
                # inbox-specific pass-throughs for the classifier:
                "_inbox_row":     row,
                "_from_inbox":    True,
                "_invoice_payload": inv_payload,
            })

    # 3. Classify each order.
    # Iter-001d — accounting invariant: every scanned row lands in
    # EITHER `counts` OR `excluded_reason_counts`. No silent drops.
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
        "unclassified_needs_review":    0,
    }
    excluded_reason_counts: dict[str, int] = {}
    # Iter-001e — per-status breakdowns so operators can see EXACTLY
    # which raw status values are being accepted vs. rejected.
    total_eligible_by_status: dict[str, int] = {}
    total_ineligible_by_status: dict[str, int] = {}
    # Iter-001f — sync-cutoff counters.
    excluded_before_sync_start_date_count = 0
    excluded_missing_order_created_at_count = 0
    items: list[dict] = []
    total_hidden_already_sent = 0

    for order in orders:
        order_id = str(order.get("order_id") or order.get("id") or "")
        order_number = str(order.get("order_number") or order_id)

        # ── Iter-001f — Tax-period sync-cutoff filter (FIRST) ─────
        # Any order whose Salla creation date is BEFORE the sync
        # cutoff (2026-07-01) belongs to a prior tax period and MUST
        # NOT enter the classifier under any classification.
        order_created_at = _extract_order_created_at(order)
        if order_created_at is None:
            excluded_missing_order_created_at_count += 1
            excluded_reason_counts["missing_order_created_at"] = \
                excluded_reason_counts.get(
                    "missing_order_created_at", 0) + 1
            continue
        if order_created_at < _SYNC_START_ISO:
            excluded_before_sync_start_date_count += 1
            reason_key = (
                f"before_sync_start_date:{QOYOD_SYNC_START_DATE} "
                f"(order_created_at={order_created_at.isoformat()})")
            excluded_reason_counts[reason_key] = \
                excluded_reason_counts.get(reason_key, 0) + 1
            continue

        # Iter-001c — in inbox_fallback mode the inbox_row is already
        # attached to the pseudo-order; skip the extra query and use it.
        if order.get("_from_inbox"):
            inbox_row = order.get("_inbox_row")
            # Post-filter: only keep rows whose canonical status is
            # actually eligible (integration_inbox holds ALL states).
            # Iter-001e — use normalized comparison so `جاري_التوصيل`
            # matches `جاري التوصيل`.
            status_raw = order.get("order_status") or ""
            slug_raw   = order.get("order_status_slug") or ""
            if (_is_eligible_status(status_raw)
                    or _is_eligible_status(slug_raw)):
                # eligible — record the raw form for the breakdown.
                key_raw = str(status_raw or slug_raw or "(empty)").strip()
                total_eligible_by_status[key_raw] = \
                    total_eligible_by_status.get(key_raw, 0) + 1
            else:
                # Iter-001d — count instead of silently dropping.
                key_raw = str(status_raw or slug_raw or "(empty)").strip()
                reason_key = (
                    f"status_not_eligible:{key_raw}" if key_raw
                    else "status_missing_from_canonical")
                excluded_reason_counts[reason_key] = \
                    excluded_reason_counts.get(reason_key, 0) + 1
                total_ineligible_by_status[key_raw or "(empty)"] = \
                    total_ineligible_by_status.get(
                        key_raw or "(empty)", 0) + 1
                continue
        else:
            # unified_orders path — the primary query already filtered by
            # eligible status variants, so we can log this as eligible.
            status_raw = str(order.get("order_status")
                             or order.get("order_status_slug")
                             or "(empty)").strip()
            total_eligible_by_status[status_raw] = \
                total_eligible_by_status.get(status_raw, 0) + 1
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

        # If inbox row itself already carries a real qoyod_invoice_id
        # (common in fallback mode), synthesise a lightweight invoice
        # dict so the classifier catches `already_sent`.
        if invoice is None and inbox_row and _is_real_invoice_id(
                inbox_row.get("qoyod_invoice_id")):
            invoice = {
                "qoyod_invoice_id": inbox_row.get("qoyod_invoice_id"),
                "posting_mode":     inbox_row.get("posting_mode"),
            }

        customer_check = await _check_customer(db, user_id, order)
        products_check = await _check_products(db, user_id, order)
        totals_check   = _check_totals(order)
        verdict = _classify(
            order, inbox_row, invoice,
            customer_check, products_check, totals_check,
            receiving_bank_configured,
        )
        cls = verdict["classification"]

        # `missing_from_pipeline` cannot arise in inbox_fallback mode
        # by definition — every row IS from the inbox. Downgrade any
        # such verdict to `ready_for_manual_approval` for safety.
        if cls == "missing_from_pipeline" and \
                source_mode == "integration_inbox_fallback":
            cls = "ready_for_manual_approval"
            verdict["classification"] = cls
            verdict["blocker_reason"] = (
                "unified_orders empty — inbox row exists but "
                "pipeline stalled")

        # Iter-001d — Safety net: if verdict returned an unknown
        # classification, bucket it in `unclassified_needs_review`
        # so the invariant holds.
        if cls not in counts:
            counts["unclassified_needs_review"] = \
                counts.get("unclassified_needs_review", 0) + 1
            verdict["classification"] = "unclassified_needs_review"
            verdict["blocker_reason"] = (
                f"internal: unknown classification token '{cls}'")
            cls = "unclassified_needs_review"
        else:
            counts[cls] = counts.get(cls, 0) + 1

        # Hide `already_sent` from `items` unless requested — but ALWAYS
        # count them in `counts`.
        if cls == "already_sent" and not show_already_sent:
            total_hidden_already_sent += 1
            continue

        if len(items) >= limit:
            continue

        items.append({
            "order_number":            order_number,
            "salla_order_id":          order_id or None,
            "latest_trace_id":         (inbox_row or {}).get("trace_id"),
            "status":                  order.get("order_status"),
            "status_slug":             order.get("order_status_slug"),
            "payment_method":          order.get("payment_method"),
            "total_amount":            round(float(order.get(
                "total_amount") or 0), 2),
            # Iter-001f — the ACTUAL Salla creation date used for the
            # cutoff decision (accountant needs to see this).
            "salla_order_created_at":  order_created_at.isoformat(),
            "created_at":              order.get("order_date")
                                        or order.get("received_at"),
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
                "valid":            totals_check["valid"],
                "total":            totals_check["total"],
                "expected":         totals_check["expected"],
                "diff":             totals_check["diff"],
                # ── Iter-001k+ diagnostics ──────────────────────
                "legacy_expected":       totals_check.get(
                                            "legacy_expected"),
                "legacy_diff":           totals_check.get(
                                            "legacy_diff"),
                "mezan_vat_rate":        totals_check.get(
                                            "mezan_vat_rate"),
                "payload_date_source":   totals_check.get(
                                            "payload_date_source"),
                "guard_engine":          totals_check.get(
                                            "guard_engine"),
            },
            "posting_mode":            verdict["posting_mode"],
            "classification":          cls,
            "blocker_reason":          verdict["blocker_reason"],
            "recommended_next_action": verdict["recommended_next_action"],
        })

    # Iter-001d — Explicit bookkeeping counters. Invariant:
    #    total_classified + excluded_status_count == total_scanned
    # `total_scanned` = every row that entered the classifier loop
    # (i.e. rows fetched from the primary source after date filter).
    total_classified = sum(counts.values())
    excluded_status_count = sum(excluded_reason_counts.values())

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since_days":   since_days,
        "since_date":   since_date_str,
        "source_mode":   source_mode,
        "source_reason": source_reason,
        # ── Iter-001f — Tax-period sync cutoff ──────────────────
        "sync_start_date":   QOYOD_SYNC_START_DATE,
        "tax_period":        QOYOD_TAX_PERIOD,
        "sync_timezone":     QOYOD_SYNC_TZ,
        "date_filter_basis": "salla_order_created_at",
        "excluded_before_sync_start_date_count":
            excluded_before_sync_start_date_count,
        "excluded_missing_order_created_at_count":
            excluded_missing_order_created_at_count,
        # Definitions:
        # - total_source_rows       = rows fetched from source
        #                             (before any post-filter)
        # - total_scanned           = rows entered the classifier
        #                             (same as total_source_rows for now)
        # - total_classified        = rows that landed in one of `counts`
        # - excluded_status_count   = rows skipped because canonical
        #                             status was not in ELIGIBLE_STATUSES
        #                             OR fell before the sync cutoff
        #                             OR missed the creation date entirely
        # - unclassified_count      = rows that fell through classifier
        #                             (safety-net bucket)
        # - total_hidden_already_sent = rows counted as already_sent but
        #                               hidden from `items` list
        # - total_returned_items    = final `len(items)`
        # INVARIANT:
        #   total_classified + excluded_status_count == total_scanned
        "total_source_rows":  len(orders),
        "total_scanned":      len(orders),
        "total_classified":   total_classified,
        "excluded_status_count": excluded_status_count,
        "unclassified_count": counts.get("unclassified_needs_review", 0),
        "total_hidden_already_sent": total_hidden_already_sent,
        "total_returned_items": len(items),
        "invariant_holds":    (total_classified
                               + excluded_status_count == len(orders)),
        "counts":              counts,
        "excluded_reason_counts": excluded_reason_counts,
        "total_eligible_by_status":   total_eligible_by_status,
        "total_ineligible_by_status": total_ineligible_by_status,
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
            "Status normalization: `_` treated as space "
            "(`جاري_التوصيل` = `جاري التوصيل`).",
            "cancelled / refunded / deleted / waiting / pending "
            "مستبعدة من الاستعلام.",
            f"Eligible Orders يعرض فقط الطلبات المنشأة من "
            f"{QOYOD_SYNC_START_DATE} وما بعد، لأن التشغيل الضريبي "
            f"يبدأ من الربع الثالث ({QOYOD_TAX_PERIOD}).",
            "Date filter basis: salla_order_created_at "
            "(NOT received_at). Missing created_at → excluded.",
            "Invariant: total_classified + excluded_status_count "
            "== total_scanned (see `invariant_holds`).",
        ],
    }
    if source_mode == "integration_inbox_fallback":
        result["notes"].append(
            "missing_from_pipeline unavailable because unified_orders "
            "is empty for this tenant.")

    # ── Iter-001b — Read-Only diagnostic block ─────────────────────
    # Enabled via `debug=true` query param. Emits collection totals +
    # status breakdowns + 5-doc samples so operators can verify field
    # names / value shapes without direct DB access. Read-only.
    if debug:
        async def _sample(coll_name: str, filt: dict, keys: list[str]):
            proj = {"_id": 0, **{k: 1 for k in keys}}
            return await db[coll_name].find(filt, proj).limit(5).to_list(5)

        async def _breakdown(coll_name: str, filt: dict, field: str):
            cursor = db[coll_name].aggregate([
                {"$match": filt},
                {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}},
                {"$limit": 40},
            ])
            rows = await cursor.to_list(50)
            return {(r["_id"] or "(null)"): r["n"] for r in rows}

        uo_filt_all = {"user_id": user_id}
        uo_filt_90d = {"user_id": user_id, "$or": [
            {"order_date": {"$gte": since_date_str}},
            {"received_at": {"$gte": since_dt}},
        ]}
        ib_filt_all = {"user_id": user_id}
        # BSON datetime filter (fixed — Iter-001c: was ISO string before).
        ib_filt_90d = {"user_id": user_id,
                       "received_at": {"$gte": since_dt}}

        # `received_at` type breakdown — helps spot mixed types (str vs
        # BSON datetime) which explain filter mismatches.
        type_probe = await db.integration_inbox.aggregate([
            {"$match": {"user_id": user_id}},
            {"$project": {"t": {"$type": "$received_at"}}},
            {"$group": {"_id": "$t", "n": {"$sum": 1}}},
        ]).to_list(20)
        received_at_type_breakdown = {r["_id"]: r["n"] for r in type_probe}

        # Parsed sample dates (first 5 rows post-90d filter — proves
        # the filter matches).
        parsed_sample = await db.integration_inbox.find(
            ib_filt_90d, {"_id": 0, "received_at": 1,
                          "salla_order_number": 1, "pipeline_stage": 1}
        ).sort([("received_at", -1)]).limit(5).to_list(5)

        result["_diagnostic"] = {
            "user_id_used_in_query": user_id,
            "since_date_str_used":   since_date_str,
            "since_iso_full_used":   since_iso_full,
            "since_dt_used_for_bson_filter": since_dt.isoformat(),
            "source_mode":           source_mode,
            "source_reason":         source_reason,
            "unified_orders_total_all_time":
                await db.unified_orders.count_documents(uo_filt_all),
            "unified_orders_total_90d":
                await db.unified_orders.count_documents(uo_filt_90d),
            "integration_inbox_total_all_time":
                await db.integration_inbox.count_documents(ib_filt_all),
            "integration_inbox_total_90d":
                await db.integration_inbox.count_documents(ib_filt_90d),
            "integration_inbox_total_90d_no_filter_by_status":
                await db.integration_inbox.count_documents(ib_filt_90d),
            "received_at_type_breakdown": received_at_type_breakdown,
            "unified_orders_status_breakdown_all_time":
                await _breakdown("unified_orders", uo_filt_all,
                                 "order_status"),
            "unified_orders_status_slug_breakdown_all_time":
                await _breakdown("unified_orders", uo_filt_all,
                                 "order_status_slug"),
            "integration_inbox_stage_breakdown_all_time":
                await _breakdown("integration_inbox", ib_filt_all,
                                 "pipeline_stage"),
            "integration_inbox_stage_breakdown_90d":
                await _breakdown("integration_inbox", ib_filt_90d,
                                 "pipeline_stage"),
            "sample_unified_orders_all_time": await _sample(
                "unified_orders", uo_filt_all,
                ["order_id", "order_number", "order_status",
                 "order_status_slug", "order_date", "received_at",
                 "payment_method", "total_amount", "user_id"]),
            "sample_integration_inbox_all_time": await _sample(
                "integration_inbox", ib_filt_all,
                ["salla_order_id", "salla_order_number", "trace_id",
                 "pipeline_stage", "received_at", "user_id"]),
            "sample_integration_inbox_last_5_within_90d":
                parsed_sample,
            "eligible_statuses_configured": sorted(ELIGIBLE_STATUSES),
        }

    return result
