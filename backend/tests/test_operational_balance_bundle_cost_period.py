"""Only unused cost expansion is omitted; all historical witnesses stay in scope."""
import asyncio
import os
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
from urllib.parse import urlsplit

from bson import BSON
import pytest

import operational_remaining_bundle_sources as bundles
from demand_read_mongo_fixture import read_db, read_mongo_uri as demand_read_mongo_uri
from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_balance_sources import collect_sources
from operational_financial_activity_runtime import preparation_scope
from operational_financial_position import build_financial_position
from stock_component_consumption_service import PLANS, RECEIPTS, UNITS, selection_tokens
from test_operational_balance_remaining_products import recipe


OWNER = "synthetic-bundle-cost-period"
START = "2030-04-02T00:00:00+00:00"
NOW = "2030-04-05T00:00:00+00:00"
OLD = "2030-04-01T10:00:00+00:00"
CURRENT = "2030-04-03T10:00:00+00:00"


@pytest.fixture(scope="session")
def read_mongo_uri(tmp_path_factory, request):
    # Stock eligibility already owns the replica via the operational/MZ2 test
    # variables. Reuse only that explicit loopback capability; ordinary local
    # runs keep the demand fixture's isolated-server creation and cleanup.
    if not (os.environ.get("DEMAND_READ_TEST_MONGO_URI") or os.environ.get("DEMAND_READ_TEST_MONGOD")):
        supplied = {os.environ[name] for name in ("OPERATIONAL_TEST_MONGO_URI", "MZ2_TEST_MONGO_URI")
                    if os.environ.get(name)}
        if supplied:
            assert len(supplied) == 1, "Conflicting isolated Mongo test capabilities"
            uri = supplied.pop()
            parsed = urlsplit(uri)
            assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port
            assert parsed.username is None and parsed.password is None and parsed.path in ("", "/")
            yield uri
            return
    yield from demand_read_mongo_uri.__wrapped__(tmp_path_factory, request)


def raw(number, created=CURRENT):
    return {"id": "native-" + number, "reference_id": number,
            "created_at": created, "updated_at": CURRENT, "status": "in_progress",
            "currency": "SAR", "amounts": {"total": {"amount": "400", "currency": "SAR"}},
            "items": [{"id": "LINE", "product_id": "bundle", "quantity": 4,
                       "options": [{"id": "gift", "value": {"id": "yes"}}]}]}


def source_state():
    return {"owner_id": OWNER, "status": "active", "started_at": START,
            "openings": {}, "movements": [], "engine": {"as_of": NOW,
            "facts": {}, "obligations": {}, "issues": []}}


def stub_authority(monkeypatch):
    calls, projected = [], []

    async def authority(db, owner, orders, **options):
        assert owner == OWNER
        assert options == {"include_corrections": False, "include_frozen_metadata": True,
                           "parallel_reads": True}
        calls.append(("authority", [row["order_number"] for row in orders]))
        return {"lines": {}, "composite_products": {"bundle"}}

    async def read(db, owner, name, query, fields, limit=20000):
        assert owner == query["user_id"] == OWNER
        calls.append((name, deepcopy(query), deepcopy(fields), limit))
        return []

    def resolve(item, *, order_number, **options):
        projected.append(order_number)
        return {"calculation_cost_available": False,
                "missing_cost_reasons": ["bundle_snapshot_missing"]}

    monkeypatch.setattr("order_cost_authority.load_order_cost_authority", authority)
    monkeypatch.setattr("order_cost_authority.resolve_order_line_cost", resolve)
    monkeypatch.setattr(bundles, "_read", read)
    return calls, projected


@pytest.mark.asyncio
@pytest.mark.parametrize("old_created", [OLD, START,
    {"date": "2030-04-02 03:00:00", "timezone": "Asia/Riyadh"},
    datetime(2030, 4, 2), datetime(2030, 4, 2, tzinfo=timezone.utc)])
async def test_cost_period_omits_only_final_old_expansion_after_full_witness_reads(monkeypatch, old_created):
    calls, projected = stub_authority(monkeypatch)
    rows = [raw("old", old_created), raw("current")]
    before = deepcopy(rows)
    full = await bundles.load_remaining_bundles(object(), OWNER, rows, NOW,
        preparation_pieces=[], native_matches={})
    full_calls = deepcopy(calls)
    assert projected == ["old", "current"]
    calls.clear(); projected.clear()
    scoped = await bundles.load_remaining_bundles(object(), OWNER, rows, NOW,
        preparation_pieces=[], native_matches={}, cost_started_at=START)
    assert calls == full_calls
    assert projected == ["current"]
    assert scoped == {key: value for key, value in full.items() if key[0] == "current"}
    assert rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize("created", [None, "", "bad", "2030-04-01", "2030-04-01T10:00:00",
    {"date": "2030-04-01T10:00:00", "timezone": "Not/AZone"}, [],
    "2030-04-06T00:00:00+00:00"])
async def test_uncertain_and_future_dates_keep_the_existing_projection(monkeypatch, created):
    calls, projected = stub_authority(monkeypatch)
    rows = [raw("uncertain", created)]
    expected = await bundles.load_remaining_bundles(object(), OWNER, rows, NOW,
        preparation_pieces=[], native_matches={})
    full_calls = deepcopy(calls)
    calls.clear(); projected.clear()
    actual = await bundles.load_remaining_bundles(object(), OWNER, rows, NOW,
        preparation_pieces=[], native_matches={}, cost_started_at=START)
    assert actual == expected and projected == ["uncertain"] and calls == full_calls


@pytest.mark.asyncio
async def test_old_current_duplicate_reference_is_not_hidden_before_normalization(monkeypatch):
    calls, projected = stub_authority(monkeypatch)
    rows = [raw("duplicate", OLD), raw("duplicate"), raw("current")]
    result = await bundles.load_remaining_bundles(object(), OWNER, rows, NOW,
        preparation_pieces=[], native_matches={}, cost_started_at=START)
    assert calls[0] == ("authority", ["current"])
    assert projected == ["current"] and {key[0] for key in result} == {"current"}


@pytest.mark.asyncio
async def test_old_malformed_items_still_fail_before_any_authority_query(monkeypatch):
    calls, _ = stub_authority(monkeypatch)
    rows = [dict(raw("old", OLD), items=42), raw("current")]
    for options in ({}, {"cost_started_at": START}):
        with pytest.raises(TypeError, match="not iterable"):
            await bundles.load_remaining_bundles(object(), OWNER, rows, NOW,
                preparation_pieces=[], native_matches={}, **options)
    assert calls == []


@pytest.mark.asyncio
async def test_historical_witness_failure_is_not_skipped_when_no_costs_are_needed(monkeypatch):
    stub_authority(monkeypatch)

    async def rejected(*args, **kwargs):
        raise ValueError("bundle_remaining_read_coverage_invalid")

    monkeypatch.setattr(bundles, "_read", rejected)
    with pytest.raises(ValueError, match="^bundle_remaining_read_coverage_invalid$"):
        await bundles.load_remaining_bundles(object(), OWNER, [raw("old", OLD)], NOW,
            preparation_pieces=[], native_matches={}, cost_started_at=START)


@pytest.mark.asyncio
async def test_cancellation_after_witnesses_prevents_scoped_projection(monkeypatch):
    _, projected = stub_authority(monkeypatch)
    stopped = Event()
    original = bundles._read

    async def stop_after_read(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[2] == UNITS:
            stopped.set()
        return result

    monkeypatch.setattr(bundles, "_read", stop_after_read)
    with preparation_scope(stopped), pytest.raises(asyncio.CancelledError):
        await bundles.load_remaining_bundles(object(), OWNER, [raw("old", OLD), raw("current")], NOW,
            preparation_pieces=[], native_matches={}, cost_started_at=START)
    assert projected == []


async def seed_pair(db):
    await db.mezan_product_cost_profiles_v2.insert_one(
        {"user_id": OWNER, "salla_product_id": "bundle", "base_cost": "15"})
    await db.mezan_suppliers_v2.insert_one({"user_id": OWNER, "id": "supplier", "name": "Synthetic supplier"})
    await db.mezan_bundle_definitions_v2.insert_one(
        {"user_id": OWNER, "product_id": "bundle", "product_type": "composite"})
    rows = [raw("old", OLD), raw("current")]
    for row in rows:
        number, created = row["reference_id"], row["created_at"]
        item = bundles.normalized_orders([row])[0]["items"][0]
        line = item["order_item_id"]
        snapshot = recipe(row)
        await db.unified_orders.insert_one({"user_id": OWNER, "order_number": number,
                                            "raw_by_source": {"salla_direct": row}})
        await db.mezan_component_order_lifecycle_v1.insert_one({"user_id": OWNER,
            "order_number": number, "bundle_intake_snapshots": {line: snapshot}})
        await db[PLANS].insert_one({"_id": "plan-" + number, "user_id": OWNER,
            "order_id": number, "bundle_snapshots": {line: snapshot}, "lines": [item]})
        for index in range(1, 5):
            leaves = [dict(leaf, state="awaiting_supplier", receipt_ids=[], reserved_quantity="0",
                          received_quantity="0", consumed_quantity="0") for leaf in snapshot["leaves"]]
            await db[UNITS].insert_one({"_id": f"unit-{number}-{index}", "user_id": OWNER,
                "plan_id": "plan-" + number, "order_id": number, "order_line_id": line,
                "unit_index": index, "state": "reserved", "product_id": "bundle",
                "bundle_leaves": leaves, "created_at": created, "allocations": [],
                "selected_context": [list(value) for value in selection_tokens(item)]})
    return rows


async def shared_allocation(db, duplicate):
    await db[RECEIPTS].insert_one({"id": "shared-receipt", "user_id": OWNER,
        "status": "posted", "source_type": "purchase_invoice", "source_id": "shared-invoice",
        "purchase_invoice_id": "shared-invoice", "posted_at": CURRENT,
        "quantity": "2" if duplicate else "1", "item_type": "product", "product_id": "rose",
        "salla_product_id": "rose", "variant_id": None})
    await db.purchase_invoices.insert_one({"id": "shared-invoice", "user_id": OWNER,
                                          "state": "approved", "supplier_id": "supplier"})
    for number in ("old", "current"):
        unit = await db[UNITS].find_one({"_id": f"unit-{number}-1"})
        leaf = next(value for value in unit["bundle_leaves"] if value["leaf_id"] == "rose")
        leaf.update(receipt_ids=["shared-receipt"], reserved_quantity="1", received_quantity="1")
        unit["allocations"] = [{"id": "same-allocation" if duplicate else "allocation-" + number,
            "bundle_leaf_id": "rose", "quantity": "1", "lot_id": "shared-receipt",
            "location_id": "synthetic-location", "inventory_identity": {"item_type": "product",
                "product_id": "rose", "salla_product_id": "rose", "variant_id": None}}]
        await db[UNITS].replace_one({"_id": unit["_id"]}, unit)


async def snapshot(db):
    return {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "duplicate", "overallocated"])
async def test_actual_mongo_keeps_historical_global_witnesses_and_full_financial_parity(monkeypatch, read_db, damage):
    rows = await seed_pair(read_db)
    if damage:
        await shared_allocation(read_db, damage == "duplicate")
    before, state = await snapshot(read_db), source_state()
    original = bundles.load_remaining_bundles
    queries = []
    collection_type = type(read_db.unified_orders)
    real_find = collection_type.find

    def observed(collection, query=None, projection=None, *args, **kwargs):
        if collection.database.name == read_db.name:
            queries.append(BSON.encode({"collection": collection.name,
                "query": query, "projection": projection, "kwargs": kwargs}))
        return real_find(collection, query, projection, *args, **kwargs)

    monkeypatch.setattr(collection_type, "find", observed)
    full = await original(read_db, OWNER, rows, NOW, preparation_pieces=[], native_matches={})
    full_queries = Counter(queries)
    queries.clear()
    current = await original(read_db, OWNER, rows, NOW, preparation_pieces=[], native_matches={},
                             cost_started_at=START)
    assert Counter(queries) == full_queries
    assert current == {key: value for key, value in full.items() if key[0] == "current"}
    if damage:
        expected = "bundle_allocation_identity_ambiguous" if damage == "duplicate" else "bundle_supplier_receipt_overallocated"
        assert any(issue["code"] == expected for value in current.values() for issue in value["issues"])
        assert all(value["complete"] is False for value in current.values())

    async def historical_projection(*args, **kwargs):
        kwargs.pop("cost_started_at", None)
        return await original(*args, **kwargs)

    monkeypatch.setattr("operational_financial_activity.now", lambda: NOW)
    with monkeypatch.context() as unscoped:
        unscoped.setattr(bundles, "load_remaining_bundles", historical_projection)
        queries.clear()
        previous_sources = await collect_sources(read_db, OWNER, START, NOW, operational_state=state)
        previous_queries = Counter(queries)
    queries.clear()
    current_sources = await collect_sources(read_db, OWNER, START, NOW, operational_state=state)
    assert Counter(queries) == previous_queries
    assert current_sources == previous_sources
    previous_state = reconcile(state, previous_sources, NOW)
    current_state = reconcile(state, current_sources, NOW)
    assert current_state == previous_state == reconcile(current_state, current_sources, NOW)
    assert report(current_state, as_of=NOW) == report(previous_state, as_of=NOW)
    assert build_financial_position(current_state, generated_at=NOW) == build_financial_position(previous_state, generated_at=NOW)
    assert state == source_state() and await snapshot(read_db) == before
