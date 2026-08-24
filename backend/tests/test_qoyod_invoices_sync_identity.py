import asyncio

from integrations.qoyod.qoyod_invoices_sync import (
    _sync_salla_order_id,
    _write_invoice_batches,
)


def test_synced_invoice_identity_is_stable_and_unique():
    assert _sync_salla_order_id("1110") == "qoyod-sync:1110"
    assert _sync_salla_order_id("1110") == _sync_salla_order_id("1110")
    assert _sync_salla_order_id("1110") != _sync_salla_order_id("1111")


class _BulkResult:
    def __init__(self, operation_count):
        self.upserted_count = min(2, operation_count)
        self.modified_count = max(0, operation_count - 2)


class _InvoiceCollection:
    def __init__(self):
        self.bulk_calls = []

    async def find_one(self, query, projection=None):
        return None

    async def bulk_write(self, operations, *, ordered):
        self.bulk_calls.append((operations, ordered))
        return _BulkResult(len(operations))

    async def update_one(self, *args, **kwargs):
        raise AssertionError("the successful hot path must use bulk_write")


def test_invoice_sync_batches_database_upserts():
    rows = [{
        "filter": {
            "user_id": "main",
            "qoyod_invoice_id": str(invoice_id),
        },
        "update": {
            "$set": {"last_sync_at": "2026-08-24T19:00:00+00:00"},
            "$setOnInsert": {
                "salla_order_id": _sync_salla_order_id(str(invoice_id)),
            },
        },
    } for invoice_id in range(1, 1202)]
    collection = _InvoiceCollection()

    result = asyncio.run(_write_invoice_batches(collection, rows))

    assert result["created"] == 6
    assert result["updated"] == 1195
    assert result["write_batches"] == 3
    assert result["bulk_fallback_batches"] == 0
    assert len(collection.bulk_calls) == 3
    assert [len(call[0]) for call in collection.bulk_calls] == [500, 500, 201]
    assert all(ordered is False for _, ordered in collection.bulk_calls)
