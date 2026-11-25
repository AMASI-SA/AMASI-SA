"""Read-only Salla paid-order page evidence adapter (NO financial posting).

This legacy module name is retained for the existing webhook inbox import.
A cumulative paid amount and transaction reference DO NOT prove original
captured_at. The canonical provider date policy forbids a new receivable
without the dated native capture. Therefore this module ONLY classifies
current native Salla paid-page evidence for the owner's accountant-review
inbox, never touches operational provider obligations, bank balances or GL.

The actual first-payout CAS writer is operational_payment_first_capture_writer
and requires a verified original transaction timestamp.
"""
from __future__ import annotations

import os
from operational_payment_salla_order_page import native_paid_order_page

PAGE_POST_FLAG = "OPERATIONAL_PAYMENT_ORDER_PAGE_POST_ENABLED"
PROJECTION = {
    "_id":0, "user_id":1, "order_number":1,
    "raw_by_source.salla_direct":1, "g47_salla_snapshot":1,
    "salla_order_id":1, "order_id":1, "order_status_slug":1,
}


def enabled():
    """Flag controls review discovery only; never allows page money posting."""
    return os.environ.get(PAGE_POST_FLAG) == "1"


async def _canonical_source(db, owner, order_number):
    record = await db.unified_orders.find_one(
        {"user_id": owner, "order_number": str(order_number)}, PROJECTION)
    if not isinstance(record, dict) or record.get("user_id") != owner:
        return None
    raw = (record.get("raw_by_source") or {}).get("salla_direct")
    if (not isinstance(raw, dict)
            or str(raw.get("reference_id") or raw.get("order_number") or "")
               != str(order_number)):
        return None
    return record, raw


async def post_first_paid_page(db, owner, merchant_id, order_number, *,
                               source_ref, clock=None):
    """No-money classification; original capture time must come from Salla.

    A signed source event's owner is independently checked before reading the
    owner-scoped current Salla order snapshot. A successful paid page is
    forwarded as a review-needed observation only. There is no CAS financial
    write and no fallback to order.created_at or updated_at as capture time.
    """
    if not enabled():
        return {"status":"posting_disabled"}
    from operational_balance_store import fail
    from salla_integration.webhook_order_sync import _resolve_user_id

    if not all(type(v) is str and v.strip() for v in
               (owner, merchant_id, str(order_number), source_ref)):
        fail("paid_page_source_invalid", "مصدر الطلب غير مكتمل", 409)
    if await _resolve_user_id(db, merchant_id) != owner:
        fail("paid_page_merchant_owner_changed", "تغير ارتباط متجر سلة", 409)
    source_pair = await _canonical_source(db, owner, order_number)
    if source_pair is None:
        return {"status":"not_eligible"}
    source, raw = source_pair
    page = native_paid_order_page(
        raw, owner=owner, order_number=str(order_number), source=source)
    if page is None:
        return {"status":"not_eligible"}
    return {"status":"review_required",
            "reason_code":"paid_page_original_capture_date_required",
            "order_id":page["order_id"], "provider":page["provider"]}
