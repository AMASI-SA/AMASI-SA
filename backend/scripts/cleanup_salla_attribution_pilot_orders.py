"""Quarantine orders leaked from the Salla attribution pilot.

Dry-run is the default. Apply mode requires the exact pilot store id and a
store-specific confirmation phrase. Qoyod/accounting records are never changed;
their presence makes the cleanup fail closed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from motor.motor_asyncio import AsyncIOMotorClient

ORDER_ATTRIBUTION_COLLECTION = "mezan_order_attributions_v1"
OUTBOX_COLLECTION = "mezan_snapchat_capi_outbox_v1"
QUARANTINE_COLLECTION = "mezan_demo_store_quarantine_v1"


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _confirmation(store_id: str) -> str:
    return f"QUARANTINE-DEMO-STORE-{store_id}"


def _configured_pilot_store_id() -> str:
    return _text(os.environ.get("SALLA_ATTRIBUTION_PILOT_STORE_ID"))


def _has_accounting_projection(order: dict[str, Any]) -> bool:
    accounting = order.get("accounting")
    if isinstance(accounting, dict) and any(
        value not in (None, "", False, [], {}) for value in accounting.values()
    ):
        return True
    guarded = (
        "qoyod_invoice_id",
        "qoyod_invoice_number",
        "qoyod_payment_id",
        "qoyod_synced_at",
        "qoyod_synced_by",
        "accounting_status",
        "accounting_synced_at",
    )
    return any(order.get(key) not in (None, "", False, [], {}) for key in guarded)


async def _count_accounting_footprints(db: Any, order_number: str) -> dict[str, int]:
    selectors = {
        "qoyod_invoices": {
            "$or": [
                {"reference": order_number},
                {"salla_order_number": order_number},
            ]
        },
        "integration_inbox": {"salla_order_number": order_number},
        "qoyod_manual_send_locks": {"order_number": order_number},
        "general_ledger": {
            "$or": [
                {"order_number": order_number},
                {"source_order_number": order_number},
                {"reference": order_number},
                {"metadata.order_number": order_number},
            ]
        },
        "account_transactions": {
            "$or": [
                {"order_number": order_number},
                {"source_order_number": order_number},
                {"reference": order_number},
                {"metadata.order_number": order_number},
            ]
        },
    }
    return {
        name: int(await db[name].count_documents(selector))
        for name, selector in selectors.items()
    }


async def cleanup(
    db: Any,
    *,
    store_id: str,
    target_date: str,
    apply: bool,
) -> dict[str, Any]:
    captures = await db.salla_webhook_event_captures.find(
        {
            "merchant_id": store_id,
            "order_sync.synced": True,
            "order_sync.order_number": {"$exists": True, "$nin": [None, ""]},
            "demo_store_cleanup.applied": {"$ne": True},
        },
        {
            "_id": 0,
            "order_sync.order_number": 1,
            "event": 1,
            "first_received_at": 1,
            "last_received_at": 1,
        },
    ).to_list(length=10000)
    order_numbers = sorted({
        _text((row.get("order_sync") or {}).get("order_number"))
        for row in captures
        if _text((row.get("order_sync") or {}).get("order_number"))
    })

    candidates: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    outside_target_date: list[str] = []
    for order_number in order_numbers:
        orders = await db.unified_orders.find(
            {"order_number": order_number},
        ).to_list(length=3)
        if len(orders) != 1:
            refused.append({
                "order_number": order_number,
                "reason": "expected_exactly_one_unified_order",
                "matches": len(orders),
            })
            continue
        order = orders[0]
        if _text(order.get("order_date")) != target_date:
            outside_target_date.append(order_number)
            continue
        if not order.get("salla_webhook_event"):
            refused.append({
                "order_number": order_number,
                "reason": "not_identified_as_webhook_created_order",
            })
            continue
        footprints = await _count_accounting_footprints(db, order_number)
        if _has_accounting_projection(order) or any(footprints.values()):
            refused.append({
                "order_number": order_number,
                "reason": "qoyod_or_accounting_footprint_present",
                "footprints": footprints,
            })
            continue
        candidates.append(order)

    summary = {
        "mode": "apply" if apply else "dry_run",
        "store_id": store_id,
        "target_date": target_date,
        "capture_count": len(captures),
        "identified_order_numbers": order_numbers,
        "candidate_order_numbers": [_text(row.get("order_number")) for row in candidates],
        "outside_target_date_order_numbers": outside_target_date,
        "refused": refused,
        "quarantined_orders": 0,
        "quarantined_attributions": 0,
        "cancelled_pending_snapchat_events": 0,
        "deleted_unified_orders": 0,
        "qoyod_writes": 0,
        "accounting_writes": 0,
    }
    if not apply:
        return summary
    if refused:
        raise RuntimeError(
            "cleanup refused: at least one identified order was ambiguous or has "
            "a Qoyod/accounting footprint"
        )

    now = datetime.now(timezone.utc)
    quarantine = db[QUARANTINE_COLLECTION]
    for order in candidates:
        order_number = _text(order.get("order_number"))
        user_id = _text(order.get("user_id"))
        original_id = order.get("_id")

        archive = deepcopy(order)
        archive.pop("_id", None)
        archived = await quarantine.update_one(
            {
                "record_type": "unified_order",
                "pilot_store_id": store_id,
                "original_id": str(original_id),
            },
            {"$setOnInsert": {
                "record_type": "unified_order",
                "pilot_store_id": store_id,
                "order_number": order_number,
                "user_id": user_id,
                "original_id": str(original_id),
                "record": archive,
                "quarantined_at": now,
                "reason": "salla_attribution_pilot_order_leak",
                "recoverable": True,
            }},
            upsert=True,
        )
        summary["quarantined_orders"] += int(bool(archived.upserted_id))

        attribution = await db[ORDER_ATTRIBUTION_COLLECTION].find_one({
            "user_id": user_id,
            "order_number": order_number,
        })
        if attribution:
            attribution_archive = deepcopy(attribution)
            attribution_archive.pop("_id", None)
            result = await quarantine.update_one(
                {
                    "record_type": "order_attribution",
                    "pilot_store_id": store_id,
                    "user_id": user_id,
                    "order_number": order_number,
                },
                {"$setOnInsert": {
                    "record_type": "order_attribution",
                    "pilot_store_id": store_id,
                    "user_id": user_id,
                    "order_number": order_number,
                    "record": attribution_archive,
                    "quarantined_at": now,
                    "reason": "salla_attribution_pilot_order_leak",
                    "recoverable": True,
                }},
                upsert=True,
            )
            summary["quarantined_attributions"] += int(bool(result.upserted_id))
            await db[ORDER_ATTRIBUTION_COLLECTION].delete_one({"_id": attribution["_id"]})

        cancelled = await db[OUTBOX_COLLECTION].update_many(
            {
                "user_id": user_id,
                "event_id": order_number,
                "status": {"$in": ["pending", "retry"]},
            },
            {"$set": {
                "status": "cancelled",
                "payload": None,
                "payload_redacted_after_cancel": True,
                "lock_owner": None,
                "lock_expires_at": None,
                "last_error": {
                    "code": "attribution_pilot_order_quarantined",
                    "retryable": False,
                },
                "updated_at": now,
            }},
        )
        summary["cancelled_pending_snapchat_events"] += int(cancelled.modified_count)

        deleted = await db.unified_orders.delete_one({
            "_id": original_id,
            "order_number": order_number,
            "user_id": user_id,
            "salla_webhook_event": {"$exists": True},
        })
        if deleted.deleted_count != 1:
            raise RuntimeError(f"archived order was not deleted safely: {order_number}")
        summary["deleted_unified_orders"] += 1

    await db.salla_webhook_event_captures.update_many(
        {"merchant_id": store_id, "order_sync.order_number": {"$in": order_numbers}},
        {"$set": {
            "demo_store_cleanup.applied": True,
            "demo_store_cleanup.applied_at": now,
            "demo_store_cleanup.quarantine_collection": QUARANTINE_COLLECTION,
        }},
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", required=True)
    parser.add_argument(
        "--date",
        default=(datetime.now(ZoneInfo("Asia/Riyadh")).date() - timedelta(days=1)).isoformat(),
        help="Only quarantine orders whose canonical order_date equals this YYYY-MM-DD date",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    try:
        date.fromisoformat(args.date)
    except ValueError as exc:
        raise SystemExit("--date must use YYYY-MM-DD") from exc
    expected_store_id = _configured_pilot_store_id()
    if not expected_store_id:
        raise SystemExit(
            "refusing: SALLA_ATTRIBUTION_PILOT_STORE_ID must be set explicitly"
        )
    if args.store_id != expected_store_id:
        raise SystemExit("refusing: --store-id is not the configured attribution pilot")
    if args.apply and args.confirm != _confirmation(expected_store_id):
        raise SystemExit(
            f"refusing apply: pass --confirm {_confirmation(expected_store_id)}"
        )
    mongo_url = _text(os.environ.get("MONGO_URL"))
    db_name = _text(os.environ.get("DB_NAME"))
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL and DB_NAME are required")
    client = AsyncIOMotorClient(mongo_url)
    try:
        result = await cleanup(
            client[db_name],
            store_id=expected_store_id,
            target_date=args.date,
            apply=bool(args.apply),
        )
        # Output intentionally contains counts and order numbers only, never PII.
        print(result)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
