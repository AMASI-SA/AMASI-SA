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


async def test_waiting_cards_defer_customer_piece_eligibility_and_suppliers(db, monkeypatch):
    await seed_order(db)
    await seed_piece(db)
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("individual rows/suppliers load only on Show")
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", forbidden)
    monkeypatch.setattr(dispatch, "_active_suppliers", forbidden)
    response = await request(db, "waiting-groups", cards_only="true")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["suppliers"] == [] and "files" not in result
    assert len(result["groups"]) == 1
    assert result["groups"][0]["lazy"] is True
    assert "piece_count" not in result["groups"][0]
    assert "source_lines" not in result["groups"][0]
