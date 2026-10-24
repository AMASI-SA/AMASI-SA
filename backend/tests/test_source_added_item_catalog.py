"""New source lines enter the ordinary unassigned catalog only after item review."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from mongomock_motor import AsyncMongoMockClient

import reviewed_preparation_batches as batches
from order_review_routes import EVENTS, WORKFLOWS
from reviewed_products_catalog import (
    PREPARATION_UNIT_ALLOCATIONS, aggregate_reviewed_products,
    apply_preparation_allocations, expand_reviewed_ready_units,
    has_unreviewed_source_additions,
)
from review_local_policy import (
    LOCAL_COMPLETION_MODE, assignment_workflow_query,
    load_source_addition_assignment_workflows,
    load_source_current_assignment_workflows,
)


def item(identity, *, quantity=1):
    return {"order_item_id": "salla:100:" + identity, "product_id": "product", "quantity": quantity,
            "name": "هدية", "sku": "SYN-1", "options_normalized": {"الاسم": identity}}


def workflow(*, approved=False, local=False):
    original = {**item("old"), "review_status": "reviewed"}
    result = {"user_id": "owner", "order_number": "100", "stage": "in_progress", "revision": 5,
              "items": [original], "reviewed_at": "2026-10-20T12:00:00Z"}
    if local:
        result.update(completion_mode=LOCAL_COMPLETION_MODE, review_completion_operation_id="initial")
    if approved:
        result.update(source_additions_reviewed=True)
        result["items"].append({**item("new", quantity=2), "review_status": "reviewed", "source_addition": True})
    return result


def current_order():
    return {"order_number": "100", "items": [item("old"), item("new", quantity=2), item("later")]}


def available(order, state, allocations):
    return expand_reviewed_ready_units(apply_preparation_allocations(
        aggregate_reviewed_products([(order, state)], []), allocations))


@pytest.mark.parametrize("local", [False, True])
def test_new_line_hidden_until_clicked_without_repeating_original_allocated_piece(local):
    state = workflow(local=local)
    order = current_order()
    allocations = [{"order_number": "100", "order_item_id": "salla:100:old", "unit_index": 1,
                    "status": "committed"}]
    originals = deepcopy((order, state, allocations))
    assert available(order, state, allocations)["products"] == []
    assert (order, state, allocations) == originals

    reviewed = workflow(approved=True, local=local)
    cards = available(order, reviewed, allocations)["products"]
    assert len(cards) == 2
    assert {r["order_item_id"] for p in cards for r in p["source_lines"]} == {"salla:100:new"}
    assert reviewed["stage"] == "in_progress"
    assert reviewed["items"][0] == state["items"][0]

    # A normal partial assignment removes only the assigned new unit.
    allocations.append({"order_number": "100", "order_item_id": "salla:100:new", "unit_index": 1,
                        "status": "committed"})
    cards = available(order, reviewed, allocations)["products"]
    assert len(cards) == 1
    assert cards[0]["source_lines"][0]["available_unit_indices"] == [2]


def test_pending_addition_metadata_is_not_review_consent():
    state = workflow(approved=True)
    state["items"][1]["review_status"] = "pending_review"
    assert aggregate_reviewed_products([(current_order(), state)], [])["summary"]["total_quantity"] == 1


def test_anonymous_legacy_snapshots_keep_existing_recovery_behavior():
    state = {"stage": "in_progress", "items": [{"product_id": "product", "sku": "SYN-1", "quantity": 1}]}
    order = {"order_number": "100", "items": [item("old")]}
    assert not has_unreviewed_source_additions(order, state)
    assert aggregate_reviewed_products([(order, state)], [])["summary"]["total_quantity"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("approved,batch_id", [(False, "partial"), (True, "partial"), (True, "")])
async def test_partial_assignment_or_recovery_preserves_started_order(monkeypatch, approved, batch_id):
    db = AsyncMongoMockClient().test
    state = workflow(approved=approved)
    original_line = deepcopy(state["items"][0])
    await db[WORKFLOWS].insert_one(state)
    await db[PREPARATION_UNIT_ALLOCATIONS].insert_one({"user_id": "owner", "order_number": "100",
        "order_item_id": "salla:100:old", "unit_index": 1, "status": "committed"})
    order = SimpleNamespace(order_number="100", items=current_order()["items"])

    async def context(*args, **kwargs):
        return {"pairs": [(order, state)]}
    monkeypatch.setattr(batches, "load_reviewed_product_context", context)
    complete, remaining = await batches._reconcile_order_stage(
        db, user_id="owner", order_number="100", batch_id=batch_id, actor={"id": "operator"})
    assert not complete
    assert remaining == 3  # Pending additions remain counted, never falsely complete.
    saved = await db[WORKFLOWS].find_one({"order_number": "100"})
    assert saved["stage"] == "in_progress"
    assert saved["items"][0] == original_line
    assert await db[EVENTS].count_documents({}) == 0
    assert await db[PREPARATION_UNIT_ALLOCATIONS].count_documents({}) == 1


@pytest.mark.asyncio
async def test_legacy_in_progress_query_requires_real_addition_marker():
    db = AsyncMongoMockClient().test
    rows = [workflow(), workflow(approved=True), workflow(local=True),
            {**workflow(approved=True), "completion_mode": "unknown"}]
    for index, row in enumerate(rows):
        row["order_number"] = str(index)
    await db[WORKFLOWS].insert_many(rows)
    selected = await db[WORKFLOWS].find({"user_id": "owner", **assignment_workflow_query()}).to_list(None)
    assert {r["order_number"] for r in selected} == {"1", "2"}


@pytest.mark.asyncio
async def test_legacy_added_queue_checks_current_source_without_fabricating_local_approval():
    from fulfillment_v2_routes import COMPONENT_LIFECYCLES, LEGACY_COMPONENT_COHORT
    from stock_component_consumption_service import PLANS
    db = AsyncMongoMockClient().test
    state = workflow(approved=True)
    await db.unified_orders.insert_one({"user_id": "owner", "order_number": "100",
        "g47_salla_snapshot": {"revision": 4, "component_pending": False}})
    await db[COMPONENT_LIFECYCLES].insert_one({"user_id": "owner", "order_number": "100",
        "state": LEGACY_COMPONENT_COHORT, "accepted": True, "snapshot_revision": 4})
    assert "100" in await load_source_addition_assignment_workflows(db, user_id="owner", workflows=[state])
    await db[COMPONENT_LIFECYCLES].update_one({}, {"$set": {"snapshot_revision": 3}})
    assert not await load_source_addition_assignment_workflows(db, user_id="owner", workflows=[state])
    await db[COMPONENT_LIFECYCLES].update_one({}, {"$set": {"snapshot_revision": 4, "accepted": False}})
    assert not await load_source_addition_assignment_workflows(db, user_id="owner", workflows=[state])
    await db[COMPONENT_LIFECYCLES].update_one({}, {"$set": {"accepted": True}})
    assert "completion_mode" not in state
    assert not await load_source_addition_assignment_workflows(db, user_id="other", workflows=[state])
    without_item_consent = deepcopy(state)
    without_item_consent["items"][1]["review_status"] = "pending_review"
    assert not await load_source_addition_assignment_workflows(db, user_id="owner", workflows=[without_item_consent])
    await db.unified_orders.update_one({}, {"$set": {"g47_salla_snapshot.component_pending": True}})
    assert not await load_source_addition_assignment_workflows(db, user_id="owner", workflows=[state])
    await db.unified_orders.update_one({}, {"$set": {"g47_salla_snapshot.component_pending": False}})
    await db[PLANS].insert_one({"user_id": "owner", "order_id": "100", "source_version": {"value": 2}})
    assert not await load_source_current_assignment_workflows(db, user_id="owner", workflows=[state])
    await db[COMPONENT_LIFECYCLES].update_one({}, {"$set": {"state": "reserved", "generation": 2}})
    assert "100" in await load_source_current_assignment_workflows(db, user_id="owner", workflows=[state])
    await db[COMPONENT_LIFECYCLES].update_one({}, {"$set": {"snapshot_revision": 3}})
    assert not await load_source_current_assignment_workflows(db, user_id="owner", workflows=[state])
