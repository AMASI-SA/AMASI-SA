"""Acquire existing-order SAR payment evidence without opening orders or posting money."""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pymongo.errors import PyMongoError
from operational_balance_store import check_authorization
from salla_paid_sar_evidence import FIELD, list_payment_evidence, matching_sar_payment
from .service import call_salla

log = logging.getLogger(__name__)
_RETRY = "salla_paid_sar_retry_after"
_RAW = "raw_by_source.salla_direct."
_PROJECTION = {"_id":0, "user_id":1, "order_number":1, "order_id":1, "salla_order_id":1,
               "g47_salla_snapshot.cancelled":1, "g47_salla_snapshot.requires_authoritative_refresh":1, FIELD:1,
               **{_RAW+k:1 for k in ("id", "reference_id", "currency", "date", "created_at", "updated_at",
                  "date_updated", "amounts.total", "total", "payment_method", "payment_methods", "payment_actions",
                  "exchange_rate", "status", "is_pending_payment", "order_number", "payment.status",
                  "payment.collection_status", "payment_status", "payment_collection_status")}}


async def persist_list_evidence(db, owner, row):
    proof = list_payment_evidence(owner, row)
    if proof is None:
        return False
    selector = {"user_id":owner, "order_number":proof["order_number"]}
    local = await db.unified_orders.find_one(selector, _PROJECTION)
    if not local:
        return False
    raw = (local.get("raw_by_source") or {}).get("salla_direct") or {}
    if matching_sar_payment({**local, FIELD:proof}, raw, owner=owner) != Decimal(proof["paid_sar"]):
        return False
    if local.get(FIELD) == proof:
        return True
    selector.update({FIELD:local.get(FIELD), _RAW+"id":raw["id"], _RAW+"payment_methods":raw["payment_methods"],
                     _RAW+"updated_at":raw.get("updated_at"), _RAW+"date_updated":raw.get("date_updated")})
    result = await db.unified_orders.update_one(selector, {"$set":{FIELD:proof}})
    return bool(result.matched_count)


async def observe_list_evidence(db, owner, row):
    try:
        await persist_list_evidence(db, owner, row)
    except PyMongoError:
        # Discovery remains available; a later explicit balance refresh can retry.
        log.warning("Salla list payment evidence deferred")


async def acquire_missing(db, owner, requests, *, limit=4):
    """Spend at most four Salla list reads in one explicit acquisition request."""
    targets = {str(r["order_number"]):str(r["order_id"]) for r in requests}
    if not targets:
        return
    now = datetime.now(timezone.utc)
    for _ in range(min(limit, 4)):
        order = await db.unified_orders.find_one_and_update(
            {"user_id":owner, "order_number":{"$in":list(targets)},
             "$or":[{_RETRY:{"$exists":False}}, {_RETRY:{"$lte":now}}]},
            {"$set":{_RETRY:now+timedelta(minutes=15)}},
            projection={"_id":0,"order_number":1,"order_id":1,"salla_order_id":1})
        if not order:
            break
        number = order["order_number"]
        if str(order.get("order_id") or order.get("salla_order_id") or "") != targets[number]:
            continue
        await check_authorization()
        response = await call_salla(db, owner, "GET", "/orders", params={"reference_id":number,"per_page":10})
        matches = [row for row in response.get("data", []) if isinstance(row, dict)
                   and str(row.get("reference_id")) == number and str(row.get("id")) == targets[number]]
        if len(matches) == 1:
            await persist_list_evidence(db, owner, matches[0])
