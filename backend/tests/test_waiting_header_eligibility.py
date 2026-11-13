"""Supplier headers must describe current eligible work, not old piece snapshots."""
from copy import deepcopy

import pytest

import preparation_supplier_dispatch as dispatch
from test_preparation_lazy_sections import request
from test_supplier_dispatch_waiting_current_status import (
    db, seed_order, seed_piece, OWNER, ELIGIBLE, BLOCKED, ObservedCollection,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("status", ELIGIBLE + BLOCKED + ["  in  progress  ", "REVIEWED"])
async def test_headers_match_current_order_status_not_stored_piece_status(db, status):
    await seed_order(db, current=status)
    piece = await seed_piece(db)
    result = await request(db, "waiting-groups", cards_only="true")
    assert result.status_code == 200, result.text
    expected = " ".join(status.replace("_", " ").casefold().split()) in {
        " ".join(value.replace("_", " ").casefold().split()) for value in ELIGIBLE}
    assert bool(result.json()["groups"]) is expected
    assert (await db.raw[dispatch.PIECES].find_one({"piece_id": piece["piece_id"]}, {"_id": 0})) == piece


async def test_terminal_and_missing_orders_cannot_hide_newer_eligible_groups(db):
    for index in range(40):
        await seed_order(db, str(index), current="completed")
        await seed_piece(db, str(index), product_id=f"000-old-{index:02}")
    await seed_piece(db, "missing", product_id="000-missing")
    await seed_order(db, "foreign", tenant="other-owner")
    await seed_piece(db, "foreign", product_id="000-foreign")
    await seed_order(db, "new")
    await seed_piece(db, "new", product_id="zzz-new")
    data = (await request(db, "waiting-groups", cards_only="true")).json()
    assert [row["key"] for row in data["groups"]] == ["product:zzz-new"]
    assert data["next_cursor"] is None
    await seed_order(db, "new", current="shipping")
    assert (await request(db, "waiting-groups", cards_only="true")).json()["groups"] == []
    await seed_order(db, "0", current="processing")
    assert (await request(db, "waiting-groups", cards_only="true")).json()["groups"][0]["key"] == "product:000-old-00"


async def test_recent_eligible_member_controls_header_and_newest_first_paging(db):
    expected = []
    for index in range(23):
        number = str(index)
        await seed_order(db, number)
        date = f"2026-10-{index + 1:02}T08:00:00+00:00"
        await db.raw.unified_orders.update_one({"user_id": OWNER["id"], "order_number": number},
            {"$set": {"order_date": date, "raw_by_source.salla_direct.date.date": date}})
        await seed_piece(db, number, product_id=f"p-{index:02}", product_name=f"Live {index}")
        expected.insert(0, f"product:p-{index:02}")
    await seed_order(db, "terminal", current="completed")
    await seed_piece(db, "terminal", product_id="p-22", product_name="Old terminal name")
    after = ""; seen = []; sizes = []
    while True:
        reply = await request(db, "waiting-groups", cards_only="true", after=after)
        assert reply.status_code == 200, reply.text
        data = reply.json(); sizes.append(len(data["groups"]))
        seen.extend(row["key"] for row in data["groups"])
        assert all(row["product_name"] != "Old terminal name" for row in data["groups"])
        after = data["next_cursor"]
        if not after:
            break
    assert sizes == [10, 10, 3] and seen == expected
    legacy = (await request(db, "waiting-groups", cards_only="true", after="g:product:p-20")).json()
    assert {row["key"] for row in legacy["groups"]} == {"product:p-21", "product:p-22"}
    for bad in ("g2:", "g2:not-json", "g2:W10"):
        assert (await request(db, "waiting-groups", cards_only="true", after=bad)).status_code == 400


@pytest.mark.parametrize("proof,payment", [(False, "paid"), (True, "unpaid"), (True, "paid")])
async def test_local_pending_review_keeps_exact_durable_proof_and_payment_policy(db, proof, payment):
    await seed_order(db, current="under_review")
    await seed_piece(db)
    await db.raw.unified_orders.update_one({"order_number": "A"}, {"$set": {
        "raw_by_source.salla_direct.payment": {"status": payment},
        "raw_by_source.salla_direct.payment_collection_status": payment,
    }})
    await db.raw.order_review_workflows.insert_one({"user_id": OWNER["id"], "order_number": "A",
        "completion_mode": "mezan_local_v1", "stage": "reviewed", "review_completion_operation_id": "approval"})
    if proof:
        await db.raw.order_review_completion_operations.insert_one({"_id": "approval", "user_id": OWNER["id"],
            "order_number": "A", "completion_mode": "mezan_local_v1", "state": "completed"})
    canonical = await dispatch.annotate_waiting_pieces(db, user_id=OWNER["id"], pieces=[{"order_number": "A"}])
    expected = proof and payment == "paid"
    assert canonical[0]["waiting_review_eligible"] is expected
    assert bool((await request(db, "waiting-groups", cards_only="true")).json()["groups"]) is expected


@pytest.mark.parametrize("current,custom,expected", [
    ("under_review", "تم المراجعة", True),
    ("completed", "تم المراجعة", False),
    ("shipping", "تم المراجعة", False),
    ("processing", "تم المراجعة", True),
    ("under_review", "بانتظار المراجعة", False),
])
async def test_header_uses_native_current_status_precedence(db, current, custom, expected):
    await seed_order(db, current=current, raw_status="under_review", raw_name="بانتظار المراجعة")
    await seed_piece(db)
    await db.raw.unified_orders.update_one({"order_number": "A"}, {"$set": {
        "raw_by_source.salla_direct.status.customized": {"name": custom}}})
    canonical = await dispatch.annotate_waiting_pieces(db, user_id=OWNER["id"], pieces=[{"order_number": "A"}])
    assert canonical[0]["waiting_review_eligible"] is expected
    assert bool((await request(db, "waiting-groups", cards_only="true")).json()["groups"]) is expected


async def test_status_slug_fallback_and_physical_availability_are_preserved(db):
    await seed_order(db, current="")
    await db.raw.unified_orders.update_one({"order_number": "A"}, {
        "$unset": {"raw_by_source.salla_direct.status": ""},
        "$set": {"raw_by_source.salla_direct.status_slug": "processing"}})
    await seed_piece(db)
    assert (await request(db, "waiting-groups", cards_only="true")).json()["groups"]
    for fields in ({"status": " blocked "}, {"assignment_status": " unassigned "},
                   {"supplier_dispatch_status": "sent"}, {"supplier_receiving_session_id": "draft"}):
        await db.raw[dispatch.PIECES].update_one({"order_number": "A"}, {"$set": fields})
        assert (await request(db, "waiting-groups", cards_only="true")).json()["groups"] == []
        await db.raw[dispatch.PIECES].update_one({"order_number": "A"}, {"$unset": {key: "" for key in fields}})


async def test_header_reads_do_not_load_options_images_or_full_workflow_and_remain_tenant_scoped(db, monkeypatch):
    await seed_order(db)
    await seed_piece(db)
    original = ObservedCollection.find
    projections = []
    def observe(collection, query, projection=None, *args, **kwargs):
        if collection.collection.name in {"unified_orders", "order_review_workflows"}:
            assert query["user_id"] == OWNER["id"]
            assert projection and all(value == 1 for key, value in projection.items() if key != "_id")
            assert not any(key in projection for key in ("raw_by_source.salla_direct", "items", "raw_by_source.salla_direct.items"))
            projections.append(deepcopy(projection))
        return original(collection, query, projection, *args, **kwargs)
    monkeypatch.setattr(ObservedCollection, "find", observe)
    result = await request(db, "waiting-groups", cards_only="true")
    assert result.status_code == 200, result.text
    assert len(result.json()["groups"]) == 1 and len(projections) == 3
    assert result.json()["suppliers"] == [] and "files" not in result.json()


async def test_first_page_checks_only_small_candidate_batch_even_with_many_live_groups(db):
    for index in range(80):
        await seed_order(db, str(index))
        await seed_piece(db, str(index), piece_id=f"p-{index:03}", product_id=f"p-{index:03}")
    db.reads.clear()
    first = await request(db, "waiting-groups", cards_only="true")
    assert first.status_code == 200, first.text
    assert len(first.json()["groups"]) == 10 and first.json()["next_cursor"]
    queries = [query for name, query in db.reads if name == "unified_orders"]
    assert len(queries) == 2 and len(queries[1]["order_number"]["$in"]) <= 16


async def test_invalid_recent_local_member_does_not_promote_an_old_product_or_skip_later_pages(db):
    # Product z has the newest candidate, but its newest *eligible* order is
    # oldest. It belongs after all other groups, including across the cursor.
    for index in range(13):
        number = str(index)
        await seed_order(db, number)
        date = f"2026-10-{index + 1:02}T08:00:00+00:00"
        await db.raw.unified_orders.update_one({"order_number": number}, {"$set": {
            "order_date": date, "raw_by_source.salla_direct.date.date": date}})
        await seed_piece(db, number, product_id="z" if index == 0 else f"p-{index:02}")
    await seed_order(db, "invalid-recent", current="under_review")
    await db.raw.unified_orders.update_one({"order_number": "invalid-recent"}, {"$set": {"order_date": "2026-11-07T00:00:00Z"}})
    await seed_piece(db, "invalid-recent", product_id="z", product_name="Invalid member")
    first = (await request(db, "waiting-groups", cards_only="true")).json()
    second = (await request(db, "waiting-groups", cards_only="true", after=first["next_cursor"])).json()
    keys = [row["key"] for data in (first, second) for row in data["groups"]]
    assert keys == [f"product:p-{index:02}" for index in range(12, 0, -1)] + ["product:z"]
    assert second["next_cursor"] is None
