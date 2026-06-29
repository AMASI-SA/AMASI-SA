"""Iter-290j-rounding-fix · Phase 1.5 — Richer read-only diagnostic.

Read-only diagnostic that scans `integration_inbox` for invoices whose
money trail diverges anywhere between Salla → Mezan → قيود, and:

  • Classifies each row into ONE of several buckets so the operator
    can see WHERE the halala drift originates BEFORE we change any
    pricing logic.
  • Tags each row with a SEVERITY (minor halala-scale rounding vs a
    real material mismatch like 6 SAR or 18 SAR — those are NOT the
    same problem and shouldn't be lumped together).
  • For INSUFFICIENT_DATA rows, emits a `data_gaps[]` list explaining
    EXACTLY which slice of telemetry is missing (no قيود response, no
    line diagnostics, no canonical items, etc.).
  • Per invoice, returns a richer `lines[]` array that fuses canonical
    item data (qty, unit_price, discount, tax_amount) with what Mezan
    computed and — when available — what قيود's response echoed back.
  • Per invoice, emits a `summary{}` block that calls out which cause
    is most likely so the operator doesn't have to read the table to
    figure it out.

STRICT ZERO-WRITE invariant
───────────────────────────
  • No DB writes.
  • No قيود writes.
  • No mutation of invoice / payment math.
The user has explicitly forbidden applying a Phase-2 fix until we have
this richer telemetry first.
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

# Severity thresholds (per the user's explicit categorisation).
MINOR_MAX     = 0.02   # |diff| ≤ 0.02 → halala-scale rounding noise
MATERIAL_MIN  = 0.05   # |diff| > 0.05 → real material mismatch


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


# ─── Extractors over the raw inbox row ───────────────────────────────
def _extract_qoyod_invoice_inner(inbox_row: dict) -> dict:
    """Return قيود's invoice body inner dict (or `{}`)."""
    responses = inbox_row.get("qoyod_responses") or {}
    inv = ((responses.get("invoice") or {}).get("body") or {})
    if not isinstance(inv, dict):
        return {}
    inner = inv.get("invoice") if isinstance(inv.get("invoice"), dict) else inv
    return inner if isinstance(inner, dict) else {}


def _extract_qoyod_invoice_total(inbox_row: dict) -> Optional[float]:
    inner = _extract_qoyod_invoice_inner(inbox_row)
    for k in ("total", "total_amount", "amount", "amount_due"):
        v = _safe_float(inner.get(k))
        if v is not None:
            return v
    return None


def _extract_qoyod_line_items(inbox_row: dict) -> list[dict]:
    """قيود echoes back the line items it actually computed under
    `body.invoice.line_items`. The shape varies a bit between API
    versions, so we just normalize each entry to {sku, gross}."""
    inner = _extract_qoyod_invoice_inner(inbox_row)
    raw = inner.get("line_items") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for li in raw:
        if not isinstance(li, dict):
            continue
        sku = li.get("sku") or li.get("product_sku") or li.get("description")
        # قيود uses different field names — try the common ones in order.
        gross = None
        for k in ("subtotal_after_taxes", "total_after_tax",
                  "total_with_tax", "total_amount", "line_total",
                  "gross_amount", "total"):
            gross = _safe_float(li.get(k))
            if gross is not None:
                break
        out.append({"sku": sku, "qoyod_line_gross": gross})
    return out


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
    payloads = inbox_row.get("qoyod_payloads") or {}
    diag = payloads.get("invoice_diagnostics") or {}
    return diag if isinstance(diag, dict) else {}


def _extract_mezan_computed_total(inbox_row: dict) -> Optional[float]:
    diag = _extract_mezan_diagnostics(inbox_row)
    return _safe_float(diag.get("expected_qoyod_total"))


def _extract_line_diagnostics(inbox_row: dict) -> list[dict]:
    diag = _extract_mezan_diagnostics(inbox_row)
    lines = diag.get("line_diagnostics") or []
    return lines if isinstance(lines, list) else []


def _extract_canonical_items(inbox_row: dict) -> list[dict]:
    canonical = inbox_row.get("canonical_payload") or {}
    items = canonical.get("items") or []
    return items if isinstance(items, list) else []


# ─── Per-line breakdown (fuses canonical + diagnostics + قيود echo) ──
def _build_lines_breakdown(inbox_row: dict, tax_percent: float) -> list[dict]:
    """Merge canonical_payload.items, line_diagnostics, and قيود's
    echoed line_items into one normalized list. Order follows the
    canonical items list, with the shipping line appended last if
    line_diagnostics has it."""
    canonical_items = _extract_canonical_items(inbox_row)
    diags = _extract_line_diagnostics(inbox_row)
    qoyod_lines = _extract_qoyod_line_items(inbox_row)

    diag_by_sku  = {d.get("sku"): d for d in diags if isinstance(d, dict)}
    qoyod_by_sku = {q.get("sku"): q for q in qoyod_lines
                    if isinstance(q, dict) and q.get("sku")}

    canonical_payload = inbox_row.get("canonical_payload") or {}
    shipping_amount = _safe_float(canonical_payload.get("shipping_amount")) or 0.0

    out: list[dict] = []

    # If canonical items aren't projected/captured (older rows or
    # tests that only set diagnostics), synthesize lines from
    # line_diagnostics so the classifier still has structural data
    # to drill into.
    if not canonical_items and diags:
        for d in diags:
            sku = d.get("sku")
            if not isinstance(sku, str):
                continue
            is_shipping = sku.startswith("_SHIPPING_")
            if is_shipping:
                continue   # shipping handled below
            salla_target = _safe_float(d.get("salla_total"))
            mezan_gross  = _safe_float(d.get("computed_qoyod_gross"))
            q = qoyod_by_sku.get(sku) or {}
            line_diff = (None if mezan_gross is None or salla_target is None
                         else round(mezan_gross - salla_target, 4))
            out.append({
                "kind":              "product",
                "sku":               sku,
                "name":              None,
                "quantity":          None,
                "unit_price":        None,
                "discount_amount":   None,
                "tax_amount":        None,
                "tax_percent":       tax_percent,
                "salla_target_gross": salla_target,
                "mezan_computed_gross": mezan_gross,
                "qoyod_line_gross":  q.get("qoyod_line_gross"),
                "line_diff":         line_diff,
                "fallback_used":     bool(d.get("fallback_used")),
            })

    for it in canonical_items:
        sku = it.get("sku")
        qty = _safe_float(it.get("quantity"))
        unit_price = _safe_float(it.get("unit_price"))
        discount = _safe_float(it.get("discount_amount"))
        tax_amount = _safe_float(it.get("tax_amount"))
        salla_total = _safe_float(it.get("total"))

        d = diag_by_sku.get(sku) or {}
        mezan_gross = _safe_float(d.get("computed_qoyod_gross"))

        q = qoyod_by_sku.get(sku) or {}
        qoyod_gross = q.get("qoyod_line_gross")

        # line_diff is Mezan vs Salla — that's what the bucket logic
        # uses. If Mezan-computed is missing we leave it as null.
        line_diff = (None if mezan_gross is None or salla_total is None
                     else round(mezan_gross - salla_total, 4))

        out.append({
            "kind":              "product",
            "sku":               sku,
            "name":              it.get("name"),
            "quantity":          qty,
            "unit_price":        unit_price,
            "discount_amount":   discount,
            "tax_amount":        tax_amount,
            "tax_percent":       tax_percent,
            "salla_target_gross": salla_total,
            "mezan_computed_gross": mezan_gross,
            "qoyod_line_gross":  qoyod_gross,
            "line_diff":         line_diff,
            "fallback_used":     bool(d.get("fallback_used")),
        })

    # Shipping line — from diagnostics (canonical doesn't carry it as
    # an item; it's a top-level `shipping_amount` field).
    shipping_diag = diag_by_sku.get("_SHIPPING_") \
                 or diag_by_sku.get("_SHIPPING_MISSING_PRODUCT_ID_")
    if shipping_diag:
        salla_target = _safe_float(shipping_diag.get("salla_total"))
        mezan_gross  = _safe_float(shipping_diag.get("computed_qoyod_gross"))
        line_diff = (None if salla_target is None or mezan_gross is None
                     else round(mezan_gross - salla_target, 4))
        out.append({
            "kind":              "shipping",
            "sku":               shipping_diag.get("sku"),
            "name":              "شحن",
            "quantity":          1,
            "unit_price":        shipping_amount or None,
            "discount_amount":   None,
            "tax_amount":        None,
            "tax_percent":       tax_percent,
            "salla_target_gross": salla_target,
            "mezan_computed_gross": mezan_gross,
            "qoyod_line_gross":  None,
            "line_diff":         line_diff,
            "fallback_used":     bool(shipping_diag.get("fallback_used")),
        })

    return out


# ─── Severity ────────────────────────────────────────────────────────
def _severity_from_diff(invoice_diff: Optional[float]) -> str:
    """User explicitly asked to separate halala-scale noise from real
    material mismatches. 6.24 / 18.84 SAR drifts are NOT rounding —
    they need their own bucket so they don't get "fixed" by a 0.01
    payment override patch."""
    if invoice_diff is None:
        return "UNKNOWN"
    a = abs(invoice_diff)
    if a <= MINOR_MAX:
        return "MINOR_ROUNDING"
    if a > MATERIAL_MIN:
        return "MATERIAL_MISMATCH"
    return "MODERATE_DRIFT"


# ─── Data-gap reasoning for INSUFFICIENT_DATA rows ───────────────────
def _detect_data_gaps(row: dict) -> list[str]:
    """Return a list of specific reasons why a row has insufficient
    data. Order is roughly "most actionable first"."""
    gaps: list[str] = []
    responses = row.get("qoyod_responses") or {}
    payloads  = row.get("qoyod_payloads") or {}
    canonical = row.get("canonical_payload") or {}

    if not row.get("qoyod_invoice_id"):
        gaps.append("no_qoyod_invoice_id")

    inv_resp = (responses.get("invoice") or {}).get("body")
    if not inv_resp:
        gaps.append("no_invoice_response")

    pay_resp = (responses.get("invoice_payment") or {}).get("body")
    pay_payload = (payloads.get("invoice_payment") or {})
    if not pay_resp and not pay_payload:
        gaps.append("no_payment_response")

    if not (payloads.get("invoice_diagnostics") or {}).get("line_diagnostics"):
        gaps.append("no_line_diagnostics")

    if not canonical.get("items"):
        gaps.append("no_canonical_items")

    # "Pre-logging row" — heuristic. If we have a qoyod_invoice_id but
    # no diagnostics AND no قيود body, it's almost certainly a row
    # that was processed before we started capturing payloads.
    if (row.get("qoyod_invoice_id")
        and "no_invoice_response" in gaps
        and "no_line_diagnostics" in gaps):
        gaps.append("pre_logging_row")

    return gaps


# ─── Core classifier ─────────────────────────────────────────────────
def _classify_row(row: dict) -> dict:
    """Return a structured diagnostic + bucket + severity tag for one
    inbox row. Pure function — no DB / no IO."""
    canonical    = row.get("canonical_payload") or {}
    salla_total  = _safe_float(canonical.get("total_amount"))
    mezan_total  = _extract_mezan_computed_total(row)
    qoyod_total  = _extract_qoyod_invoice_total(row)
    payment_amt  = _extract_payment_amount(row)

    invoice_diff = (None if qoyod_total is None or salla_total is None
                    else round(qoyod_total - salla_total, 4))
    payment_diff = (None if payment_amt is None or qoyod_total is None
                    else round(payment_amt - qoyod_total, 4))

    diag = _extract_mezan_diagnostics(row)
    tax_percent = _safe_float(diag.get("qoyod_tax_percent_used")) or 15.0

    lines = _build_lines_breakdown(row, tax_percent)

    # Helper lists scoped over the merged lines.
    shipping_offenders = [
        ld for ld in lines
        if ld["kind"] == "shipping"
        and ld["line_diff"] is not None
        and abs(ld["line_diff"]) > EPS
    ]
    non_shipping_offenders = [
        ld for ld in lines
        if ld["kind"] != "shipping"
        and ld["line_diff"] is not None
        and abs(ld["line_diff"]) > EPS
    ]
    cumulative_offenders = [
        ld for ld in lines
        if ld["line_diff"] is not None
        and abs(ld["line_diff"]) > LINE_EPS
    ]

    # ── Bucket rules ────────────────────────────────────────────────
    bucket = "NO_MISMATCH"
    rationale = "كل الأرقام متطابقة."
    data_gaps: list[str] = []

    if invoice_diff is None and payment_diff is None:
        bucket = "INSUFFICIENT_DATA"
        data_gaps = _detect_data_gaps(row)
        rationale = ("لا يمكن حساب الفرق — راجع `data_gaps` لمعرفة "
                     "أي شريحة بيانات مفقودة.")
    elif (abs(invoice_diff or 0) <= EPS
          and abs(payment_diff or 0) <= EPS):
        bucket = "NO_MISMATCH"
        rationale = "إجمالي قيود = Salla، والسداد = إجمالي قيود."
    elif abs(invoice_diff or 0) <= EPS and abs(payment_diff or 0) > EPS:
        bucket = "PAYMENT_MISMATCH_ONLY"
        rationale = ("إجمالي قيود مطابق لـ Salla، لكن مبلغ السداد "
                     "المُرسَل مختلف عن إجمالي قيود.")
    elif abs(invoice_diff or 0) > EPS:
        # Drill down by line. We classify in priority order:
        #   1) Shipping is the sole offender              → SHIPPING
        #   2) Exactly one product line is the offender   → DISCOUNT
        #   3) ≥2 lines drift                             → MULTI_LINE
        #   4) Mezan thinks it tied but قيود disagrees    → QOYOD_SERVER
        #   5) Otherwise                                  → catch-all
        if (shipping_offenders
                and not non_shipping_offenders):
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
        elif (mezan_total is not None
              and salla_total is not None
              and abs(mezan_total - salla_total) <= EPS
              and not cumulative_offenders):
            # Mezan's pre-POST estimate matched Salla exactly, but
            # قيود's POST-POST total drifted — that means قيود's own
            # rounding logic differs from ours. This was the silent
            # catch-all before Iter-290j Phase 1.5.
            bucket = "QOYOD_SERVER_SIDE_ROUNDING"
            rationale = ("ميزان حسبت الإجمالي مطابقاً لسلة، لكن قيود "
                         "أعاد حسابه بقيمة مختلفة — الاختلاف من منطق "
                         "تقريب قيود الداخلي وليس من بياناتنا.")
        else:
            bucket = "INVOICE_TOTAL_ROUNDING_MISMATCH"
            rationale = ("الفرق موجود لكن لا يمكن تحديد سطر "
                         "بعينه — يحتاج فحص يدوي.")

    severity = _severity_from_diff(invoice_diff)

    # ── Per-invoice summary ─────────────────────────────────────────
    # The user asked for a human-readable cause summary that doesn't
    # require the operator to mentally re-derive it from the table.
    shipping_contrib = sum(
        (ld["line_diff"] or 0.0) for ld in shipping_offenders)
    non_shipping_contrib = sum(
        (ld["line_diff"] or 0.0) for ld in non_shipping_offenders)
    largest = None
    if cumulative_offenders:
        worst = max(cumulative_offenders,
                    key=lambda d: abs(d["line_diff"] or 0))
        largest = {"sku": worst["sku"],
                   "kind": worst["kind"],
                   "line_diff": worst["line_diff"]}

    if bucket == "INSUFFICIENT_DATA":
        primary_cause = "insufficient_data"
    elif bucket == "NO_MISMATCH":
        primary_cause = "none"
    elif bucket == "PAYMENT_MISMATCH_ONLY":
        primary_cause = "payment_only"
    elif bucket == "SHIPPING_ROUNDING_MISMATCH":
        primary_cause = "shipping_line"
    elif bucket == "DISCOUNT_ALLOCATION_MISMATCH":
        primary_cause = "single_product_line"
    elif bucket == "MULTI_LINE_CUMULATIVE_ROUNDING":
        primary_cause = "multi_line_cumulative"
    elif bucket == "QOYOD_SERVER_SIDE_ROUNDING":
        primary_cause = "qoyod_server_rounding"
    else:
        primary_cause = "unclassified"

    summary = {
        "primary_cause":              primary_cause,
        "is_single_line_cause":       (len(cumulative_offenders) == 1),
        "is_shipping_cause":          (len(shipping_offenders) >= 1
                                       and len(non_shipping_offenders) == 0),
        "is_discount_allocation":     (bucket == "DISCOUNT_ALLOCATION_MISMATCH"),
        "is_multi_line_cumulative":   (bucket == "MULTI_LINE_CUMULATIVE_ROUNDING"),
        "is_qoyod_server_rounding":   (bucket == "QOYOD_SERVER_SIDE_ROUNDING"),
        "offender_count":             len(cumulative_offenders),
        "shipping_contribution":      round(shipping_contrib, 4)
                                      if shipping_offenders else None,
        "non_shipping_contribution":  round(non_shipping_contrib, 4)
                                      if non_shipping_offenders else None,
        "largest_offender":           largest,
    }

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
        "tax_percent":           tax_percent,
        # New richer line table (replaces the older `line_diffs`).
        "lines":                 lines,
        # Kept for backwards compat with any caller still reading the
        # legacy field name (the unit tests / older UI snapshot).
        "line_diffs":            [{
            "sku":           ld["sku"],
            "salla_total":   ld["salla_target_gross"],
            "computed_gross": ld["mezan_computed_gross"],
            "line_diff":     ld["line_diff"],
            "is_shipping":   ld["kind"] == "shipping",
        } for ld in lines],
        "bucket":                bucket,
        "severity":              severity,
        "data_gaps":             data_gaps,
        "rationale":             rationale,
        "summary":               summary,
    }


async def build_rounding_mismatch_report(
    db, *, user_id: str, limit: int = 200,
) -> dict:
    """Scan recent inbox rows, classify each, and return ONLY those
    with a real discrepancy. The summary block lets the UI render
    badges for bucket, severity, and data-gap reasons."""
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
         "canonical_payload.total_amount":   1,
         "canonical_payload.order_id":       1,
         "canonical_payload.order_number":   1,
         "canonical_payload.shipping_amount": 1,
         "canonical_payload.items":          1,
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

    # Bucket histogram across ALL scanned rows.
    by_bucket: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_gap_reason: dict[str, int] = {}
    for c in classified:
        by_bucket[c["bucket"]] = by_bucket.get(c["bucket"], 0) + 1
        # Severity only counted on rows that have a real mismatch
        # (no point counting MINOR_ROUNDING for clean rows).
        if c["bucket"] not in ("NO_MISMATCH", "INSUFFICIENT_DATA"):
            by_severity[c["severity"]] = by_severity.get(c["severity"], 0) + 1
        for gap in c.get("data_gaps") or []:
            by_gap_reason[gap] = by_gap_reason.get(gap, 0) + 1

    return {
        "ok":             True,
        "scanned_count":  len(classified),
        "mismatch_count": len(interesting),
        "by_bucket":      by_bucket,
        "by_severity":    by_severity,
        "by_gap_reason":  by_gap_reason,
        "rows":           interesting,
    }
