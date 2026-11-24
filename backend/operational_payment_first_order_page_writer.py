"""Owner-scoped CAS writer for an independently current Salla paid order page.

The order page exposes cumulative paid principal and a transaction reference
but may lack a true captured_at. This module NEVER invents a gateway timestamp.
It reads the canonical Salla direct snapshot, calls the existing
operational_order_page_payment.observed_balance guard and writes the same
provider_order_balance:<order_id> fact/obligation inside the existing CAS.

Disabled by default; source version, accounting gate, owner scope, fee contract
and cutover checks must succeed. No bank, general ledger or Qoyod posting.
"""
from __future__ import annotations

import os
from operational_payment_direct_order_page import FirstPageHold, apply_first_paid_order_page

PAGE_POST_FLAG = "OPERATIONAL_PAYMENT_ORDER_PAGE_POST_ENABLED"
PROJECTION = {"_id":0, "user_id":1, "order_number":1,
              "raw_by_source.salla_direct":1, "g47_salla_snapshot":1,
              "salla_order_id":1, "order_id":1, "order_status_slug":1}


def enabled():
    return os.environ.get(PAGE_POST_FLAG) == "1"


async def _canonical_source(db, owner, order_number):
    record = await db.unified_orders.find_one(
        {"user_id":owner, "order_number":str(order_number)}, PROJECTION)
    if not isinstance(record, dict) or record.get("user_id") != owner:
        return None
    raw = (record.get("raw_by_source") or {}).get("salla_direct")
    if not isinstance(raw, dict) or str(raw.get("reference_id") or raw.get("order_number") or "") != str(order_number):
        return None
    return record, raw


def _financial_fingerprint(source, raw):
    from operational_balance_store import digest
    # Compare only financially relevant native fields and the source version,
    # not names, addresses or other customer content.
    return digest({
        "order_id":raw.get("id"), "reference_id":raw.get("reference_id"),
        "payment_method":raw.get("payment_method"),
        "payment_methods":raw.get("payment_methods"),
        "payment_actions":raw.get("payment_actions"),
        "status":raw.get("status"), "date":raw.get("date"),
        "created_at":raw.get("created_at"), "updated_at":raw.get("updated_at"),
        "amounts":raw.get("amounts"), "watermark":source.get("g47_salla_snapshot"),
        "owner":source.get("user_id"), "source_order_id":source.get("salla_order_id"),
    })


async def post_first_paid_page(db, owner, merchant_id, order_number, *,
                               source_ref, clock=None):
    """Return posted / already_posted / review_required / not_eligible.

    Persistent financial mutations occur ONLY under owner CAS and revalidate
    merchant ownership/source version before the final commit.
    """
    if not enabled():
        return {"status":"posting_disabled"}
    from operational_balance_store import mutate, fail, audit, now
    from operational_balance_service import active_gate
    from operational_payment_first_capture_writer import _recent_source_snapshot
    from operational_payment_salla_order_page import native_paid_order_page
    from operational_payment_refresh_health_gate import require_current_refresh_health
    from salla_integration.webhook_order_sync import _resolve_user_id

    if not all(isinstance(v, str) and v.strip() for v in (owner, merchant_id, str(order_number), source_ref)):
        fail("paid_page_source_invalid", "مصدر الطلب غير مكتمل", 409)
    stamp = clock or now()
    if await _resolve_user_id(db, merchant_id) != owner:
        fail("paid_page_merchant_owner_changed", "تغير ارتباط متجر سلة", 409)
    original = await _canonical_source(db, owner, order_number)
    if original is None:
        return {"status":"not_eligible"}
    source, raw = original
    page = native_paid_order_page(raw, owner=owner, order_number=str(order_number), source=source)
    if page is None:
        return {"status":"not_eligible"}
    fingerprint = _financial_fingerprint(source, raw)

    async def apply(state):
        await active_gate(db, owner, state)
        await require_current_refresh_health(db, owner, state, at=stamp)
        if not _recent_source_snapshot(state, stamp):
            fail("paid_page_source_snapshot_stale",
                 "احتساب المصادر قديم ولا يسمح بتسجيل مالي جديد", 409)
        try:
            result = apply_first_paid_order_page(
                state, raw=raw, source=source, owner=owner,
                order_number=str(order_number), as_of=stamp)
        except FirstPageHold as exc:
            if str(exc) in {
                "historical_page_requires_reconciliation",
                "preexisting_order_provider_effect",
                "native_capture_already_recognized",
                "provider_settlement_requires_review",
                "provider_fee_policy_missing",
                "paid_page_provider_projection_unverified",
                "paid_page_reference_conflict",
                "paid_page_reference_reused",
                "salla_payment_rail_unverified",
            }:
                return {"status":"review_required","reason_code":str(exc),
                        "provider":page["provider"],"order_id":page["order_id"]}
            fail(str(exc), "تعذر اعتماد دفع سلة من المصدر", 409)
        if result["status"] == "posted":
            audit(state, "operational_salla_payment_worker",
                  "salla_first_paid_page_posted", None,
                  {"provider":result["provider"],"order_number":str(order_number),
                   "source_ref":source_ref,"transaction_reference":result["reference"],
                   "amount":result["amount"],"currency":"SAR"},
                  "استحقاق منصة من صفحة سلة مدفوعة مؤكدة (دون افتراض وقت التحصيل)",
                  "salla_order_page", stamp)
        return result

    async def final_source_check(before, after):
        await require_current_refresh_health(db, owner, after, at=stamp)
        if await _resolve_user_id(db, merchant_id) != owner:
            fail("paid_page_merchant_owner_changed", "تغير ارتباط متجر سلة", 409)
        fresh = await _canonical_source(db, owner, order_number)
        if fresh is None or _financial_fingerprint(*fresh) != fingerprint:
            fail("paid_page_source_changed", "تغير إثبات سلة أثناء تسجيل القيد", 409)

    return await mutate(db, owner, apply, before_commit=final_source_check)
