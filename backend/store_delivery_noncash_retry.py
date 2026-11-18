"""Read-only recovery of an identical completed noncash delivery request."""
from fastapi import HTTPException

from store_delivery_domain import StoreDeliveryRuleError, collection_requirements, money, normalize_text


def _conflict():
    raise HTTPException(409, detail={"code": "driver_delivery_idempotency_conflict"})


async def noncash_delivery_retry(db, *, owner, actor, driver, assignment, payload):
    user = await db.users.find_one({"id": actor.get("id")})
    if (not user or user.get("role") != "store_driver" or user.get("created_by") != owner
            or user.get("disabled") is True or user.get("is_active") is False):
        raise HTTPException(403, detail={"code": "store_driver_account_required"})
    if not await db.store_drivers.find_one({"user_id": owner, "id": driver["id"],
            "account_user_id": actor["id"], "status": "active"}):
        raise HTTPException(403, detail={"code": "store_driver_profile_not_linked"})
    identity = {"user_id": owner, "driver_id": driver["id"], "assignment_id": assignment["id"]}
    rows = await db.store_delivery_collections.find(identity).limit(2).to_list(2)
    stored = rows[0] if len(rows) == 1 else None
    method = payload.payment_method
    proof = normalize_text(payload.delivery_proof_reference)
    receipt = normalize_text(payload.receipt_reference)
    if (not stored or not assignment.get("active") or assignment.get("status") != "delivered"
            or assignment.get("driver_id") != driver["id"] or assignment.get("user_id") != owner
            or method not in {"bank_transfer", "card_terminal"}
            or stored.get("payment_method") != method or assignment.get("collection_method") != method
            or not proof or not receipt
            or stored.get("delivery_proof_reference") != proof or assignment.get("delivery_proof_reference") != proof
            or stored.get("receipt_reference") != receipt or assignment.get("receipt_reference") != receipt
            or normalize_text(stored.get("bank_account_id")) != normalize_text(payload.bank_account_id)
            or payload.physical_cash_amount is not None or payload.physical_cash_confirmed
            or normalize_text(payload.conversation_evidence_reference)
            or stored.get("order_id") != assignment.get("order_id")
            or stored.get("order_number") != assignment.get("order_number")
            or not assignment.get("delivered_at")):
        _conflict()
    for collection, token in (("store_delivery_delivery_proofs", proof), ("store_delivery_receipts", receipt)):
        if not await db[collection].find_one({**identity, "token": token, "status": "bound"}, {"_id": 1}):
            _conflict()
    order = await db.unified_orders.find_one({"user_id": owner, "order_id": assignment.get("order_id"),
        "order_number": assignment.get("order_number"), "store_delivery_assignment_id": assignment["id"],
        "store_delivery_driver_id": driver["id"], "store_delivery_status": "delivered",
        "store_delivery_proof_reference": proof, "store_delivery_receipt_reference": receipt}, {"_id": 1})
    earnings = await db.store_delivery_driver_earnings.find(identity).limit(2).to_list(2)
    if not order or len(earnings) != 1:
        _conflict()
    try:
        if (money(earnings[0].get("amount")) != money(assignment.get("delivery_fee_snapshot"))
                or money(stored.get("amount")) != money(assignment.get("collection_amount"))):
            _conflict()
        requirements = collection_requirements(outstanding_amount=stored["amount"], payment_method=method)
    except StoreDeliveryRuleError:
        _conflict()
    requirements["review_status"] = stored.get("review_status") or requirements["review_status"]
    return {**{key: value for key, value in assignment.items() if key not in {"_id", "user_id"}},
        "earning_amount": float(money(earnings[0]["amount"])), "collection": requirements,
        "authoritative_outstanding_amount": requirements["amount"]}
