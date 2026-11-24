"""Exercise the installed status wrapper, helper and original snapshot together."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mongomock_motor import AsyncMongoMockClient
import pytest

# This is the same package bootstrap used by server.py, including the installer.
from integrations.qoyod_manual import auto_send
from qoyod_auto_unified import installer
from salla_integration import sync
from salla_integration.status_snapshot_completion import CONNECTOR, validate_update


OWNER = "synthetic-status-owner"
NUMBER = "synthetic-status-order"


@pytest.fixture
def native_doc():
    return {
        "order_number": NUMBER, "order_status": "completed", "order_status_slug": "completed",
        "payment_method": "bank", "payment_status": "paid", "paid_amount": 40.0,
        "remaining_amount": 0.0, "has_remaining_amount": False,
        "customer_name": "Synthetic customer",
    }


def test_production_bootstrap_installs_wrapper_once():
    installed = sync._refresh_plan_b_status_snapshot
    assert auto_send._unified_source_patch_installed is True
    assert installed.__qualname__.endswith(".refresh_status_snapshot_complete")
    installer.install_auto_send_payment_freshness_patch()
    assert sync._refresh_plan_b_status_snapshot is installed


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_handoff", [None, True, False])
async def test_installed_wrapper_forwards_policy_and_preserves_enrichment(monkeypatch, native_doc, allow_handoff):
    db = AsyncMongoMockClient(tz_aware=True).db
    await db.unified_orders.insert_one({"user_id": OWNER, "order_number": NUMBER})
    handoff = AsyncMock(return_value={"advanced": True})
    monkeypatch.setattr(sync, "advance_carrier_handoff_from_salla_status", handoff)
    kwargs = {} if allow_handoff is None else {"allow_handoff_transition": allow_handoff}
    first = await sync._refresh_plan_b_status_snapshot(db, OWNER, NUMBER, native_doc, **kwargs)
    before = await db.integration_inbox.find_one({"user_id": OWNER, "connector_key": CONNECTOR})
    second = await sync._refresh_plan_b_status_snapshot(db, OWNER, NUMBER, native_doc, **kwargs)
    after = await db.integration_inbox.find_one({"user_id": OWNER, "connector_key": CONNECTOR})
    assert first["payment_facts_complete"] and second["customer_identity_facts_complete"]
    assert after["id"] == before["id"]
    assert await db.integration_inbox.count_documents({"user_id": OWNER}) == 1
    assert after["canonical_payload"]["paid_amount"] == 40.0
    assert after["canonical_payload"]["remaining_amount"] == 0.0
    assert after["canonical_payload"]["has_remaining_amount"] is False
    assert after["no_qoyod_send"] is True and after["eligibility_only"] is True
    assert after["auto_send_allowed"] is False and after["manual_send_allowed"] is False
    assert (await db.unified_orders.find_one({"user_id": OWNER}))["customer_name"] == "Synthetic customer"
    if allow_handoff is False:
        handoff.assert_not_awaited()
        assert second["carrier_handoff_transition"]["reason"] == "scheduled_refresh_only"
    else:
        assert handoff.await_count == 2


@pytest.mark.asyncio
async def test_disabled_installed_snapshot_does_not_borrow_other_owner_payload(monkeypatch, native_doc):
    db = AsyncMongoMockClient(tz_aware=True).db
    await db.integration_inbox.insert_one({"user_id": "main", "salla_order_number": NUMBER,
        "canonical_payload": {"order_number": NUMBER, "items": [{"private": "foreign"}]}})
    handoff = AsyncMock()
    monkeypatch.setattr(sync, "advance_carrier_handoff_from_salla_status", handoff)
    await sync._refresh_plan_b_status_snapshot(db, OWNER, NUMBER, native_doc, allow_handoff_transition=False)
    saved = await db.integration_inbox.find_one({"user_id": OWNER})
    assert "items" not in saved["canonical_payload"]
    handoff.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_typeerror_is_not_retried_without_the_fence(monkeypatch, native_doc):
    db = AsyncMongoMockClient(tz_aware=True).db
    read = AsyncMock(side_effect=TypeError("synthetic snapshot failure"))
    handoff = AsyncMock()
    broken_db = SimpleNamespace(integration_inbox=SimpleNamespace(find_one=read))
    monkeypatch.setattr(sync, "advance_carrier_handoff_from_salla_status", handoff)
    with pytest.raises(TypeError, match="synthetic snapshot failure"):
        await sync._refresh_plan_b_status_snapshot(broken_db, OWNER, NUMBER, native_doc,
                                                  allow_handoff_transition=False)
    read.assert_awaited_once()
    handoff.assert_not_awaited()
    assert await db.integration_inbox.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("name,query,update,options", [
    ("accounting_general_ledger_v2", {"user_id": OWNER}, {"$set": {"amount": 1}}, {}),
    ("unified_orders", {"user_id": "other", "order_number": NUMBER}, {"$set": {"paid_amount": 1}}, {}),
    ("unified_orders", {"user_id": OWNER, "order_number": NUMBER},
        {"$set": {"g47_salla_snapshot.component_pending": False, "qoyod_live_preflight_source": "salla_order_details"}}, {}),
    ("unified_orders", {"user_id": OWNER, "order_number": NUMBER},
        {"$set": {"paid_amount": 1, "qoyod_live_preflight_source": "salla_order_details"}}, {"upsert": True}),
    ("unified_orders", {"user_id": OWNER, "order_number": NUMBER},
        {"$set": {"paid_amount": 1, "qoyod_live_preflight_source": "salla_order_details"}, "$inc": {"amount": 1}}, {}),
    ("integration_inbox", {"user_id": OWNER, "connector_key": "foreign", "trace_id": "x"},
        {"$set": {"canonical_payload.paid_amount": 1}}, {}),
    ("integration_inbox", {"user_id": OWNER, "connector_key": CONNECTOR, "trace_id": "x"},
        {"$set": {"auto_send_allowed": True}}, {}),
    ("unified_orders", {"user_id": OWNER, "order_number": {"$ne": ""}},
        {"$set": {"paid_amount": 1, "qoyod_live_preflight_source": "salla_order_details"}}, {}),
    ("integration_inbox", {"user_id": OWNER, "connector_key": CONNECTOR, "trace_id": {"$ne": ""}},
        {"$set": {"canonical_payload.paid_amount": 1}}, {}),
    ("unified_orders", {"user_id": OWNER, "order_number": NUMBER},
        {"$set": {"paid_amount": 1, "qoyod_live_preflight_source": "salla_order_details"}}, {"upsert": 0}),
])
async def test_completion_capability_rejects_unrelated_writes(name, query, update, options):
    db = AsyncMongoMockClient(tz_aware=True).db
    assert not await validate_update(db, OWNER, name, deepcopy(query), deepcopy(update), options)


@pytest.mark.asyncio
@pytest.mark.parametrize("on_insert", [None, 7, ["id", "created_at"]])
async def test_initial_snapshot_rejects_malformed_insert_shape(monkeypatch, native_doc, on_insert):
    db = AsyncMongoMockClient(tz_aware=True).db
    monkeypatch.setattr(sync, "advance_carrier_handoff_from_salla_status", AsyncMock())
    await sync._refresh_plan_b_status_snapshot(db, OWNER, NUMBER, native_doc,
                                              allow_handoff_transition=False)
    saved = await db.integration_inbox.find_one({"user_id": OWNER})
    values = {key: value for key, value in saved.items() if key not in {"_id", "id", "created_at"}}
    query = {"user_id": OWNER, "connector_key": CONNECTOR, "idempotency_key": saved["idempotency_key"]}
    valid = {"$set": values, "$setOnInsert": {"id": "synthetic-id", "created_at": "synthetic-time"}}
    assert await validate_update(db, OWNER, "integration_inbox", query, valid, {"upsert": True})
    invalid = {"$set": values, "$setOnInsert": on_insert}
    assert await validate_update(db, OWNER, "integration_inbox", query, invalid, {"upsert": True}) is False


@pytest.mark.asyncio
async def test_enrichment_requires_an_existing_owner_bound_no_send_snapshot():
    db = AsyncMongoMockClient(tz_aware=True).db
    query = {"user_id": OWNER, "connector_key": CONNECTOR, "trace_id": "synthetic-trace"}
    update = {"$set": {"canonical_payload.paid_amount": 40}}
    assert not await validate_update(db, OWNER, "integration_inbox", query, update, {})
    row = {**query, "source": CONNECTOR, "pipeline_stage": "STATUS_SNAPSHOT",
        "no_qoyod_send": True, "eligibility_only": True,
        "manual_send_allowed": False, "auto_send_allowed": False}
    await db.integration_inbox.insert_one(row)
    assert await validate_update(db, OWNER, "integration_inbox", query, update, {})
    await db.integration_inbox.update_one(query, {"$set": {"auto_send_allowed": True}})
    assert not await validate_update(db, OWNER, "integration_inbox", query, update, {})
