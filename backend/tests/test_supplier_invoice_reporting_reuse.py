"""Repeated report batches reuse only pure work; financial proof stays fresh."""
from copy import deepcopy
from datetime import timedelta, timezone
from decimal import Decimal

import pytest
from mongomock_motor import AsyncMongoMockClient

from accounting_ledger_v2 import GROUPS_COLLECTION
import supplier_invoice_reporting_costs as reporting
import supplier_native_invoice_v2 as native
from test_supplier_invoice_reporting_costs import (
    APPROVED, NOW, OWNER, ReadOnlyDB, catalog, event, invoice, item, piece,
)


async def shared_invoice_database(monkeypatch, *, count=501, native_invoice=True, price=1900):
    monkeypatch.setattr(reporting, "_now", lambda: NOW)
    orders, pieces = [], []
    for index in range(count):
        number = f"{index:06d}"
        row = item()
        row.update(order_item_id="line-" + number, source_item_id="line-" + number)
        orders.append({"order_number": number, "items": [row]})
        received = piece(pid="piece-" + number)
        received.update(order_number=number, order_item_id=row["order_item_id"])
        pieces.append(received)
    inv = invoice(pids=tuple(row["piece_id"] for row in pieces), price=price)
    groups = []
    if native_invoice:
        inv.update(mz2_financial_contract="mz2_supplier_invoice_v1", mz2_txn_group_id="native-group")
        groups = [{"user_id": OWNER, "txn_group_id": "native-group", "status": "posted",
                   "metadata": {"supplier_invoice_id": inv["id"], "invoice_digest": native.invoice_digest(inv)}}]
    db = AsyncMongoMockClient().supplier_invoice_reporting_reuse
    data = {reporting.PIECES: pieces, reporting.EVENTS: [event(inv, row) for row in pieces],
            reporting.INVOICES: [inv], reporting.SUPPLIERS: [{"user_id": OWNER, "id": "supplier"}],
            GROUPS_COLLECTION: groups}
    for name, rows in data.items():
        if rows:
            await db[name].insert_many(deepcopy(rows))
    return db, orders


def resolved_orders(orders, authority):
    results = []
    for order in orders:
        row = order["items"][0]
        estimated = catalog(row)
        results.append(reporting.resolve_supplier_invoice_reporting_cost(
            row, order_number=order["order_number"], catalog_cost=estimated,
            historical_cost=estimated, supplier_authority=authority))
    return results


@pytest.mark.asyncio
async def test_shared_invoice_across_501_orders_allocates_and_hashes_once(monkeypatch):
    db, orders = await shared_invoice_database(monkeypatch)
    calculations, digests = [], []
    original_amounts, original_digest = reporting._invoice_amounts, native.invoice_digest

    def amounts(inv):
        calculations.append(inv["id"])
        return original_amounts(inv)

    def digest(inv):
        digests.append(inv["id"])
        return original_digest(inv)

    monkeypatch.setattr(reporting, "_invoice_amounts", amounts)
    monkeypatch.setattr(native, "invoice_digest", digest)
    readonly = ReadOnlyDB(db)
    authority = await reporting.load_supplier_invoice_reporting_costs(readonly, OWNER, orders)
    results = resolved_orders(orders, authority)
    assert len(results) == 501 and all(row["calculation_cost_available"] for row in results)
    assert sum(row["line_total"] for row in results) == 501 * 21
    assert authority["read_batches"] == 3
    assert calculations == ["invoice"]
    assert digests == ["invoice"]
    # Receipt, invoice, supplier, catalog and native journal proof are still
    # read afresh in every batch, under the same owner/order scopes.
    assert len(readonly.reads) == 18


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "duplicate_invoice", "deleted_invoice", "reversed_invoice", "malformed_invoice_id",
    "duplicate_supplier", "deleted_supplier", "reversed_journal", "duplicate_journal",
    "draft_journal", "changed_journal_digest",
])
async def test_cached_pure_work_cannot_reuse_earlier_authority(monkeypatch, change):
    monkeypatch.setattr(reporting, "CHUNK_SIZE", 2)
    db, orders = await shared_invoice_database(monkeypatch, count=5)
    original_read, reads = reporting._read, 0

    async def changing_read(database, collection, query, *, projection=None):
        nonlocal reads
        if collection == reporting.INVOICES:
            reads += 1
            if reads == 2:
                if change == "duplicate_invoice":
                    duplicate = await db[reporting.INVOICES].find_one({}, {"_id": 0})
                    await db[reporting.INVOICES].insert_one(duplicate)
                elif change == "deleted_invoice":
                    await db[reporting.INVOICES].delete_many({})
                elif change == "reversed_invoice":
                    await db[reporting.INVOICES].update_one({}, {"$set": {"reversed_at": APPROVED}})
                elif change == "malformed_invoice_id":
                    await db[reporting.INVOICES].update_one({}, {"$set": {"id": True}})
                elif change == "duplicate_supplier":
                    await db[reporting.SUPPLIERS].insert_one({"user_id": OWNER, "id": "supplier"})
                elif change == "deleted_supplier":
                    await db[reporting.SUPPLIERS].delete_many({})
                elif change == "reversed_journal":
                    await db[GROUPS_COLLECTION].insert_one({"user_id": OWNER, "txn_group_id": "reversal",
                        "reversal_of_txn_group_id": "native-group", "status": "posted"})
                elif change == "duplicate_journal":
                    duplicate = await db[GROUPS_COLLECTION].find_one({}, {"_id": 0})
                    await db[GROUPS_COLLECTION].insert_one(duplicate)
                elif change == "draft_journal":
                    await db[GROUPS_COLLECTION].update_one({}, {"$set": {"status": "draft"}})
                elif change == "changed_journal_digest":
                    await db[GROUPS_COLLECTION].update_one({}, {"$set": {"metadata.invoice_digest": "changed"}})
        return await original_read(database, collection, query, projection=projection)

    monkeypatch.setattr(reporting, "_read", changing_read)
    authority = await reporting.load_supplier_invoice_reporting_costs(ReadOnlyDB(db), OWNER, orders)
    results = resolved_orders(orders, authority)
    assert [row["calculation_cost_available"] for row in results] == [True, True, False, False, False]
    assert [row["line_total"] for row in results[:2]] == [21, 21]
    assert all(authority["lines"][(order["order_number"], order["items"][0]["order_item_id"])]["units"][1]["product"] is None
               for order in orders[2:])


@pytest.mark.asyncio
@pytest.mark.parametrize("native_invoice", [False, True])
async def test_same_total_header_cannot_hide_changed_invoice_lines(monkeypatch, native_invoice):
    monkeypatch.setattr(reporting, "CHUNK_SIZE", 2)
    db, orders = await shared_invoice_database(monkeypatch, count=5, native_invoice=native_invoice)
    original_read, reads = reporting._read, 0
    original_amounts, allocations = reporting._invoice_amounts, []

    def amounts(inv):
        allocations.append(inv["lines"][0]["product_unit_price_halalas"])
        return original_amounts(inv)

    async def changing_read(database, collection, query, *, projection=None):
        nonlocal reads
        if collection == reporting.INVOICES:
            reads += 1
            if reads == 2:
                inv = await db[reporting.INVOICES].find_one({})
                line = inv["lines"][0]
                line.update(product_unit_price_halalas=1100, product_total_halalas=5500,
                            services_total_halalas=4000,
                            services=[{"service_id": "manual", "service_name": "Manual service",
                                       "quantity_per_piece": 1, "unit_price_halalas": 800,
                                       "total_halalas": 4000}])
                # Invoice IDs, all headers and totals stay exactly unchanged.
                await db[reporting.INVOICES].update_one({}, {"$set": {"lines": inv["lines"]}})
        return await original_read(database, collection, query, projection=projection)

    monkeypatch.setattr(reporting, "_read", changing_read)
    monkeypatch.setattr(reporting, "_invoice_amounts", amounts)
    authority = await reporting.load_supplier_invoice_reporting_costs(ReadOnlyDB(db), OWNER, orders)
    results = resolved_orders(orders, authority)
    if native_invoice:
        assert [row["calculation_cost_available"] for row in results] == [True, True, False, False, False]
        assert allocations == [1900]  # Fresh native digest rejects changed lines before allocation.
    else:
        assert all(row["calculation_cost_available"] for row in results)
        assert [row["line_total"] for row in results] == [21] * 5
        assert allocations == [1900, 1100]
        for order in orders[2:]:
            unit = authority["lines"][(order["order_number"], order["items"][0]["order_item_id"])]["units"][1]
            assert Decimal(unit["product"]["amount"]) == 11
            assert Decimal(unit["services"]["manual"]["amount"]) == 8


@pytest.mark.asyncio
async def test_equal_python_boolean_is_not_a_cached_numeric_amount(monkeypatch):
    monkeypatch.setattr(reporting, "CHUNK_SIZE", 2)
    db, orders = await shared_invoice_database(monkeypatch, count=5, native_invoice=False, price=1)
    original_read, reads = reporting._read, 0

    async def changing_read(database, collection, query, *, projection=None):
        nonlocal reads
        if collection == reporting.INVOICES:
            reads += 1
            if reads == 2:
                await db[reporting.INVOICES].update_one({}, {"$set": {"lines.0.product_unit_price_halalas": True}})
        return await original_read(database, collection, query, projection=projection)

    monkeypatch.setattr(reporting, "_read", changing_read)
    result = await reporting.load_supplier_invoice_reporting_costs(ReadOnlyDB(db), OWNER, orders)
    assert [row["calculation_cost_available"] for row in resolved_orders(orders, result)] == [True, True, False, False, False]


@pytest.mark.parametrize("left,right", [
    (1, True), (1, 1.0), (0.0, -0.0), (Decimal("1"), Decimal("1.0")),
    (APPROVED, APPROVED.astimezone(timezone(timedelta(hours=3)))),
])
def test_digest_or_validation_distinct_values_do_not_match_the_memo(left, right):
    assert not reporting._same_invoice_value({"nested": [left]}, {"nested": [right]})


@pytest.mark.asyncio
@pytest.mark.parametrize("byte_limit", [0, 1, 8 * 1024 * 1024])
async def test_byte_limit_changes_only_recomputation_not_financial_results(monkeypatch, byte_limit):
    monkeypatch.setattr(reporting, "CHUNK_SIZE", 2)
    monkeypatch.setattr(reporting, "INVOICE_MEMO_MAX_BYTES", byte_limit)
    db, orders = await shared_invoice_database(monkeypatch, count=5)
    original_amounts, allocations = reporting._invoice_amounts, []
    original_memo, observed = reporting._InvoiceCalculationMemo, []

    class ObservedMemo(original_memo):
        def remember(self, *args):
            result = super().remember(*args)
            observed.append((len(self.entries), self.retained_bytes))
            return result

    def amounts(inv):
        allocations.append(inv["id"])
        return original_amounts(inv)

    monkeypatch.setattr(reporting, "_InvoiceCalculationMemo", ObservedMemo)
    monkeypatch.setattr(reporting, "_invoice_amounts", amounts)
    readonly = ReadOnlyDB(db)
    authority = await reporting.load_supplier_invoice_reporting_costs(readonly, OWNER, orders)
    assert [row["line_total"] for row in resolved_orders(orders, authority)] == [21] * 5
    assert all(row["calculation_cost_available"] for row in resolved_orders(orders, authority))
    assert len(allocations) == (1 if byte_limit > 1 else 3)
    assert observed and all(count <= reporting.INVOICE_MEMO_MAX_ENTRIES and size <= byte_limit for count, size in observed)
    assert len(readonly.reads) == 18


@pytest.mark.asyncio
async def test_lru_eviction_recomputes_without_dropping_received_costs(monkeypatch):
    monkeypatch.setattr(reporting, "CHUNK_SIZE", 2)
    monkeypatch.setattr(reporting, "INVOICE_MEMO_MAX_ENTRIES", 1)
    db, orders = await shared_invoice_database(monkeypatch, count=6, native_invoice=False)
    pieces = await db[reporting.PIECES].find({}, {"_id": 0}).to_list(None)
    await db[reporting.INVOICES].delete_many({})
    await db[reporting.EVENTS].delete_many({})
    for offset, name in enumerate(("first", "second")):
        received = pieces[offset::2]
        inv = invoice(identity=name, pids=tuple(row["piece_id"] for row in received))
        await db[reporting.INVOICES].insert_one(inv)
        await db[reporting.EVENTS].insert_many([event(inv, row) for row in received])
    allocations, original_amounts = [], reporting._invoice_amounts

    def amounts(inv):
        allocations.append(inv["id"])
        return original_amounts(inv)

    monkeypatch.setattr(reporting, "_invoice_amounts", amounts)
    authority = await reporting.load_supplier_invoice_reporting_costs(ReadOnlyDB(db), OWNER, orders)
    results = resolved_orders(orders, authority)
    assert all(row["calculation_cost_available"] and row["line_total"] == 21 for row in results)
    assert allocations == ["first", "second"] * 3


@pytest.mark.asyncio
async def test_full_invoice_unit_limit_is_not_bypassed_by_selected_batch(monkeypatch):
    monkeypatch.setattr(reporting, "CHUNK_SIZE", 2)
    monkeypatch.setattr(reporting, "MAX_COST_UNITS", 3)
    db, orders = await shared_invoice_database(monkeypatch, count=5)
    authority = await reporting.load_supplier_invoice_reporting_costs(ReadOnlyDB(db), OWNER, orders)
    assert all(not row["calculation_cost_available"] for row in resolved_orders(orders, authority))


@pytest.mark.asyncio
async def test_request_finished_memo_cannot_cross_into_next_report(monkeypatch):
    monkeypatch.setattr(reporting, "CHUNK_SIZE", 2)
    db, orders = await shared_invoice_database(monkeypatch, count=5)
    before = await reporting.load_supplier_invoice_reporting_costs(ReadOnlyDB(db), OWNER, orders)
    assert all(row["calculation_cost_available"] for row in resolved_orders(orders, before))
    await db[GROUPS_COLLECTION].update_one({}, {"$set": {"status": "draft"}})
    after = await reporting.load_supplier_invoice_reporting_costs(ReadOnlyDB(db), OWNER, orders)
    assert all(not row["calculation_cost_available"] for row in resolved_orders(orders, after))
