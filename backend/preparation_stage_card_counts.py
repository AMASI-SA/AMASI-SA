"""Metadata-only employee stage badge counts shared by My Products and Monitoring.

The routes return counts, never product DTOs, pictures, customer content or
supplier file history. Scoped to the same canonical preparation actor.
"""
from preparation_progress_counts import progress_count, CHUNK
from preparation_supplier_dispatch import (
    PIECES, _waiting_query, piece_is_available_for_supplier_dispatch,
    annotate_waiting_pieces, _supplier_receipt_awaiting_handoff,
)


async def _waiting_ids(db, owner, employee):
    # Exact same prefilter and current-order eligibility as the waiting queue.
    projection = {"_id": 0, "piece_id": 1, "order_number": 1, "status": 1,
                  "experiment_archived_at": 1, "assignment_status": 1,
                  "supplier_dispatch_status": 1, "supplier_receiving_session_id": 1}
    cursor = db[PIECES].find(_waiting_query(owner, employee), projection).batch_size(CHUNK)
    selected = set()
    try:
        while True:
            rows = await cursor.to_list(CHUNK)
            if not rows:
                break
            valid = [row for row in rows if piece_is_available_for_supplier_dispatch(row)]
            if not valid:
                continue
            annotated = await annotate_waiting_pieces(db, user_id=owner, pieces=valid)
            selected.update(row["piece_id"] for row in annotated
                            if row.get("piece_id") and row.get("waiting_review_eligible") is True)
    finally:
        await cursor.close()
    return selected


async def _received_ids(db, owner, employee):
    # Match the received-from-supplier stage: an actual receipt not yet handed
    # off to the branch. 'ready' without receipt is not a receipt.
    query = {
        "user_id": owner,
        "responsible_employee_id": employee,
        "experiment_archived_at": {"$in": [None, ""]},
        "status": {"$ne": "cancelled"},
        "branch_handoff_at": {"$in": [None, ""]},
        "$or": [{"supplier_dispatch_status": {"$in": ["partial_received", "received"]}},
                {"status": "received"}],
    }
    projection = {"_id": 0, "piece_id": 1, "status": 1,
                  "supplier_dispatch_status": 1, "branch_handoff_at": 1}
    cursor = db[PIECES].find(query, projection).batch_size(CHUNK)
    selected = set()
    try:
        while True:
            rows = await cursor.to_list(CHUNK)
            if not rows:
                break
            selected.update(row["piece_id"] for row in rows
                            if row.get("piece_id") and _supplier_receipt_awaiting_handoff(row))
    finally:
        await cursor.close()
    return selected


async def progress_stage_card_counts(db, *, user_id, employee_id):
    progress = await progress_count(db, user_id=user_id, employee_id=employee_id)
    waiting = await _waiting_ids(db, user_id, employee_id)
    received = await _received_ids(db, user_id, employee_id)
    return {"ok": True, "read_only": True, "employee_id": employee_id,
            "remaining": progress["remaining"],
            "waiting_for_supplier": len(waiting),
            "received_from_supplier": len(received)}
