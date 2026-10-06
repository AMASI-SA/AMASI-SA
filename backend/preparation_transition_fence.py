"""Read fences used inside the owner-serialized transition transaction only."""
from fastapi import HTTPException
import fulfillment_lifecycle as lc

async def assert_transition_current(db, *, user_id, piece_id, expected_revision=None, expected_generation=None, expected_identity=None):
    piece = await lc.assert_piece_current(db, user_id=user_id, piece_id=piece_id,
        expected_revision=expected_revision, expected_generation=expected_generation)
    if expected_identity is not None and any(piece.get(key) != value for key, value in expected_identity.items()):
        raise HTTPException(409, detail={"code": "preparation_transition_identity_conflict"})
    await lc._assert_order_executable(db, user_id, piece.get("order_number"))
    await lc.assert_not_held(db, user_id=user_id, order_number=piece.get("order_number"),
        order_item_id=piece.get("order_item_id", ""), piece_id=piece_id)
    # Historical source events are immutable. Only a completed PR5 receipt may
    # resolve an EDIT; a notification acknowledgement never grants execution.
    events = await db[lc.AUDIT].find({"user_id": user_id, "order_number": piece.get("order_number"),
        "event_type": "salla_order_change", "change_type": "edit_options"}).to_list(10001)
    if len(events) > 10000:
        raise HTTPException(409, detail={"code": "preparation_edit_history_overflow"})
    for event in events:
        affected = (event.get("old_data") or {}).get("order_item_id") or (event.get("new_data") or {}).get("order_item_id")
        if affected != piece.get("order_item_id"):
            continue
        receipt = await db[lc.AUDIT].find_one({"user_id": user_id, "order_number": piece.get("order_number"),
            "event_type": "salla_edit_applied", "event_id": event["event_id"]})
        if not receipt:
            raise HTTPException(409, detail={"code": "preparation_edit_pending", "change_id": event.get("change_id"),
                "order_item_id": affected, "unit_index": piece.get("unit_index"),
                "generation": piece.get("generation", 0), "revision": piece.get("revision", 0)})
    return piece
