"""Selected anchors bound reads; canonical unit, options and revision stay authoritative."""
import httpx
import pytest
from fastapi import FastAPI

import reviewed_products_catalog as catalog
import reviewed_preparation_batches as batches
from preparation_file_registry import REGISTRY
from preparation_piece_operations import PIECES
from test_supplier_dispatch_waiting_current_status import db, OWNER
from test_reviewed_product_pages import seed
from order_review_forward_stage_guard import install_order_review_forward_stage_guard
import preparation_piece_operations as piece_operations
import preparation_file_registry as registry_module
import preparation_file_failure_safety as safety
from mobile_product_file_pdf import make_mobile_product_file_pdf_router

install_order_review_forward_stage_guard()
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def installed_assignment_stack(monkeypatch):
    # Exercise the real Production wrappers in these HTTP tests without
    # replacing neighboring unit modules' imported reconciler during collection.
    for module in (piece_operations, registry_module, safety, batches):
        for name, value in list(vars(module).items()):
            if callable(value) or name.startswith("_ORIGINAL_") or name == "_INSTALLED":
                monkeypatch.setattr(module, name, value)
    piece_operations.install_preparation_piece_operations()
    safety.install_preparation_finalize_safety()


@pytest.mark.parametrize("count", [1, 50])
async def test_create_file_hydrates_only_selected_orders_and_preserves_printable_assignment(db, monkeypatch, count):
    await seed(db, max(23, count + 3))
    numbers = [f"A{index:03}" for index in range(count)]
    # The exact physical unit/revision is obtained through the original loader.
    context = await catalog.load_reviewed_product_context(db.raw, user_id=OWNER["id"], order_numbers=numbers)
    chosen = context["catalog"]["products"]
    assert len(chosen) == count
    scope = []
    original = catalog.get_orders
    async def observe(repository, **kwargs):
        scope.append(kwargs.get("order_numbers"))
        assert kwargs["order_numbers"] == numbers, "unselected orders must not be hydrated for assignment"
        return await original(repository, **kwargs)
    monkeypatch.setattr(catalog, "get_orders", observe)
    async def no_provider(*args, **kwargs):
        return False, 1
    monkeypatch.setattr(batches, "_reconcile_order_stage", no_provider)
    source_reads = []
    async def provider_read(_db, owner, number, **kwargs):
        assert owner == OWNER["id"] and number in numbers and kwargs["allow_auto_fulfillment"] is False
        source_reads.append(number)
        return {"ok": True, "found": True}
    monkeypatch.setattr(batches, "refresh_order_from_salla", provider_read)
    await db.raw[REGISTRY].insert_one({"user_id": OWNER["id"], "client_request_id": "selected-order-scope",
        "status": "draft", "file_number": "PF-SYNTHETIC-SCOPE", "file_title": "Synthetic scoped file",
        "file_date": "2026-11-06", "expected_quantity": count, "selected_product_count": count,
        "responsible_employee_id": OWNER["id"], "responsible_employee_name": "Synthetic employee"})
    app = FastAPI()
    app.include_router(batches.make_reviewed_preparation_batches_router(db.raw, lambda: OWNER))
    app.include_router(safety.make_preparation_file_failure_safety_router(db.raw, lambda: OWNER))
    app.include_router(make_mobile_product_file_pdf_router(db.raw, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        body = {"client_request_id": "selected-order-scope", "selections": [{"group_key": row["group_key"],
            "quantity": 1, "revision": row["revision"], "order_number": row["source_lines"][0]["order_number"]} for row in chosen]}
        response = await http.post("/reviewed-preparation-batches-v1/batches", json=body)
        assert response.status_code == 200, response.text
        recovered = await http.get("/preparation-file-safety-v1/requests/selected-order-scope/status")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["state"] == "COMMITTED"
        assert recovered.json()["batch"]["selections"] == body["selections"]
        pdf = await http.get(f"/mobile-product-file-v1/{response.json()['batch_id']}/pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.text[:200]
        import fitz
        document = fitz.open(stream=pdf.content, filetype="pdf")
        assert sum(len(page.get_images(full=True)) for page in document) == count, "each assigned piece retains its own printable QR"
        document.close()
        body["selections"][0]["revision"] = "b" * 64
        changed = await http.post("/reviewed-preparation-batches-v1/batches", json=body)
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "preparation_request_selection_conflict"
    assert response.status_code == 200, response.text
    assert scope == [numbers]
    assert sorted(source_reads) == numbers, "genuine-no-options checks retain canonical source reads"
    assert response.json()["allocated_quantity"] == count and response.json()["file_registered"] is True
    assert await db.raw[batches.PREPARATION_UNIT_ALLOCATIONS].count_documents({"status": "committed"}) == count
    assert await db.raw[PIECES].count_documents({"responsible_employee_id": OWNER["id"]}) == count


async def test_wrong_order_anchor_cannot_expand_scope_or_consume_another_order(db, monkeypatch):
    await seed(db, 2)
    context = await catalog.load_reviewed_product_context(db.raw, user_id=OWNER["id"], order_numbers=["A000"])
    chosen = context["catalog"]["products"][0]
    original = catalog.get_orders
    scope = []
    async def observe(repository, **kwargs):
        scope.append(kwargs.get("order_numbers"))
        assert kwargs["order_numbers"] == ["A001"]
        return await original(repository, **kwargs)
    monkeypatch.setattr(catalog, "get_orders", observe)
    app = FastAPI()
    app.include_router(batches.make_reviewed_preparation_batches_router(db.raw, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        body = {"client_request_id": "wrong-anchor-request", "selections": [{"group_key": chosen["group_key"],
            "quantity": 1, "revision": chosen["revision"], "order_number": "A001"}]}
        response = await http.post("/reviewed-preparation-batches-v1/batches", json=body)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "reviewed_product_not_available"
        body["selections"].append({"group_key": "missing-anchor", "quantity": 1, "revision": "a" * 64})
        partial = await http.post("/reviewed-preparation-batches-v1/batches", json=body)
        assert partial.status_code == 422
    assert scope == [["A001"]]
    assert await db.raw[batches.PREPARATION_UNIT_ALLOCATIONS].count_documents({}) == 0
    assert await db.raw[PIECES].count_documents({}) == 0


async def test_group_page_remains_bounded_with_the_actual_installed_sorting_and_pdf_wrappers(db, monkeypatch):
    await seed(db, 23)
    scopes = []
    original = catalog.get_orders
    async def observe(repository, **kwargs):
        assert 0 < len(kwargs["order_numbers"]) <= 10
        scopes.append(kwargs["order_numbers"])
        return await original(repository, **kwargs)
    monkeypatch.setattr(catalog, "get_orders", observe)
    app = FastAPI()
    app.include_router(catalog.make_reviewed_products_catalog_router(db.raw, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        response = await http.get("/reviewed-products-v1/group-page", params={"group_key": "product:p"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["products"]) == 10 and result["next_cursor"]
    assert all(row["piece_level"] is True and row["revision"] for row in result["products"])
    assert len(scopes) == 1
