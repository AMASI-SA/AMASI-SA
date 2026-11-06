"""Real Mongo/HTTP navigation headers and selected-date isolation."""
import httpx
import pytest
from fastapi import FastAPI

import preparation_supplier_dispatch as dispatch
from test_supplier_dispatch_waiting_current_status import db, seed_order, seed_piece, OWNER

pytestmark = pytest.mark.asyncio


async def request(db, path, **params):
    app = FastAPI()
    app.include_router(dispatch.make_preparation_supplier_dispatch_router(db, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        return await http.get("/supplier-dispatch-v1/" + path, params=params)


async def test_waiting_header_uses_same_current_order_authority_as_its_empty_details(db):
    await seed_order(db, "completed", current="completed")
    await seed_piece(db, "completed", product_id="only-stale-product")
    detail = await request(db, "waiting-page", group="product:only-stale-product")
    assert detail.status_code == 200, detail.text
    assert detail.json()["files"] == []
    headers = await request(db, "waiting-groups", cards_only="true")
    assert headers.status_code == 200, headers.text
    assert headers.json()["groups"] == [], "a product with no currently eligible pieces must not advertise Show"
    assert all(method in {"create_index", "create_indexes"} for _, method in db.writes), "headers never change historical pieces"


async def test_waiting_header_keeps_valid_siblings_and_rechecks_on_refresh(db, monkeypatch):
    await seed_order(db, "old", current="completed")
    await seed_order(db, "current", current="reviewed")
    for index in range(45):
        await seed_piece(db, "old", piece_id=f"old-{index}", product_id="mixed-product")
    await seed_piece(db, "current", product_id="mixed-product")
    annotated = dispatch.annotate_waiting_pieces
    orders = []
    async def observe(db, *, user_id, pieces):
        orders.extend(row["order_number"] for row in pieces)
        assert all(not ({"piece_id", "specifications_snapshot", "services", "image_b64"} & set(row)) for row in pieces)
        return await annotated(db, user_id=user_id, pieces=pieces)
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", observe)
    headers = await request(db, "waiting-groups", cards_only="true")
    assert [row["key"] for row in headers.json()["groups"]] == ["product:mixed-product"]
    assert sorted(orders) == ["current", "old"], "sibling history must not repeatedly hydrate its order"
    await seed_order(db, "current", current="completed")
    assert (await request(db, "waiting-groups", cards_only="true")).json()["groups"] == []
    assert all(method in {"create_index", "create_indexes"} for _, method in db.writes)


async def progress_seed(db, number, day, *, piece_status="in_progress", order_status="processing", worker=None):
    await seed_order(db, number, current=order_status)
    piece = await seed_piece(db, number, batch_id="batch-" + number,
        status=piece_status, supplier_dispatch_status="sent", supplier_id="supplier",
        sent_to_supplier_by_id=worker or OWNER["id"], responsible_employee_id=worker or OWNER["id"])
    await db.raw[dispatch.REGISTRY].insert_one({"user_id": OWNER["id"], "batch_id": piece["batch_id"],
        "file_number": "file-" + number, "registered_at": day + "T23:00:00+03:00"})
    await db.raw[dispatch.DISPATCHES].insert_one({"user_id": OWNER["id"], "id": "dispatch-" + number,
        "client_request_id": "request-" + number, "supplier_file_number": "supplier-file-" + number,
        "supplier_id": "supplier", "sent_by_id": worker or OWNER["id"], "status": "sent",
        "source_files": [{"cards": [{"piece_id": piece["piece_id"]}]}]})
    return piece


async def test_progress_initial_dates_only_live_outstanding_employee_days(db, monkeypatch):
    await progress_seed(db, "A", "2026-11-01")
    await progress_seed(db, "B", "2026-11-02", piece_status="received")
    await progress_seed(db, "C", "2026-11-03", order_status="completed")
    await progress_seed(db, "D", "2026-11-04", worker="other-worker")
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("navigation must not hydrate pieces or orders")
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", forbidden)
    response = await request(db, "progress-days")
    assert response.status_code == 200, response.text
    assert response.json()["days"] == [{"key": "2026-11-01"}]
    assert "files" not in response.json() and "summary" not in response.json()


async def test_progress_selected_day_loads_only_that_dates_files_and_summary(db):
    current = await progress_seed(db, "A", "2026-11-01")
    await progress_seed(db, "B", "2026-11-02")
    response = await request(db, "progress-workspace", day="2026-11-01")
    assert response.status_code == 200, response.text
    result = response.json()
    assert {row["batch_id"] for row in result["files"]} == {current["batch_id"]}
    assert result["summary"]["in_progress_pieces"] == 1
    assert [product["piece_id"] for row in result["files"] for product in row["products"]] == [current["piece_id"]]
    empty = await request(db, "progress-workspace", day="2026-10-01")
    assert empty.status_code == 200
    assert empty.json()["files"] == [] and empty.json()["supplier_accounts"] == []


async def test_waiting_cards_validate_current_order_without_customer_rows_or_suppliers(db, monkeypatch):
    await seed_order(db)
    await seed_piece(db)
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("individual rows/suppliers load only on Show")
    annotate = dispatch.annotate_waiting_pieces
    async def metadata_only(db, *, user_id, pieces):
        assert all(not ({"piece_id", "specifications_snapshot", "services", "image_b64"} & set(row)) for row in pieces)
        return await annotate(db, user_id=user_id, pieces=pieces)
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", metadata_only)
    monkeypatch.setattr(dispatch, "_active_suppliers", forbidden)
    response = await request(db, "waiting-groups", cards_only="true")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["suppliers"] == [] and "files" not in result
    assert len(result["groups"]) == 1
    assert result["groups"][0]["lazy"] is True
    assert "piece_count" not in result["groups"][0]
    assert "source_lines" not in result["groups"][0]


async def test_waiting_cards_page_ten_unique_headers_without_hydrating_customer_rows(db, monkeypatch):
    for index in range(23):
        await seed_order(db, str(index))
        await seed_piece(db, str(index), product_id=f"product-{index:02}",
            product_name=f"Product {index:02}", piece_id=f"piece-{index:02}")
    for index in range(8):
        await seed_order(db, f"closed-{index}", current="completed")
        await seed_piece(db, f"closed-{index}", product_id=f"product-{index:02}-closed")
    await seed_piece(db, "foreign", product_id="foreign-product", responsible_employee_id="other-worker")
    await seed_piece(db, "foreign-tenant", product_id="foreign-tenant-product", user_id="other-owner")
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("header scrolling must not load physical details or suppliers")
    annotate = dispatch.annotate_waiting_pieces
    async def metadata_only(db, *, user_id, pieces):
        assert len(pieces) <= 32
        assert all(not ({"piece_id", "specifications_snapshot", "services", "image_b64"} & set(row)) for row in pieces)
        return await annotate(db, user_id=user_id, pieces=pieces)
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", metadata_only)
    monkeypatch.setattr(dispatch, "_active_suppliers", forbidden)
    after = ""; keys = []; sizes = []
    for _ in range(4):
        response = await request(db, "waiting-groups", cards_only="true", after=after)
        assert response.status_code == 200, response.text
        data = response.json()
        sizes.append(len(data["groups"]))
        keys.extend(group["key"] for group in data["groups"])
        assert data["suppliers"] == []
        assert all("piece_count" not in group and "source_lines" not in group for group in data["groups"])
        after = data["next_cursor"]
        if not after:
            break
    assert sizes == [10, 10, 3]
    assert len(keys) == len(set(keys)) == 23
    assert "product:foreign-product" not in keys
    assert "product:foreign-tenant-product" not in keys
    for invalid in ("bad", "g:"):
        response = await request(db, "waiting-groups", cards_only="true", after=invalid)
        assert response.status_code == 400


async def test_selected_waiting_group_reads_each_current_order_once_and_stays_fresh(db, monkeypatch):
    await seed_order(db, "eligible", current="reviewed")
    await seed_piece(db, "eligible", piece_id="available-abaya", product_id="abaya")
    await seed_order(db, "old", current="completed")
    for index in range(45):
        await seed_piece(db, "old", piece_id=f"old-abaya-{index}", product_id="abaya")
    annotate = dispatch.annotate_waiting_pieces
    checked = []
    async def observe(db, *, user_id, pieces):
        checked.extend(row["order_number"] for row in pieces)
        return await annotate(db, user_id=user_id, pieces=pieces)
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", observe)
    response = await request(db, "waiting-page", group="product:abaya")
    assert response.status_code == 200, response.text
    assert [row["piece_id"] for file in response.json()["files"] for row in file["products"]] == ["available-abaya"]
    assert checked.count("old") == 1, "historical sibling pieces must share one current-order read within the page"
    assert checked.count("eligible") == 1
    await seed_order(db, "eligible", current="cancelled")
    again = await request(db, "waiting-page", group="product:abaya")
    assert again.status_code == 200 and again.json()["files"] == [], "new requests must revalidate current status"


async def test_progress_returns_ten_then_ten_with_whole_day_totals(db, monkeypatch):
    # Exercise the actual Production installer, including its route-history
    # validation, rather than only the unwrapped workspace implementation.
    import preparation_route_history as route_history
    monkeypatch.setattr(route_history, "_INSTALLED", False)
    monkeypatch.setattr(dispatch, "_employee_workspace", dispatch._employee_workspace)
    route_history.install_supplier_dispatch_route_guard()
    original = await progress_seed(db, "A", "2026-11-01")
    extra = [{**original, "piece_id": f"page-piece-{index:03}", "unit_index": index + 2,
              "status": "in_progress" if index < 22 else "received",
              "supplier_dispatch_status": "sent" if index < 22 else "received"} for index in range(29)]
    await db.raw[dispatch.PIECES].insert_many(extra)
    all_ids = [original["piece_id"], *(row["piece_id"] for row in extra)]
    await db.raw[dispatch.DISPATCHES].update_one({"id": "dispatch-A"}, {"$set": {
        "piece_count": 30, "source_files": [{"cards": [{"piece_id": pid} for pid in all_ids]}]}})
    # A never-dispatched sibling must not inflate either whole-date totals or pages.
    await db.raw[dispatch.PIECES].insert_one({**original, "piece_id": "never-dispatched", "supplier_dispatch_status": ""})
    annotate = dispatch.annotate_waiting_pieces
    batches = []
    async def observe(db, *, user_id, pieces):
        batches.append(len(pieces))
        assert len(pieces) <= 10, "full canonical detail hydration must be page-bounded"
        return await annotate(db, user_id=user_id, pieces=pieces)
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", observe)
    after = ""; loaded = []; counts = []
    for _ in range(4):
        response = await request(db, "progress-workspace", day="2026-11-01", after=after)
        assert response.status_code == 200, response.text
        page = response.json()
        ids = [row["piece_id"] for file in page["files"] for row in file["products"]]
        counts.append(len(ids)); loaded.extend(ids)
        assert page["progress_day_summary"]["assigned"] == 30
        assert page["progress_day_summary"]["ready"] == 7
        assert page["progress_day_summary"]["remaining"] == 23
        assert "never-dispatched" not in page["progress_active_piece_ids"]
        for account in page["supplier_accounts"]:
            assert account["dispatches"][0]["progress"]["remaining"] == 23
        after = page["next_cursor"]
        if not after:
            break
    assert counts == [10, 10, 3] and len(set(loaded)) == len(loaded) == 23
    assert batches == [10, 10, 3]
    scoped_reads = [query for collection, query in db.reads if collection == dispatch.PIECES
                    and "piece_id" in query and isinstance(query["piece_id"], dict)]
    assert scoped_reads
    assert all(len(query["piece_id"]["$in"]) <= 10 for query in scoped_reads)
