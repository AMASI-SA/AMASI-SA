"""Single-order, owner-bound, READ-ONLY native Salla capture proof preview.

No Salla network call, source refresh or financial write. Reads a maximum of
two canonical order records to detect duplicate order-number identity and 33
native evidence envelopes (32 allowed), then validates only the documented
tenant-scoped transaction proof. Returns no customer names, addresses,
tokens or raw order payload.
"""
from __future__ import annotations

import asyncio

from operational_payment_native_capture_candidate import recovered_dated_native_capture
from operational_provider_capture_batches import FIELD as BATCH_FIELD

MAX_DOCS = 32
ORDER_FIELDS = {
    "_id":0,"user_id":1,"order_number":1,"salla_order_id":1,"order_id":1,
    "raw_by_source.salla_direct":1,"g47_salla_snapshot":1,
}
TRANSACTION_FIELDS = {
    "_id":0,"user_id":1,"source":1,"order_number":1,
    "provider_order_id":1,"transaction_id":1,"fingerprint":1,
    "native_transaction_evidence":1,BATCH_FIELD:1,
}


def _bounded_order_number(value):
    if type(value) is not str or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 32:
        raise ValueError("payment_order_number_invalid")
    return value


async def preview_one_native_capture(db, owner, order_number, *, as_of):
    """Pure observation of SAVED Salla data, not an authorization to post."""
    if type(owner) is not str or not owner or len(owner) > 200:
        raise ValueError("payment_owner_scope_invalid")
    number = _bounded_order_number(order_number)
    import pymongo
    from operational_read_runtime import read_source_cursor

    # Independently bounded owner reads; duplicate or over-limit results are
    # unknown, NEVER interpreted as a single valid payment.
    with pymongo.timeout(2.0):
        async with asyncio.timeout(3.0):
            found = await read_source_cursor(
                db["unified_orders"].find(
                    {"user_id":owner,"order_number":number}, ORDER_FIELDS
                ).limit(2), 2)
    if not isinstance(found,list) or len(found) != 1 or not isinstance(found[0],dict):
        return {"status":"not_verified","reason":"canonical_salla_order_count_invalid",
                "posting_permitted":False}
    source = found[0]
    raw = (source.get("raw_by_source") or {}).get("salla_direct")
    if not isinstance(raw,dict):
        return {"status":"not_verified","reason":"canonical_salla_order_missing",
                "posting_permitted":False}
    order_id = str(raw.get("id") or "")
    if not order_id:
        return {"status":"not_verified","reason":"canonical_salla_identity_missing",
                "posting_permitted":False}

    with pymongo.timeout(2.0):
        async with asyncio.timeout(3.0):
            evidence = await read_source_cursor(
                db["order_payment_transactions_v2"].find({
                    "user_id":owner, "order_number":number,
                    "provider_order_id":order_id, "source":"salla_transactions",
                },TRANSACTION_FIELDS).limit(MAX_DOCS+1), MAX_DOCS+1)
    if type(evidence) is not list or len(evidence) > MAX_DOCS:
        return {"status":"not_verified","reason":"transaction_rows_incomplete",
                "posting_permitted":False}
    return recovered_dated_native_capture(
        source=source,raw=raw,rows=evidence,owner=owner,
        order_number=number,as_of=as_of,
    )
