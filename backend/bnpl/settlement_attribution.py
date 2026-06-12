"""Iter-147 — Tamara settlement attribution priority.

Source-of-truth for "which weekly invoice does THIS order belong to?"

Priority (highest → lowest):

    1. provider_official      — set when an imported Tamara settlement
                                file mapped the order to a specific
                                settlement_id / invoice_id / date.
                                Field: `provider_settlement_date`.
    2. billing_eligible       — set when the order first reached a
                                billable status (Iter-146).
                                Field: `billing_eligible_at`.
    3. estimated              — last-resort fallback to the order's
                                `created_at_provider`.  Flagged so the
                                UI can show ⚠ "تقديري" beside the row.

The computed `effective_settlement_date` field is what
`settlements_service` aggregates by for Tamara — never the raw
`created_at_provider`.

Helpers in this module:

  • `compute_attribution(doc)` — pure function returning
    `(effective_settlement_date, settlement_source)` for a txn dict.
  • `recompute_attribution_for_order(...)` — re-derive after any write.
  • `set_provider_official_attribution(...)` — called by the Tamara
    settlement-file importer.

We DO NOT touch the upstream raw fields (`created_at_provider`,
`billing_eligible_at`).  Those remain audit-friendly evidence.  Only
the `effective_*` + `settlement_source` columns are derived.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


SETTLEMENT_SOURCE_OFFICIAL  = "provider_official"
SETTLEMENT_SOURCE_BILLING   = "billing_eligible"
SETTLEMENT_SOURCE_ESTIMATED = "estimated"


def _norm_date(v: Any) -> Optional[str]:
    """Return ISO YYYY-MM-DD (or full ISO timestamp) if non-empty."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def compute_attribution(doc: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Apply the priority rule and return the pair
    `(effective_settlement_date, settlement_source)`.

    Returns `(None, 'estimated')` when even `created_at_provider` is
    missing — caller should NOT write the row in that case.
    """
    # 1. provider_official — any of these wins.
    psd = _norm_date(doc.get("provider_settlement_date"))
    if psd or doc.get("provider_settlement_id") or doc.get("provider_invoice_id"):
        # Prefer the actual settlement DATE if present, else payout_date,
        # else fall back to billing_eligible_at as a date proxy.
        effective = (
            psd
            or _norm_date(doc.get("provider_payout_date"))
            or _norm_date(doc.get("billing_eligible_at"))
            or _norm_date(doc.get("created_at_provider"))
        )
        return effective, SETTLEMENT_SOURCE_OFFICIAL

    # 2. billing_eligible — from Iter-146 status transition.
    be = _norm_date(doc.get("billing_eligible_at"))
    if be:
        return be, SETTLEMENT_SOURCE_BILLING

    # 3. estimated — last resort.
    return _norm_date(doc.get("created_at_provider")), SETTLEMENT_SOURCE_ESTIMATED


async def recompute_attribution_for_doc(
    db, *, user_id: str, txn_id: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-derive (effective_settlement_date, settlement_source) and
    write them onto the matched payment_transactions row.  Logs every
    transition to `tamara_attribution_log` for audit when the source
    changes (e.g., estimated → provider_official).

    Identify the row by either internal `id` or by `provider_id`.
    """
    if not (txn_id or provider_id):
        return {"updated": 0, "reason": "no key"}

    q: Dict[str, Any] = {"user_id": user_id, "provider": "tamara"}
    if txn_id:
        q["id"] = txn_id
    else:
        q["provider_id"] = provider_id

    doc = await db.payment_transactions.find_one(q)
    if not doc:
        return {"updated": 0, "reason": "not found"}

    new_eff, new_src = compute_attribution(doc)
    old_eff = doc.get("effective_settlement_date")
    old_src = doc.get("settlement_source")

    if new_eff == old_eff and new_src == old_src:
        return {"updated": 0, "reason": "unchanged",
                "effective_settlement_date": new_eff,
                "settlement_source":         new_src}

    await db.payment_transactions.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "effective_settlement_date": new_eff,
            "settlement_source":         new_src,
        }},
    )

    # Audit transitions so the merchant can see e.g. "row moved from
    # estimated → provider_official on 2026-06-12 (Δ +3 days)".
    await db.tamara_attribution_log.insert_one({
        "user_id":            user_id,
        "txn_id":             doc.get("id"),
        "provider_id":        doc.get("provider_id"),
        "order_reference_id": doc.get("order_reference_id"),
        "old_source":         old_src,
        "new_source":         new_src,
        "old_effective":      old_eff,
        "new_effective":      new_eff,
        "at":                 datetime.now(timezone.utc).isoformat(),
    })

    return {
        "updated": 1,
        "old_effective": old_eff,
        "new_effective": new_eff,
        "old_source":    old_src,
        "new_source":    new_src,
    }


async def set_provider_official_attribution(
    db,
    user_id: str,
    *,
    order_reference_id: Optional[str] = None,
    order_number: Optional[str] = None,
    provider_id: Optional[str] = None,
    provider_settlement_id: Optional[str] = None,
    provider_invoice_id: Optional[str] = None,
    provider_settlement_date: Optional[str] = None,
    provider_payout_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Stamp the official Tamara settlement fields on every matching
    Tamara payment_transactions row, then recompute attribution.

    `provider_settlement_date` is the merchant-relevant cutoff (the
    settlement statement date).  Used to group orders into the correct
    weekly invoice in `settlements_service`.

    First call always wins for `provider_settlement_*` (no clobbering
    of an earlier official attribution).
    """
    refs = [r for r in (order_reference_id, order_number, provider_id) if r]
    if not refs:
        return {"matched": 0, "updated": 0, "reason": "no key"}

    or_clause: list[Dict[str, Any]] = []
    for r in refs:
        or_clause.append({"order_reference_id": r})
        or_clause.append({"order_number": r})
        or_clause.append({"provider_id": r})

    base_q = {"user_id": user_id, "provider": "tamara", "$or": or_clause}

    set_fields: Dict[str, Any] = {}
    if provider_settlement_id:
        set_fields["provider_settlement_id"] = str(provider_settlement_id)
    if provider_invoice_id:
        set_fields["provider_invoice_id"] = str(provider_invoice_id)
    if provider_settlement_date:
        set_fields["provider_settlement_date"] = str(provider_settlement_date)
    if provider_payout_date:
        set_fields["provider_payout_date"] = str(provider_payout_date)

    if not set_fields:
        return {"matched": 0, "updated": 0, "reason": "nothing to set"}

    # We use $setOnInsert-like semantics manually: only fields that are
    # NOT yet set (or empty) get overwritten.  We do this per-field via
    # an aggregation pipeline so we never clobber a stronger prior
    # provider_official attribution.
    pipeline_set = {
        k: {
            "$cond": [
                {"$or": [
                    {"$eq": [{"$ifNull": [f"${k}", ""]}, ""]},
                    {"$eq": [f"${k}", None]},
                ]},
                v,
                f"${k}",
            ]
        }
        for k, v in set_fields.items()
    }

    matched = await db.payment_transactions.count_documents(base_q)
    res = await db.payment_transactions.update_many(
        base_q, [{"$set": pipeline_set}],
    )

    # Recompute attribution for every affected row.
    recomputed = 0
    async for d in db.payment_transactions.find(base_q, {"_id": 0, "id": 1}):
        r = await recompute_attribution_for_doc(
            db, user_id=user_id, txn_id=d.get("id"),
        )
        if r.get("updated"):
            recomputed += 1

    return {
        "matched":    matched,
        "updated":    int(getattr(res, "modified_count", 0) or 0),
        "recomputed": recomputed,
    }
