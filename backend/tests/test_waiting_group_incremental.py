"""Selected product first-result and eligible count through real HTTP/Mongo."""
import pytest

import preparation_supplier_dispatch as dispatch
from test_preparation_lazy_sections import request
from test_supplier_dispatch_waiting_current_status import db, seed_order, seed_piece, OWNER

pytestmark = pytest.mark.asyncio


async def test_first_piece_arrives_before_remaining_nine_with_same_physical_cursor(db, monkeypatch):
    await seed_order(db, "A")
    for index in range(35):
        await seed_piece(db, "A", f"incremental-{index:03}")
    original = dispatch.annotate_waiting_pieces
    sizes = []
    async def observe(db, **kwargs):
        sizes.append(len(kwargs["pieces"]))
        return await original(db, **kwargs)
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", observe)
    first = await request(db, "waiting-page", group="product:p", page_size=1)
    assert first.status_code == 200, first.text
    first = first.json()
    ids = [p["piece_id"] for f in first["files"] for p in f["products"]]
    assert len(ids) == 1 and first["next_cursor"]
    assert max(sizes) == 1 and sum(sizes) <= 2, "first response must not hydrate ten customer rows"
    sizes.clear()
    rest = await request(db, "waiting-page", group="product:p", after=first["next_cursor"], page_size=9)
    assert rest.status_code == 200, rest.text
    rest = rest.json()
    next_ids = [p["piece_id"] for f in rest["files"] for p in f["products"]]
    assert len(next_ids) == 9 and not set(ids).intersection(next_ids)
    assert max(sizes) <= 9
    assert rest["next_cursor"]


async def test_count_is_selected_group_current_eligible_physical_pieces_without_images_or_suppliers(db, monkeypatch):
    await seed_order(db, "A")
    for index in range(35):
        await seed_piece(db, "A", f"count-{index:03}")
    await seed_piece(db, "A", "other-product", product_id="other")
    await seed_piece(db, "A", "other-worker", responsible_employee_id="foreign")
    await seed_piece(db, "A", "other-tenant", user_id="foreign")
    await seed_order(db, "C", current="completed")
    await seed_piece(db, "C", "terminal")
    await seed_piece(db, "missing", "missing-order")
    await seed_piece(db, "A", "sent", supplier_dispatch_status="sent")
    await seed_piece(db, "A", "reserved", supplier_receiving_session_id="other-draft")
    original = dispatch.annotate_waiting_pieces
    observed = []
    async def observe(db, **kwargs):
        observed.extend(kwargs["pieces"])
        assert all(not any(key in row for key in ("product_options_snapshot", "image_url", "services"))
                   for row in kwargs["pieces"]), "count must use eligibility metadata only"
        return await original(db, **kwargs)
    async def forbidden(*_a, **_k):
        raise AssertionError("count must not hydrate images, files or supplier catalogs")
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", observe)
    monkeypatch.setattr(dispatch, "_active_suppliers", forbidden)
    monkeypatch.setattr(dispatch, "_hydrate_piece_images_from_batches", forbidden)
    response = await request(db, "waiting-count", group="product:p")
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "employee_id": OWNER["id"], "group": "product:p",
                               "eligible_piece_count": 35, "read_only": True}
    assert len(observed) == 3, "current order eligibility is shared only within this count request"
    await seed_order(db, "A", current="completed")
    assert (await request(db, "waiting-count", group="product:p")).json()["eligible_piece_count"] == 0


async def test_candidate_card_can_exist_but_canonical_empty_read_and_count_never_grant_selection(db):
    await seed_order(db, "A", current="cancelled")
    await seed_piece(db, "A")
    headers = (await request(db, "waiting-groups", cards_only="true")).json()
    assert len(headers["groups"]) == 1
    assert (await request(db, "waiting-page", group=headers["groups"][0]["key"], page_size=1)).json()["files"] == []
    count = await request(db, "waiting-count", group=headers["groups"][0]["key"])
    assert count.status_code == 200 and count.json()["eligible_piece_count"] == 0
    for limit in (0, 11):
        assert (await request(db, "waiting-page", page_size=limit)).status_code == 422
    assert (await request(db, "waiting-count")).status_code == 422
