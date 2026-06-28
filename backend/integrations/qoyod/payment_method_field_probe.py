"""Iter-290h.7 — Read-only diagnostic for the empty `payment_method`
field in قيود invoice headers.

Why this exists
───────────────
After Iter-290h shipped, invoices created via Mezan show as **paid**
in قيود (correct accounting) but the **payment_method** column in
the invoice header is empty. Manual / legacy invoices created from
قيود's own UI DO show a payment method. The wire field that drives
this column is unknown.

This module exposes a strictly READ-ONLY probe:

    POST /api/integrations/qoyod/admin/payment-method-field-probe
    {
      "empty_payment_method_invoice_id":     "<id of the new invoice>",
      "reference_invoice_id_with_payment":   "<id of a working one>"
    }

It calls `GET /invoices/{id}` on both invoices and returns:
  • both raw JSON bodies
  • candidate field names found in either response (any key that
    mentions `payment`, `method`, `term`, `mode`, `way`)
  • the values for those fields in each invoice — so the operator
    can see, at a glance, which key carries the payment-method label
    on the reference invoice and is empty on ours.

Nothing in this module POSTs, PATCHes, PUTs, or DELETEs against قيود.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.credentials import get_api_key


# Keys that might describe the payment method on the invoice header.
# We err on the side of recall — false positives are harmless; the
# operator gets to see every candidate and pick the canonical one.
_FIELD_KEYWORDS_RE = re.compile(
    r"(payment|method|term|mode|way|tender|cash|bank|wallet|gateway|channel)",
    re.IGNORECASE,
)


def _walk_dict(obj: Any, *, path: str = "", out: Optional[list] = None) -> list:
    """Flatten a nested dict into (dotted_path, value) tuples — used
    so we can match field names even when they live under a nested
    object (e.g. `details.payment_method`)."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                _walk_dict(v, path=sub, out=out)
            else:
                out.append((sub, v))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            sub = f"{path}[{i}]"
            if isinstance(v, (dict, list)):
                _walk_dict(v, path=sub, out=out)
            else:
                out.append((sub, v))
    else:
        out.append((path, obj))
    return out


def _candidate_payment_fields(doc: Any) -> dict[str, Any]:
    """Return {dotted_path: value} for every leaf key whose name
    looks like it could be the payment-method field on the invoice
    header. Excludes deeply nested ARRAY entries (like
    `allocations[0].account_id`) which describe receipts, not the
    invoice header."""
    if not isinstance(doc, dict):
        return {}
    inner = doc.get("invoice") if isinstance(doc.get("invoice"), dict) else doc
    matches: dict[str, Any] = {}
    for path, value in _walk_dict(inner):
        # Top-level / one-level-deep keys only — keeps the diff
        # focused on the invoice HEADER and avoids noise from nested
        # collections (line items, allocations).
        if path.count(".") <= 1 and "[" not in path:
            last = path.rsplit(".", 1)[-1]
            if _FIELD_KEYWORDS_RE.search(last):
                matches[path] = value
    return matches


def _all_top_level_keys(doc: Any) -> list[str]:
    """List the top-level keys on the invoice header — helps spot
    fields the reference invoice has but our invoice lacks."""
    if not isinstance(doc, dict):
        return []
    inner = doc.get("invoice") if isinstance(doc.get("invoice"), dict) else doc
    if not isinstance(inner, dict):
        return []
    return sorted(inner.keys())


async def probe_payment_method_field(
    db, *, user_id: str,
    empty_payment_method_invoice_id: str,
    reference_invoice_id_with_payment: str,
) -> dict:
    """Iter-290h.7 — Fetch both invoices via GET and return a
    side-by-side comparison so the operator can identify the
    canonical wire field for the payment-method column.

    Returns a structured dict with:

      ok                                  bool
      empty_invoice                       dict — raw قيود response
      reference_invoice                   dict — raw قيود response
      candidate_fields_empty_invoice      {path: value}
      candidate_fields_reference_invoice  {path: value}
      keys_only_in_reference              list[str]
      summary                             str (Arabic operator hint)

    Side-effects: NONE. Two GET requests against قيود, no writes.
    """
    api_key = await get_api_key(db, user_id)
    if not api_key:
        return {
            "ok": False,
            "code": "qoyod_api_key_missing",
            "message": "مفتاح API قيود غير مُهيّأ في الإعدادات.",
        }
    api = QoyodAPIClient(api_key)

    def _coerce_id(value: str) -> str:
        return str(value).strip()

    empty_id = _coerce_id(empty_payment_method_invoice_id)
    ref_id   = _coerce_id(reference_invoice_id_with_payment)
    if not empty_id or not ref_id:
        return {
            "ok": False,
            "code": "missing_invoice_ids",
            "message": ("يجب تحديد رقم الفاتورة ذات طريقة الدفع الفارغة "
                        "ورقم فاتورة مرجعية تظهر فيها طريقة الدفع."),
        }
    if empty_id == ref_id:
        return {
            "ok": False,
            "code": "same_invoice_id",
            "message": "يجب أن تكون الفاتورتان مختلفتين للمقارنة.",
        }

    fetch_results: dict[str, Any] = {
        "empty_invoice":     None,
        "reference_invoice": None,
        "fetch_errors":      {},
    }
    for slot, inv_id in (("empty_invoice", empty_id),
                         ("reference_invoice", ref_id)):
        try:
            fetch_results[slot] = await api.get_invoice(inv_id)
        except QoyodAPIError as exc:
            fetch_results["fetch_errors"][slot] = exc.to_log_dict()

    empty_doc = fetch_results["empty_invoice"]
    ref_doc   = fetch_results["reference_invoice"]

    empty_candidates = _candidate_payment_fields(empty_doc)
    ref_candidates   = _candidate_payment_fields(ref_doc)
    empty_top_keys = set(_all_top_level_keys(empty_doc))
    ref_top_keys   = set(_all_top_level_keys(ref_doc))
    keys_only_in_reference = sorted(ref_top_keys - empty_top_keys)
    keys_only_in_empty     = sorted(empty_top_keys - ref_top_keys)

    # Hint generation — purely advisory.
    hint_lines = []
    if not ref_doc and not empty_doc:
        hint_lines.append("تعذّر جلب الفاتورتين من قيود. راجع `fetch_errors`.")
    else:
        if not ref_doc:
            hint_lines.append(
                "لم تُجلَب الفاتورة المرجعية — لا يمكن المقارنة. "
                "تحقق من رقم الفاتورة المرجعية.")
        if not empty_doc:
            hint_lines.append(
                "لم تُجلَب الفاتورة الفارغة — تحقق من رقم الفاتورة.")
        if ref_candidates and empty_candidates:
            # Find fields where reference has a non-empty value but
            # our invoice has empty/missing.
            divergent = []
            for path, ref_val in ref_candidates.items():
                empty_val = empty_candidates.get(path, "<missing>")
                if ref_val not in (None, "", []) and (
                        empty_val in (None, "", "<missing>")):
                    divergent.append((path, ref_val, empty_val))
            if divergent:
                hint_lines.append(
                    "حقول مرشحة موجودة في الفاتورة المرجعية وفارغة لدينا:")
                for path, ref_val, empty_val in divergent:
                    hint_lines.append(
                        f"  • `{path}`: مرجع=‘{ref_val}’ — لدينا=‘{empty_val}’")
            else:
                hint_lines.append(
                    "كل الحقول المرشحة لدينا تطابق المرجع. الحقل قد "
                    "يكون مشتقاً عند قيود من حقل آخر (مثل allocations) "
                    "أو غير قابل للإرسال عبر API.")
        if keys_only_in_reference:
            hint_lines.append(
                f"مفاتيح موجودة في المرجع فقط: "
                f"{', '.join(keys_only_in_reference)}")
    summary = "\n".join(hint_lines) or "—"

    return {
        "ok": True,
        "empty_invoice_id":     empty_id,
        "reference_invoice_id": ref_id,
        "empty_invoice":        empty_doc,
        "reference_invoice":    ref_doc,
        "fetch_errors":         fetch_results["fetch_errors"] or None,
        "candidate_fields_empty_invoice":     empty_candidates,
        "candidate_fields_reference_invoice": ref_candidates,
        "keys_only_in_reference":             keys_only_in_reference,
        "keys_only_in_empty":                 keys_only_in_empty,
        "summary":                            summary,
    }
