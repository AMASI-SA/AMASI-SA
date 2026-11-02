"""Fresh destination reads use the real GET adapters and never persist evidence."""

import socket
from copy import deepcopy
from types import SimpleNamespace

import pytest
from mongomock_motor import AsyncMongoMockClient

import order_engine.shipping_label_service as shipping
import store_delivery_destination as destination
from salla_shipping import CURRENT_SHIPPING
from store_delivery_domain import StoreDeliveryRuleError, assignment_snapshot


OWNER = "synthetic-destination-owner"
NUMBER = "9002001"
INTERNAL_ID = "synthetic-provider-order"
SHIPMENT_ID = "synthetic-superseded-shipment"
CARRIER = {"name": "مندوب الرياض", "id": "synthetic-store-courier"}


def sparse_order():
    return {
        "user_id": OWNER,
        "order_id": "synthetic-local-order-key",
        "order_number": NUMBER,
        "shipping_city": "جدة",
        "customer_city": "جدة",
        "shipping_company": CARRIER["name"],
        "shipping_company_code": CARRIER["id"],
        "salla_shipment_id": None,
        CURRENT_SHIPPING: {
            "company_name": CARRIER["name"],
            "company_code": CARRIER["id"],
            "shipment_id": None,
            "status": "pending",
            "carrier_updated_at": "2026-10-30T10:00:00+00:00",
            "superseded_shipment_ids": [SHIPMENT_ID],
        },
        "raw_by_source": {
            "salla_direct": {"id": INTERNAL_ID, "reference_id": NUMBER, "shipments": []},
        },
    }


def provider_shipment(city="Riyadh", **changes):
    return {
        "id": SHIPMENT_ID,
        "order_id": INTERNAL_ID,
        "courier_name": CARRIER["name"],
        "courier_id": CARRIER["id"],
        "meta": {"app_id": 0},
        "type": "shipment",
        "status": "created",
        "updated_at": "2026-11-01T10:00:00+00:00",
        "ship_to": {"city": city},
        **changes,
    }


def order_with_current_shipment_evidence(*rows):
    order = sparse_order()
    order[CURRENT_SHIPPING].update(
        shipment_id=SHIPMENT_ID,
        status="created",
        superseded_shipment_ids=[],
        shipment_updated_at="2026-11-01T10:00:00+00:00",
    )
    order["salla_shipment_id"] = SHIPMENT_ID
    order["raw_by_source"]["salla_direct"]["shipments"] = list(rows)
    return order


async def database_snapshot(db):
    return {
        name: await db[name].find({}).sort("_id", 1).to_list(None)
        for name in sorted(await db.list_collection_names())
    }


async def seed_case(case, order=None):
    order = deepcopy(order if order is not None else sparse_order())
    await case.db.unified_orders.insert_one(deepcopy(order))
    await case.db.store_delivery_assignments.insert_one({
        "user_id": OWNER, "id": "historical-assignment", "status": "delivered",
        "active": False, "delivery_fee_snapshot": 17.25,
    })
    await case.db.general_ledger.insert_one({"user_id": OWNER, "id": "historical-ledger", "amount": 17.25})
    return order, await database_snapshot(case.db)


@pytest.fixture
def provider(monkeypatch):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def local_connect(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            raise AssertionError("Provider destination tests prohibit live network access")
        return original_connect(sock, address)

    def local_connect_ex(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            raise AssertionError("Provider destination tests prohibit live network access")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", local_connect_ex)
    case = SimpleNamespace(
        db=AsyncMongoMockClient().fresh_destination_contract,
        order={
            "id": INTERNAL_ID,
            "reference_id": NUMBER,
            "status": {"slug": "completed"},
            "shipping": {"company": deepcopy(CARRIER)},
            "customer": {"city": "جدة"},
            "shipments": [],
        },
        shipments=[provider_shipment()],
        pagination=None,
        calls=[],
        scope_valid=True,
        global_shipments_forbidden=False,
        failure=None,
        on_read=None,
    )

    async def get_only(database, user_id, method, path, **kwargs):
        case.calls.append((method, path, deepcopy(kwargs)))
        case.scope_valid = case.scope_valid and database is case.db and user_id == OWNER
        assert database is case.db
        assert user_id == OWNER
        assert method == "GET", f"Destination resolution attempted provider mutation: {method} {path}"
        assert not {"json", "data"} & kwargs.keys()
        if case.failure is not None:
            raise case.failure
        if case.on_read is not None:
            await case.on_read(path)
        if path == f"/orders/{INTERNAL_ID}":
            return {"data": deepcopy(case.order)}
        if path == "/shipments":
            assert kwargs.get("params") == {"order_id": INTERNAL_ID, "per_page": 50}
            if case.global_shipments_forbidden:
                raise shipping.SallaError("Synthetic orders-only scope", status_code=403)
            return {"data": deepcopy(case.shipments), **({"pagination": deepcopy(case.pagination)} if case.pagination is not None else {})}
        if path == f"/orders/{INTERNAL_ID}/shipments":
            assert kwargs.get("params") == {"per_page": 50}
            return {"data": deepcopy(case.shipments), **({"pagination": deepcopy(case.pagination)} if case.pagination is not None else {})}
        raise AssertionError(f"Unexpected destination provider read: {path}")

    monkeypatch.setattr(shipping, "call_salla", get_only)
    yield case
    # A production exception handler must not hide a forbidden attempted write.
    assert case.scope_valid
    assert all(method == "GET" for method, _, _ in case.calls)
    assert all(not {"json", "data"} & kwargs.keys() for _, _, kwargs in case.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("orders_scope_only", [False, True], ids=["shipments-scope", "orders-scope-fallback"])
async def test_fresh_exact_provider_destination_can_override_sparse_superseded_local_context(provider, orders_scope_only):
    provider.global_shipments_forbidden = orders_scope_only
    order, before = await seed_case(provider)
    original_argument = deepcopy(order)

    city = await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert city == "Riyadh"
    assert order == original_argument
    assert await database_snapshot(provider.db) == before
    assert [path for _, path, _ in provider.calls] == [
        f"/orders/{INTERNAL_ID}",
        "/shipments",
        *([f"/orders/{INTERNAL_ID}/shipments"] if orders_scope_only else []),
    ]


@pytest.mark.asyncio
async def test_verified_other_city_is_returned_for_the_existing_coverage_rule_to_reject(provider):
    provider.shipments = [provider_shipment("جدة")]
    order, before = await seed_case(provider)

    city = await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert city == "جدة"
    with pytest.raises(StoreDeliveryRuleError, match="^driver_city_mismatch$"):
        assignment_snapshot(driver={
            "id": "synthetic-riyadh-driver", "name": "Synthetic driver", "city": "الرياض",
            "status": "active", "coverage_mode": "city", "delivery_fee": 23,
        }, shipping_city=city)
    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
async def test_fresh_shipment_supplies_carrier_when_provider_order_and_shipment_clock_are_sparse(provider):
    provider.order.pop("shipping")
    provider.shipments[0].pop("updated_at")
    order, before = await seed_case(provider)

    city = await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert city == "Riyadh"
    assert await database_snapshot(provider.db) == before
    assert [path for _, path, _ in provider.calls] == [f"/orders/{INTERNAL_ID}", "/shipments"]


@pytest.mark.asyncio
async def test_incomplete_provider_pagination_cannot_prove_one_current_shipment(provider):
    provider.pagination = {"currentPage": 1, "totalPages": 2, "total": 2}
    order, before = await seed_case(provider)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "reference_id"])
async def test_wrong_provider_order_identity_is_unavailable_not_a_city_fallback(provider, field):
    provider.order[field] = "different-provider-order"
    order, before = await seed_case(provider)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert await database_snapshot(provider.db) == before
    assert [path for _, path, _ in provider.calls] == [f"/orders/{INTERNAL_ID}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "refunded", "in_progress"])
async def test_provider_order_must_still_be_completed_for_assignment(provider, status):
    provider.order["status"] = {"slug": status}
    order, before = await seed_case(provider)

    with pytest.raises(StoreDeliveryRuleError, match="^store_courier_order_not_completed$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert await database_snapshot(provider.db) == before
    assert [path for _, path, _ in provider.calls] == [f"/orders/{INTERNAL_ID}"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["ambiguous", "archived-status", "archived-flag", "return", "wrong-carrier", "wrong-order", "missing-city", "no-current-shipment"],
)
async def test_unproven_provider_shipment_never_uses_stale_customer_city(provider, reason):
    row = provider.shipments[0]
    if reason == "ambiguous":
        provider.shipments.append(provider_shipment("Riyadh", id="second-active-shipment"))
    elif reason == "archived-status":
        row["status"] = "archived"
    elif reason == "archived-flag":
        row["archived"] = True
    elif reason == "return":
        row["type"] = "return"
    elif reason == "wrong-carrier":
        row["courier_id"] = "other-carrier"
    elif reason == "wrong-order":
        row["order_id"] = "other-order"
    elif reason == "missing-city":
        row["ship_to"] = {}
    elif reason == "no-current-shipment":
        provider.shipments = []
    order, before = await seed_case(provider)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert provider.calls
    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
async def test_fresh_order_carrier_conflict_is_unavailable(provider):
    provider.order["shipping"]["company"] = {"name": "Other carrier", "id": "other-carrier"}
    order, before = await seed_case(provider)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [shipping.SallaError("Synthetic unavailable", status_code=503), TimeoutError("Synthetic timeout")])
async def test_provider_failure_is_unavailable_and_has_no_local_side_effect(provider, failure):
    provider.failure = failure
    order, before = await seed_case(provider)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert provider.calls
    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
async def test_explicit_current_canonical_id_cannot_be_replaced_by_provider_other_id(provider):
    order = sparse_order()
    order[CURRENT_SHIPPING].update(shipment_id="different-current-shipment", status="created")
    order["salla_shipment_id"] = "different-current-shipment"
    order, before = await seed_case(provider, order)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
async def test_explicit_current_id_marked_superseded_cannot_be_reactivated_by_provider(provider):
    order = sparse_order()
    order[CURRENT_SHIPPING].update(shipment_id=SHIPMENT_ID, status="created")
    order["salla_shipment_id"] = SHIPMENT_ID
    order, before = await seed_case(provider, order)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
async def test_explicitly_older_provider_shipment_clock_cannot_override_current_fence(provider):
    order = sparse_order()
    order[CURRENT_SHIPPING].update(
        shipment_id=SHIPMENT_ID,
        status="created",
        superseded_shipment_ids=[],
        shipment_updated_at="2026-11-01T11:00:00+00:00",
    )
    order["salla_shipment_id"] = SHIPMENT_ID
    order, before = await seed_case(provider, order)

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert provider.calls
    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_clock", "accepted"),
    [
        pytest.param("2026-11-01T11:00:00+00:00", False, id="older-than-stored-cancellation"),
        pytest.param("2026-11-01T12:00:00+00:00", False, id="equal-to-stored-cancellation"),
        pytest.param(None, False, id="unknown-clock-cannot-displace-cancellation"),
        pytest.param("2026-11-01T13:00:00+00:00", True, id="newer-active-provider-evidence"),
    ],
)
async def test_provider_evidence_must_supersede_latest_stored_same_shipment_cancellation(provider, provider_clock, accepted):
    cancelled = provider_shipment("جدة", status="cancelled", updated_at="2026-11-01T12:00:00+00:00")
    order, before = await seed_case(provider, order_with_current_shipment_evidence(cancelled))
    original_argument = deepcopy(order)
    if provider_clock is None:
        provider.shipments[0].pop("updated_at")
    else:
        provider.shipments[0]["updated_at"] = provider_clock

    if accepted:
        assert await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order) == "Riyadh"
    else:
        with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
            await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert provider.calls
    assert order == original_argument
    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
async def test_equal_clock_provider_city_cannot_resolve_conflicting_stored_destinations(provider):
    clock = "2026-11-01T12:00:00+00:00"
    rows = [provider_shipment("Riyadh", updated_at=clock), provider_shipment("جدة", updated_at=clock)]
    order, before = await seed_case(provider, order_with_current_shipment_evidence(*rows))
    provider.shipments[0]["updated_at"] = clock

    with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert provider.calls
    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_clock", "accepted"),
    [(None, False), ("2026-11-01T13:00:00+00:00", True)],
    ids=["unknown-clock-keeps-carrier-conflict", "newer-provider-resolves-carrier-conflict"],
)
async def test_stored_shipment_inherits_enclosing_carrier_when_fencing_provider_reads(provider, provider_clock, accepted):
    stored = provider_shipment(updated_at="2026-11-01T12:00:00+00:00")
    for field in ("courier_name", "courier_id", "meta"):
        stored.pop(field)
    order = order_with_current_shipment_evidence(stored)
    order["raw_by_source"]["salla_direct"]["shipping"] = {
        "company": {"name": "Other carrier", "id": "other-carrier"},
    }
    order, before = await seed_case(provider, order)
    if provider_clock is None:
        provider.shipments[0].pop("updated_at")
    else:
        provider.shipments[0]["updated_at"] = provider_clock

    if accepted:
        assert await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order) == "Riyadh"
    else:
        with pytest.raises(StoreDeliveryRuleError, match="^shipping_city_required$"):
            await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert provider.calls
    assert await database_snapshot(provider.db) == before


@pytest.mark.asyncio
async def test_local_destination_fence_change_during_provider_read_discards_the_city(provider):
    order, before = await seed_case(provider)
    expected_after = None

    async def concurrent_change(path):
        nonlocal expected_after
        if path == "/shipments":
            await provider.db.unified_orders.update_one(
                {"user_id": OWNER, "order_number": NUMBER},
                {"$set": {f"{CURRENT_SHIPPING}.shipment_id": "concurrently-selected-shipment"}},
            )
            expected_after = await database_snapshot(provider.db)

    provider.on_read = concurrent_change
    with pytest.raises(StoreDeliveryRuleError, match="^shipment_destination_changed$"):
        await destination.resolve_assignment_city(provider.db, user_id=OWNER, order=order)

    assert expected_after is not None and expected_after != before
    assert await database_snapshot(provider.db) == expected_after
