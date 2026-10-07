"""Dashboard-only projected settlement streams, without whole-period lists.

The caller owns its query and consumes rows in Mongo cursor order. No sorting,
classification, monetary conversion, or rounding occurs in these readers.
Callers stopping early must close the async generator (including on failure).
"""

SETTLEMENT_PROJECTION = {
    "_id": 0, "payment_method": 1, "adjustment_amount": 1,
    "original_amount": 1, "new_amount": 1,
}
WALLET_PROJECTION = {"_id": 0, "adjustment_amount": 1, "order_created_at": 1}


async def _rows(db, match, projection):
    cursor = db.payment_adjustments.find(match, projection).batch_size(128)
    try:
        async for row in cursor:
            yield row
    finally:
        await cursor.close()


def iter_dashboard_settlement_rows(db, match):
    """Async iterable for aggregate_settlements_by_provider(row_loader=...)."""
    return _rows(db, match, dict(SETTLEMENT_PROJECTION))


def iter_dashboard_wallet_adjustments(db, match):
    """Async iterable for the caller's existing wallet-window classification."""
    return _rows(db, match, dict(WALLET_PROJECTION))
