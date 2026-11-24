"""CAS-atomic first-payment posting — internal signed-Salla worker only.

This is NOT an HTTP API and defaults disabled. It writes a canonical capture
fact and its provider receivable obligation using the existing financial
aggregate. No independent journal, bank entry, GL, or Qoyod entry exists.

The source proof MUST have been durably staged earlier in the same owner
aggregate by the verified Salla payment inbox. The last successful source
snapshot must be recent; a stale/source-broken workspace never guesses money.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os

from operational_payment_direct_provider import (
    InitialCaptureHold, apply_first_provider_capture,
)

POST_FLAG = "OPERATIONAL_PAYMENT_DIRECT_POST_ENABLED"
MAX_SOURCE_AGE = timedelta(hours=3)


def enabled() -> bool:
    return os.environ.get(POST_FLAG) == "1"


def _recent_source_snapshot(state, at) -> bool:
    stamp = state.get("engine", {}).get("as_of")
    if not isinstance(stamp, str):
        return False
    try:
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        current = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if observed.utcoffset() is None or current.utcoffset() is None:
            return False
        elapsed = current.astimezone(timezone.utc) - observed.astimezone(timezone.utc)
        return timedelta(0) <= elapsed <= MAX_SOURCE_AGE
    except (TypeError, ValueError):
        return False


def _assert_staged_evidence(state, owner, source_ref, order_id, normalized):
    from operational_payment_capture_adapter import payment_events_from_normalized
    from operational_payment_evidence_inbox import event_record
    events = payment_events_from_normalized(order_id, normalized, ())
    if len(events) != 1:
        raise InitialCaptureHold("initial_capture_requires_one_transaction")
    record = event_record(events[0], owner=owner,
                          source="salla_native_transactions", source_ref=source_ref)
    saved = state.get("payment_event_evidence_inbox", {}).get(record["key"])
    if not isinstance(saved, dict):
        raise InitialCaptureHold("signed_payment_evidence_not_staged")
    matching = all(saved.get(field) == record[field] for field in (
        "order_id", "provider", "kind", "currency", "amount",
        "transaction_key", "native_ref", "captured_at", "target",
    ))
    proofs = saved.get("sources") or []
    if (not matching or saved.get("status") not in
            {"evidence_received", "already_recognized"}
            or not isinstance(proofs, list)
            or {"source": "salla_native_transactions", "ref": source_ref} not in proofs):
        raise InitialCaptureHold("staged_financial_evidence_conflict")


async def post_first_salla_capture(db, owner, merchant_id, order_id, order_number,
                                   normalized, *, source_ref, clock=None):
    """Owner-scoped CAS posting behind a SECOND opt-in flag.

    If the native proof or financial settings changed, fail closed. Separate
    Qoyod/accounting state remains protected by the existing active_gate.
    """
    if not enabled():
        return {"status": "posting_disabled"}
    from operational_balance_store import audit, fail, mutate, now
    from operational_balance_service import active_gate
    from operational_payment_cas_reconciliation import reconcile_staged_payment_evidence

    stamp = clock or now()
    if not isinstance(source_ref, str) or not source_ref.strip() or not isinstance(merchant_id, str) or not merchant_id.strip():
        fail("payment_source_reference_missing", "إثبات الدفع الأصلي غير متاح", 409)

    async def verify_merchant():
        # Independently recheck current merchant->owner authorization at
        # commit, not just when an inbound Salla event entered the queue.
        from salla_integration.webhook_order_sync import _resolve_user_id
        if await _resolve_user_id(db, merchant_id) != owner:
            fail("payment_merchant_owner_changed", "تغير ارتباط متجر سلة؛ أوقف الترحيل", 409)

    async def apply(state):
        await active_gate(db, owner, state)
        if not _recent_source_snapshot(state, stamp):
            fail("payment_source_snapshot_stale",
                 "تحديث مصادر الأرصدة قديم؛ لا يمكن ترحيل الدفعة قبل التحقق", 409)
        try:
            _assert_staged_evidence(state, owner, source_ref, order_id, normalized)
            result = apply_first_provider_capture(
                state, order_id=order_id, order_number=order_number,
                normalized=normalized, as_of=stamp)
        except InitialCaptureHold as exc:
            fail(str(exc), "تعذر اعتماد الدفعة آليًا؛ يلزم مطابقة إثبات المصدر", 409)
        if result["status"] == "posted":
            reconcile_staged_payment_evidence(state)
            audit(state, "operational_salla_payment_worker",
                  "salla_first_capture_posted", None,
                  {"provider": result["provider"], "order_number": order_number,
                   "source_ref": source_ref, "capture_ref": result["reference"],
                   "amount": result["amount"], "currency": "SAR"},
                  "تسجيل مستحق المنصة من عملية دفع أصلية مؤكدة",
                  "salla_verified_payment", stamp)
        return result

    await verify_merchant()
    return await mutate(db, owner, apply, before_commit=lambda before, after: verify_merchant())
