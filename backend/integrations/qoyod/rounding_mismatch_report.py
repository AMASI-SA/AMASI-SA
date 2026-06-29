"""Iter-290j-rounding-fix · Phase 1 — Read-only rounding diagnostic.

What this does
──────────────
Scans `integration_inbox` for invoices whose money trail diverges
anywhere between Salla → Mezan → قيود and classifies each row into
ONE of five buckets so the operator can see WHERE the halala drift
originates BEFORE we change any pricing logic.

Strictly READ-ONLY:
  • No DB writes.
  • No Qoyod writes.
  • Optionally one `GET /invoices/{id}` per row for live status
    (gated behind `live_check=True` — default off).

Classification
──────────────
For each row we compute four numbers:

  salla_total              = canonical.total_amount               (source of truth)
  mezan_computed_total     = Σ row.line_diagnostics.computed_qoyod_gross
                              (Mezan's PRE-POST estimate of what قيود will compute)
  qoyod_invoice_total      = qoyod_responses.invoice.body.total
                              (قيود's POST-POST authoritative total)
  payment_amount_sent      = qoyod_responses.invoice_payment.body.amount
                              (what /invoice_payments saw)

…then derive:

  invoice_diff = qoyod_invoice_total - salla_total
  payment_diff = payment_amount_sent  - qoyod_invoice_total

…and apply the bucket rules:

  PAYMENT_MISMATCH_ONLY
      |invoice_diff| ≤ 0.005  AND  |payment_diff| > 0.005
      → قيود computed the invoice correctly, but our payment amount
        didn't match what قيود ended up with.

  SHIPPING_ROUNDING_MISMATCH
      |invoice_diff| > 0.005
      AND a shipping line is present
      AND |shipping_line.computed_gross - shipping_target_gross| > 0.005
      → the gap originates ONLY on the shipping line (its Mezan
        gross diverges from its Salla-implied gross).

  DISCOUNT_ALLOCATION_MISMATCH
      |invoice_diff| > 0.005
      AND exactly ONE non-shipping line has |line_diff| > 0.005
      → the gap concentrates on a single product line — usually
        because Mezan's per-line discount distribution rounded
        differently than قيود.

  MULTI_LINE_CUMULATIVE_ROUNDING
      |invoice_diff| > 0.005
      AND ≥2 lines each contribute |line_diff| > 0.001
      → the gap is the SUM of many tiny per-line rounding deltas.
        This is the classic "0.01 SAR drift" pattern.

  INVOICE_TOTAL_ROUNDING_MISMATCH  (catch-all)
      |invoice_diff| > 0.005 but none of the more specific buckets
      fit. Means we know the invoice total drifted but can't pin it
      on a specific line.

  NO_MISMATCH
      Everything ties out. Row is NOT returned by the report
      (filtered out for noise control).

Why we run a classifier instead of one-size-fits-all
────────────────────────────────────────────────────
Each bucket implies a different fix:
  • PAYMENT_MISMATCH_ONLY        → switch payment source to قيود-total.
  • SHIPPING_ROUNDING_MISMATCH   → fix shipping math.
  • DISCOUNT_ALLOCATION_MISMATCH → redistribute the discount.
  • MULTI_LINE_CUMULATIVE_ROUNDING → switch to Decimal/halalas.
  • INVOICE_TOTAL_ROUNDING_MISMATCH → needs case-by-case inspection.

The user explicitly asked NOT to default to "payment = قيود total"
without first knowing which bucket the drift comes from.
"""
from __future__ import annotations

from typing import Any, Optional

# Half-a-halala — anything below this is "exactly zero" for the
# purpose of classification. SAR rounding at قيود is 2dp so any real
# difference is >= 0.01 = 1 halala.
EPS = 0.005

# A "real" per-line gap that should count toward the multi-line
# cumulative bucket. Smaller than EPS to surface drift that adds up.
LINE_EPS = 0.001


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _round2(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return round(v, 2)


def _extract_qoyod_invoice_total(inbox_row: dict) -> Optional[float]:
    """قيود returns the invoice total under `body.invoice.total`
    (string or number depending on the resource shape)."""
    responses = inbox_row.get("qoyod_responses") or {}
    inv = ((responses.get("invoice") or {}).get("body") or {})
    if isinstance(inv, dict):
        # `body` is either `{"invoice": {...}}` or flat.
        inner = inv.get("invoice") if isinstance(inv.get("invoice"), dict) else inv
        for k in ("total", "total_amount", "amount", "amount_due"):
            v = _safe_float(inner.get(k))
            if v is not None:
                return v
    return None


def _extract_payment_amount(inbox_row: dict) -> Optional[float]:
    responses = inbox_row.get("qoyod_responses") or {}
    ip = ((responses.get("invoice_payment") or {}).get("body") or {})
    if isinstance(ip, dict):
        inner = ip.get("invoice_payment") if isinstance(
            ip.get("invoice_payment"), dict) else ip
        v = _safe_float(inner.get("amount"))
        if v is not None:
            return v
    # Fallback to what we SENT — useful when قيود's response body
    # didn't echo the amount back.
    payloads = inbox_row.get("qoyod_payloads") or {}
    payload = (payloads.get("invoice_payment") or {})
    if isinstance(payload, dict):
        inner = payload.get("invoice_payment") or payload
        return _safe_float(inner.get("amount"))
    return None


def _extract_mezan_diagnostics(inbox_row: dict) -> dict:
    """The invoice builder stashes per-line diagnostics under
    `qoyod_payloads.invoice_diagnostics`. Returns the dict (or `{}`
    if the row predates the diagnostics block)."""
    payloads = inbox_row.get("qoyod_payloads") or {}
    diag = payloads.get("invoice_diagnostics") or {}
    return diag if isinstance(diag, dict) else {}


def _extract_mezan_computed_total(inbox_row: dict) -> Optional[float]:
    """Mezan's PRE-POST estimate of قيود's invoice total —
    `qoyod_payloads.invoice_diagnostics.expected_qoyod_total`. The
    full per-line breakdown lives under `.line_diagnostics`."""
    diag = _extract_mezan_diagnostics(inbox_row)
    return _safe_float(diag.get("expected_qoyod_total"))


def _extract_line_diagnostics(inbox_row: dict) -> list[dict]:
    diag = _extract_mezan_diagnostics(inbox_row)
    lines = diag.get("line_diagnostics") or []
    return lines if isinstance(lines, list) else []


def _classify_row(row: dict) -> dict:
    """Return a structured diagnostic + bucket tag for one inbox row."""
    canonical    = row.get("canonical_payload") or {}
    salla_total  = _safe_float(canonical.get("total_amount"))
    mezan_total  = _extract_mezan_computed_total(row)
    qoyod_total  = _extract_qoyod_invoice_total(row)
    payment_amt  = _extract_payment_amount(row)

    invoice_diff = (None if qoyod_total is None or salla_total is None
                    else round(qoyod_total - salla_total, 4))
    payment_diff = (None if payment_amt is None or qoyod_total is None
                    else round(payment_amt - qoyod_total, 4))

    diags = _extract_line_diagnostics(row)
    line_diffs = []
    for d in diags:
        salla_target = _safe_float(d.get("salla_total"))
        computed     = _safe_float(d.get("computed_qoyod_gross"))
        line_diff = (None if salla_target is None or computed is None
                     else round(computed - salla_target, 4))
        line_diffs.append({
            "sku":           d.get("sku"),
            "salla_total":   salla_target,
            "computed_gross": computed,
            "line_diff":     line_diff,
            "is_shipping":   (d.get("sku") == "_SHIPPING_"),
        })

    # ── Apply the bucket rules ─────────────────────────────────────
    bucket = "NO_MISMATCH"
    rationale = "كل الأرقام متطابقة."

    if invoice_diff is None and payment_diff is None:
        bucket = "INSUFFICIENT_DATA"
        rationale = ("لا يوجد رد فاتورة من قيود مخزّن للصف — "
                     "قد يكون قديم قبل تفعيل الـ response logging.")
    elif (abs(invoice_diff or 0) <= EPS
          and abs(payment_diff or 0) <= EPS):
        bucket = "NO_MISMATCH"
        rationale = "إجمالي قيود = Salla، والسداد = إجمالي قيود."
    elif abs(invoice_diff or 0) <= EPS and abs(payment_diff or 0) > EPS:
        bucket = "PAYMENT_MISMATCH_ONLY"
        rationale = ("إجمالي قيود مطابق لـ Salla، لكن مبلغ السداد "
                     "المُرسَل مختلف عن إجمالي قيود.")
    elif abs(invoice_diff or 0) > EPS:
        # Drill down by line.
        shipping_offenders = [
            d for d in line_diffs
            if d["is_shipping"]
            and d["line_diff"] is not None
            and abs(d["line_diff"]) > EPS
        ]
        non_shipping_offenders = [
            d for d in line_diffs
            if not d["is_shipping"]
            and d["line_diff"] is not None
            and abs(d["line_diff"]) > EPS
        ]
        cumulative_offenders = [
            d for d in line_diffs
            if d["line_diff"] is not None
            and abs(d["line_diff"]) > LINE_EPS
        ]
        if (shipping_offenders
                and not non_shipping_offenders
                and len(shipping_offenders) >= 1):
            bucket = "SHIPPING_ROUNDING_MISMATCH"
            rationale = ("سطر الشحن يحمل الفارق — Mezan-computed "
                         "vs Salla-target متباعدان.")
        elif (len(non_shipping_offenders) == 1
              and not shipping_offenders):
            bucket = "DISCOUNT_ALLOCATION_MISMATCH"
            sku = non_shipping_offenders[0]["sku"]
            rationale = (f"سطر واحد (SKU={sku}) يحمل كامل الفارق — "
                         "غالباً توزيع الخصم مختلف بين Mezan وقيود.")
        elif len(cumulative_offenders) >= 2:
            bucket = "MULTI_LINE_CUMULATIVE_ROUNDING"
            rationale = (f"الفارق ينتج من تراكم تقريب "
                         f"{len(cumulative_offenders)} سطور.")
        else:
            bucket = "INVOICE_TOTAL_ROUNDING_MISMATCH"
            rationale = ("الفرق موجود لكن لا يمكن تحديد سطر "
                         "بعينه — يحتاج فحص يدوي.")

    return {
        "row_id":        row.get("id"),
        "trace_id":      row.get("trace_id"),
        "order_id":      canonical.get("order_id")
                          or row.get("salla_order_id"),
        "order_number":  canonical.get("order_number"),
        "pipeline_stage": row.get("pipeline_stage"),
        "qoyod_invoice_id":         row.get("qoyod_invoice_id"),
        "qoyod_invoice_payment_id": row.get("qoyod_invoice_payment_id"),
        "salla_total":           salla_total,
        "mezan_computed_total":  mezan_total,
        "qoyod_invoice_total":   qoyod_total,
        "payment_amount_sent":   payment_amt,
        "invoice_diff":          invoice_diff,
        "payment_diff":          payment_diff,
        "line_diffs":            line_diffs,
        "bucket":                bucket,
        "rationale":             rationale,
    }


async def build_rounding_mismatch_report(
    db, *, user_id: str, limit: int = 200,
) -> dict:
    """Scan recent inbox rows, classify each, and return ONLY those
    with a real discrepancy. The summary block lets the UI render a
    quick "how many of each bucket" badge."""
    cursor = db.integration_inbox.find(
        {"user_id": user_id,
         "pipeline_stage": {"$in": [
             "COMPLETED", "PARTIAL_FAILURE",
             "INVOICE_PAYMENT_CREATED", "INVOICE_CREATED",
         ]}},
        {"_id": 0,
         "id": 1, "trace_id": 1, "salla_order_id": 1,
         "pipeline_stage": 1, "pipeline_outcome": 1,
         "qoyod_invoice_id": 1, "qoyod_invoice_payment_id": 1,
         "qoyod_responses.invoice.body": 1,
         "qoyod_responses.invoice_payment.body": 1,
         "qoyod_payloads.invoice_payment": 1,
         "qoyod_payloads.invoice_diagnostics": 1,
         "canonical_payload.total_amount": 1,
         "canonical_payload.order_id": 1,
         "canonical_payload.order_number": 1,
         },
        sort=[("received_at", -1)],
        limit=limit,
    )

    rows = []
    async for r in cursor:
        rows.append(r)

    classified = [_classify_row(r) for r in rows]

    # Operator only cares about rows that have a real drift, OR
    # rows where we couldn't tell because data is missing.
    interesting = [c for c in classified
                   if c["bucket"] not in ("NO_MISMATCH",)]

    # Bucket histogram across ALL scanned rows (so the operator can
    # see how widespread each bucket is).
    by_bucket: dict[str, int] = {}
    for c in classified:
        by_bucket[c["bucket"]] = by_bucket.get(c["bucket"], 0) + 1

    return {
        "ok":             True,
        "scanned_count":  len(classified),
        "mismatch_count": len(interesting),
        "by_bucket":      by_bucket,
        "rows":           interesting,
    }
