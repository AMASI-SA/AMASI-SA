"""Acquire original provider capture evidence into the isolated source ledger.

Only saved owner-bound date gaps are selected.  Authenticated native transaction
observations remain append-only; this adapter cannot post or modify balances.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from operational_balance_store import check_authorization
from operational_payment_evidence import _instant
from operational_provider_dates import UNPROVEN
from operational_provider_transaction_evidence import COLLECTION, VERSION
from operational_provider_capture_batches import PENDING_CODE, PENDING_FIELD, build_batch
from order_activity_v1 import normalize_transaction_row
from salla_payment_collection import provider_order_page_current
from .courier_delivery_sync import _complete_pages


_RETRY = "salla_provider_capture_retry_after"
_RAW = "raw_by_source.salla_direct"
_FENCED = ("order_id", "salla_order_id", "merchant", "merchant_id", "store_id", "g47_salla_snapshot",
    "order_status", "order_status_slug", "last_salla_direct_status_source_updated_at")
_PROJECTION = {"_id": 0, "user_id": 1, "order_number": 1, _RAW: 1, **dict.fromkeys(_FENCED, 1)}


def _targets(issues):
    targets = set()
    for row in issues:
        if not isinstance(row, dict) or row.get("code") not in {UNPROVEN, PENDING_CODE}:
            continue
        source = row.get("source_id")
        if not isinstance(source, str):
            continue
        if source.startswith("provider:"):
            identity = source.removeprefix("provider:").split(":", 1)[0]
        elif row.get("code") == PENDING_CODE:
            identity = source
        else:
            continue
        if identity and len(identity) <= 32 and identity.isascii() and identity.isdecimal():
            targets.add(identity)
    return sorted(targets)


async def acquire_missing(db, owner, issues, *, limit=4):
    """Read at most four selected orders, three pages each, under the caller budget."""
    targets = _targets(issues)
    receipt = {"status": "completed", "selected": 0, "outcomes": []}
    if not targets:
        return receipt
    identities = targets + [int(value) for value in targets]
    now = datetime.now(timezone.utc)
    for _ in range(max(0, min(limit, 4))):
        await check_authorization()
        token = uuid4().hex
        source = await db.unified_orders.find_one_and_update(
            {"user_id": owner, _RAW + ".id": {"$in": identities},
             "$or": [{_RETRY: {"$exists": False}}, {_RETRY: {"$lte": now}}]},
            {"$set": {_RETRY: now + timedelta(minutes=15), PENDING_FIELD: token}}, projection=_PROJECTION)
        if not source:
            break
        receipt["selected"] += 1
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        identity, number = str(raw.get("id") or ""), str(source.get("order_number") or "")
        outcome = {"order_id": identity, "status": "blocked", "reason": "provider_source_binding_unproven"}
        receipt["outcomes"].append(outcome)
        if (source.get("user_id") != owner or identity not in targets or not number
                or str(raw.get("reference_id") or raw.get("order_number") or "") != number
                or source.get("salla_order_id") is not None and str(source["salla_order_id"]) != identity
                or not provider_order_page_current(source, raw, owner=owner, order_number=number, provider_order_id=identity)):
            receipt["status"] = "partial"
            continue
        try:
            # This is the same authenticated endpoint and order_id parameter as
            # order_activity_v1. Complete pagination is required before saving.
            rows = await _complete_pages(db, owner, "/transactions", {"order_id": identity}, max_pages=3)
            normalized = []
            dated = False
            for row in rows:
                refs = row.get("references")
                if isinstance(refs, dict) and refs.get("order_id") is not None and str(refs["order_id"]) != identity:
                    continue
                value = normalize_transaction_row(row, order_number=number, provider_order_id=identity)
                proof = value["native_transaction_evidence"]
                if (proof.get("schema") == VERSION and proof.get("provider_order_id") == identity
                        and proof.get("status") in {"paid", "captured"} and proof.get("paid_at")
                        and _instant(proof["paid_at"]) <= now):
                    dated = True
                # Retain negative/unverified matching observations too. Filtering
                # them out could hide a conflicting native refund or void.
                normalized.append(value)
            batch = build_batch(normalized, owner=owner, order_number=number,
                provider_order_id=identity) if normalized else None
        except (ValueError, TypeError, KeyError, ArithmeticError):
            receipt["status"] = "partial"
            outcome["reason"] = "provider_native_pagination_or_evidence_unproven"
            continue
        selector = {"user_id": owner, "order_number": number, _RAW: raw,
                    **{key: source.get(key) for key in _FENCED}}
        await check_authorization()
        if not await db.unified_orders.find_one(selector, {"_id": 0, "user_id": 1}):
            receipt["status"] = "partial"
            outcome.update(status="source_changed", reason="provider_source_changed")
            continue
        if batch is not None:
            # Publish all observations together. A timeout can leave either no
            # batch or the complete batch, never a positive financial prefix
            # whose trailing native refund/void was not saved yet.
            await check_authorization()
            await db[COLLECTION].update_one(
                {"user_id": owner, "order_number": number, "provider_order_id": identity,
                 "fingerprint": batch["fingerprint"], "source": "salla_transactions"},
                {"$setOnInsert": {**batch, "created_at": now.isoformat()},
                 "$set": {"last_seen_at": now.isoformat()}}, upsert=True)
            # A pending source blocks even older cached positives until this
            # whole publication is acknowledged. An uncertain write or scope
            # change cannot clear the admission fence.
            await check_authorization()
            cleared = await db.unified_orders.update_one({**selector, PENDING_FIELD: token},
                {"$set": {PENDING_FIELD: None}})
            if cleared.matched_count != 1:
                receipt["status"] = "partial"
                outcome.update(status="source_changed", reason="provider_source_changed")
                continue
        outcome.update(status="acquired" if dated else "unavailable",
            reason="provider_native_observations_saved" if dated else "provider_native_capture_time_missing",
            observations=len(normalized))
        if not dated:
            receipt["status"] = "partial"
    return receipt
