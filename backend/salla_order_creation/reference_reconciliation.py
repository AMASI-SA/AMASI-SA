"""Read-only Salla order-reference recovery after an already successful POST.

Salla Merchant Create Order may return order.id before reference_id is usable.
Mezan's Order Engine indexes by order reference. Recover reference_id with one
authenticated GET /orders/{salla_order_id}, NEVER another POST /orders.

This only amends the internal assisted-order intent metadata. It does not
create a payment URL, change Salla order status, send WhatsApp or post GL.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .service import intent_storage_id


class ReferenceUnverified(ValueError):
    pass


async def recover_salla_reference(
    db: Any, *,
    user_id: str,
    intent_id: str,
    provider_get: Callable[..., Awaitable[dict]],
) -> dict:
    if not user_id or not callable(provider_get):
        raise ReferenceUnverified("trusted_provider_reader_required")
    key = intent_storage_id(user_id, intent_id)
    previous = await db.salla_ai_order_intents.find_one({
        "_id": key, "user_id": user_id,
    })
    if not isinstance(previous,dict):
        raise ReferenceUnverified("unknown_order_intent")
    if previous.get("state") not in ("created", "created_amount_unverified"):
        raise ReferenceUnverified("salla_creation_not_confirmed")
    order_id = previous.get("salla_order_id")
    if type(order_id) is not int or order_id < 1:
        raise ReferenceUnverified("salla_order_id_not_proven")
    known = previous.get("reference_id")
    if type(known) is int and known > 0:
        return {"state":"reference_known", "salla_order_id":order_id,
                "reference_id":known, "provider_get_used":False}

    # Read only the order ID stored from the original successful Salla POST.
    # No customer- or model-supplied order lookup and no blind POST retries.
    try:
        response = await provider_get(
            db,user_id,"GET",f"/orders/{order_id}",
        )
    except Exception:
        return {"state":"reference_not_yet_available","salla_order_id":order_id,
                "reference_id":None,"provider_get_used":True}
    if not isinstance(response,dict) or response.get("success") is False:
        return {"state":"reference_not_yet_available","salla_order_id":order_id,
                "reference_id":None,"provider_get_used":True}
    data = response.get("data")
    if not isinstance(data,dict):
        raise ReferenceUnverified("invalid_provider_order_details")
    provider_id = data.get("id")
    if (type(provider_id) not in (str,int) or
            not str(provider_id).isdigit() or int(provider_id)!=order_id):
        raise ReferenceUnverified("provider_order_identity_mismatch")
    ref = data.get("reference_id")
    if (type(ref) not in (str,int) or
            not str(ref).isdigit() or int(ref) <= 0):
        return {"state":"reference_not_yet_available","salla_order_id":order_id,
                "reference_id":None,"provider_get_used":True}
    reference = int(ref)
    changed = await db.salla_ai_order_intents.update_one({
        "_id":key, "user_id":user_id,
        "salla_order_id":order_id, "reference_id":None,
        "state":{"$in":["created","created_amount_unverified"]},
    },{"$set":{"reference_id":reference}})
    if getattr(changed,"modified_count",0) != 1:
        latest=await db.salla_ai_order_intents.find_one({
            "_id":key, "user_id":user_id,
        })
        if (not isinstance(latest,dict) or
                latest.get("salla_order_id")!=order_id or
                latest.get("reference_id")!=reference):
            raise ReferenceUnverified("concurrent_reference_conflict")
    return {"state":"reference_recovered","salla_order_id":order_id,
            "reference_id":reference,"provider_get_used":True}
