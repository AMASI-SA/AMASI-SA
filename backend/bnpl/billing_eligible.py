"""Iter-146 — Tamara billing-eligibility tracking.

Tamara settles each order in the **week that the order first becomes
operationally ready** (shipped / prepared / delivered…), NOT in the
week the order was created.  Filtering by `created_at_provider` makes
our weekly invoice diverge from Tamara's official settlement statement
whenever fulfilment lags into the next week.

This module owns:

  • `BILLABLE_STATUSES` — the canonical Arabic statuses (plus English
    aliases) that flip an order into Tamara's billing cycle.
  • `is_billable_status(status)` — boolean check.
  • `mark_billing_eligible_for_order(...)` — sets
    `payment_transactions.billing_eligible_at` on every Tamara row tied
    to the given order reference, **only if the field is not already
    populated** (idempotent — first billable transition wins).

Refunds intentionally keep their existing `refunded_at` aggregation
(Iter-120 rule).  This module touches sales only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Canonical billable statuses per merchant requirement.  Anything that
# represents "the goods are on their way / handed off / delivered" — i.e.,
# the merchant has done the work and Tamara has acknowledged it.
BILLABLE_STATUSES: frozenset[str] = frozenset({
    # Arabic (Salla / Make)
    "تم التنفيذ",
    "جاري التوصيل",
    "تم التوصيل",
    "تم التجهيز",
    "تم الشحن",
    # English aliases (Tamara API / custom_app)
    "delivered",
    "completed",
    "shipped",
    "out_for_delivery",
    "processing",        # تم التجهيز
    "fulfilled",
    "fully_captured",    # Tamara API status (capture = goods released)
})


def is_billable_status(status: Optional[str]) -> bool:
    """Return True if the given order_status marks the order as eligible
    to enter Tamara's weekly settlement cycle."""
    if not status:
        return False
    s = str(status).strip()
    if not s:
        return False
    if s in BILLABLE_STATUSES:
        return True
    return s.lower() in BILLABLE_STATUSES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def mark_billing_eligible_for_order(
    db,
    user_id: str,
    *,
    order_reference_id: Optional[str] = None,
    order_number: Optional[str] = None,
    event_at: Optional[str] = None,
    provider: str = "tamara",
) -> Dict[str, Any]:
    """Idempotently stamp `billing_eligible_at` on every payment_transactions
    row of `provider` that matches this order reference.

    `event_at` should be the ISO timestamp of the status transition.  When
    omitted we fall back to "now (UTC)" — acceptable because Tamara cares
    about the **week** the transition fell into, not the precise second.

    Returns a small stats dict for diagnostics / audit.
    """
    refs: list[str] = [r for r in (order_reference_id, order_number) if r]
    if not refs:
        return {"matched": 0, "updated": 0, "reason": "no order reference"}

    stamp = event_at or _now_iso()

    # Match by either order_reference_id OR order_number (both columns
    # exist on payment_transactions; some providers fill only one).
    or_clause: list[Dict[str, Any]] = []
    for r in refs:
        or_clause.append({"order_reference_id": r})
        or_clause.append({"order_number": r})

    query = {
        "user_id": user_id,
        "provider": provider,
        "$or": or_clause,
        # CRITICAL — never overwrite an already-stamped row.  The first
        # billable status the order reached owns the settlement week.
        "$and": [
            {"$or": [
                {"billing_eligible_at": {"$exists": False}},
                {"billing_eligible_at": None},
                {"billing_eligible_at": ""},
            ]},
        ],
    }

    matched = await db.payment_transactions.count_documents({
        "user_id": user_id,
        "provider": provider,
        "$or": or_clause,
    })
    res = await db.payment_transactions.update_many(
        query, {"$set": {"billing_eligible_at": stamp}},
    )

    # Iter-147 — refresh attribution (effective_settlement_date /
    # settlement_source) on every row we just stamped, but ONLY for
    # tamara (the only provider that uses the priority system today).
    recomputed = 0
    if provider == "tamara" and int(getattr(res, "modified_count", 0) or 0) > 0:
        from .settlement_attribution import recompute_attribution_for_doc
        # Walk the touched rows and recompute one-by-one.
        async for d in db.payment_transactions.find(
            {"user_id": user_id, "provider": provider, "$or": or_clause},
            {"_id": 0, "id": 1},
        ):
            r = await recompute_attribution_for_doc(
                db, user_id=user_id, txn_id=d.get("id"),
            )
            if r.get("updated"):
                recomputed += 1

    return {
        "matched": matched,
        "updated": int(getattr(res, "modified_count", 0) or 0),
        "stamp":   stamp,
        "attribution_recomputed": recomputed,
    }


async def propagate_status_to_billing_eligible(
    db,
    user_id: str,
    *,
    order_reference_id: Optional[str] = None,
    order_number: Optional[str] = None,
    new_status: Optional[str],
    event_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience helper called from every order-status write site
    (custom_app, Make webhook, Salla sync, Tamara webhook).

    No-ops when:
      • new_status is None / empty / non-billable.
      • neither order_reference_id nor order_number is supplied.

    On a billable transition, calls `mark_billing_eligible_for_order`
    which is itself idempotent (first stamp wins)."""
    if not is_billable_status(new_status):
        return {"matched": 0, "updated": 0, "reason": "status not billable"}
    return await mark_billing_eligible_for_order(
        db, user_id,
        order_reference_id=order_reference_id,
        order_number=order_number,
        event_at=event_at,
        provider="tamara",
    )
