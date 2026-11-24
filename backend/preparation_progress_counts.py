"""Count-only supplier-day reads, shared by My Products and Monitoring.

No workspace DTOs, images, products, writes or background jobs. Date cohorts
are based on current supplier dispatch (Riyadh), as in My Products.
"""
from preparation_progress_dates import progress_day
from preparation_section_reads import _nonterminal_numbers
from supplier_dispatch_pdf import (
    _active_remaining_dispatch_pieces, _piece_received_from_dispatch_supplier,
)

PAGE_SIZE = 7
CHUNK = 500
PIECE_FIELDS = (
    "piece_id", "order_number", "status", "experiment_archived_at",
    "responsible_employee_id", "supplier_id", "supplier_dispatch_id",
    "supplier_dispatch_status", "supplier_receiving_history.supplier_id",
    "supplier_receiving_history.received_at", "supplier_receiving_history.invoice_id",
)
DISPATCH_FIELDS = ("id", "supplier_id", "sent_by_id", "sent_at", "status",
                   "piece_ids", "source_files.cards.piece_id")


def _projection(fields):
    return {"_id": 0, **{field: 1 for field in fields}}


def _member_ids(row):
    return set(row.get("piece_ids") or [
        card["piece_id"] for source in row.get("source_files") or []
        for card in source.get("cards") or [] if card.get("piece_id")
    ])


def _dispatch_member(piece, dispatch, members):
    return (piece.get("supplier_dispatch_id") == dispatch.get("id")
            and piece.get("supplier_id") == dispatch.get("supplier_id")
            and piece.get("piece_id") in members)


def _current_member(piece, dispatch, employee, members):
    return (piece.get("responsible_employee_id") == employee
            and _dispatch_member(piece, dispatch, members))


def _chunks(values):
    values = sorted(set(values) - {None, ""})
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


async def _directory(db, owner, employee):
    from preparation_supplier_dispatch import DISPATCHES, PIECES
    cursor = db[PIECES].find({
        "user_id": owner, "responsible_employee_id": employee,
        "status": {"$ne": "cancelled"},
        "experiment_archived_at": {"$in": [None, ""]},
        "supplier_dispatch_status": {"$ne": "received"},
        "supplier_dispatch_id": {"$nin": [None, ""]},
    }, _projection(PIECE_FIELDS)).batch_size(CHUNK)
    days = {}
    try:
        while True:
            candidates = await cursor.to_list(CHUNK)
            if not candidates:
                break
            ids = sorted({p["supplier_dispatch_id"] for p in candidates
                          if p.get("supplier_dispatch_id")})
            rows = await db[DISPATCHES].find({
                "user_id": owner, "sent_by_id": employee, "id": {"$in": ids},
                "status": {"$ne": "building"},
            }, _projection(DISPATCH_FIELDS)).to_list(None)
            by_dispatch = {row["id"]: row for row in rows}
            membership = {key: _member_ids(row) for key, row in by_dispatch.items()}
            eligible = await _nonterminal_numbers(db, owner, sorted({
                p["order_number"] for p in candidates if p.get("order_number")
            }))
            grouped = {}
            for piece in candidates:
                row = by_dispatch.get(piece.get("supplier_dispatch_id"))
                if row and _current_member(piece, row, employee, membership[row["id"]]):
                    grouped.setdefault(row["id"], []).append(piece)
            for identity, members in grouped.items():
                row = by_dispatch[identity]
                active = await _active_remaining_dispatch_pieces(
                    db, owner, row, members, eligible=eligible)
                if active:
                    days.setdefault(progress_day(row.get("sent_at")), set()).update(
                        p["piece_id"] for p in active if p.get("piece_id"))
    finally:
        await cursor.close()
    return days


async def progress_count(db, *, user_id, employee_id):
    days = await _directory(db, user_id, employee_id)
    return {"ok": True, "read_only": True, "employee_id": employee_id,
            "remaining": len(set().union(*days.values())) if days else 0}


async def _received_by_day(db, owner, employee, keys):
    from preparation_supplier_dispatch import DISPATCHES, PIECES
    # Filter dispatch metadata to this page of dates, including fully received
    # files on dates that still have outstanding pieces. No historic detail DTO.
    date_expr = {"$dateToString": {
        "date": {"$convert": {"input": "$sent_at", "to": "date",
                             "onError": None, "onNull": None}},
        "format": "%Y-%m-%d", "timezone": "Asia/Riyadh",
        "onNull": "__unknown__",
    }}
    cursor = db[DISPATCHES].find({
        "user_id": owner, "sent_by_id": employee, "status": {"$ne": "building"},
        "$expr": {"$in": [date_expr, keys]},
    }, _projection(DISPATCH_FIELDS)).batch_size(CHUNK)
    result = {key: set() for key in keys}
    try:
        while True:
            rows = await cursor.to_list(CHUNK)
            if not rows:
                break
            by_dispatch = {row["id"]: row for row in rows
                           if progress_day(row.get("sent_at")) in result}
            membership = {identity: _member_ids(row)
                          for identity, row in by_dispatch.items()}
            if not by_dispatch:
                continue
            # Receipt may transfer custody to a different employee. The
            # committed supplier file and exact current dispatch identity
            # attribute it to its sender without counting old reassignments.
            for ids in _chunks(set().union(*membership.values())):
                pieces = db[PIECES].find({
                    "user_id": owner, "piece_id": {"$in": ids},
                    "supplier_dispatch_id": {"$in": list(by_dispatch)},
                }, _projection(PIECE_FIELDS)).batch_size(CHUNK)
                try:
                    async for piece in pieces:
                        identity = piece.get("supplier_dispatch_id")
                        row = by_dispatch.get(identity)
                        if (row and _dispatch_member(piece, row, membership[identity])
                                and not piece.get("experiment_archived_at")
                                and _piece_received_from_dispatch_supplier(piece, row)):
                            result[progress_day(row.get("sent_at"))].add(piece["piece_id"])
                finally:
                    await pieces.close()
    finally:
        await cursor.close()
    return result


async def progress_day_counts(db, *, user_id, employee_id, after=""):
    days = await _directory(db, user_id, employee_id)
    keys = sorted(days, key=lambda key: (key == "__unknown__", key))
    if after:
        keys = [key for key in keys if (key == "__unknown__", key) >
                (after == "__unknown__", after)]
    page = keys[:PAGE_SIZE]
    receipts = await _received_by_day(db, user_id, employee_id, page) if page else {}
    result = []
    for key in page:
        remaining = days[key]
        received = receipts[key] - remaining
        result.append({"key": key, "assigned": len(remaining | received),
                       "received": len(received), "remaining": len(remaining)})
    return {"ok": True, "read_only": True, "employee_id": employee_id,
            "date_basis": "supplier_dispatched_at", "days": result,
            "next_cursor": page[-1] if len(keys) > PAGE_SIZE else None}
