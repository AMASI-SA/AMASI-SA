"""Actual supplier-cost adoption invalidates report caches only on commit."""
from fastapi import HTTPException
import pytest

from product_cost_revision import get_product_cost_revision
from supplier_invoice_reporting_costs import latest_report_invoice
from test_supplier_native_invoice_v2 import (
    OWNER, env, http, identities, mapping, receiving_session,
)


@pytest.mark.asyncio
async def test_same_price_receipt_changes_read_side_cache_key_only_once(env, http):
    db, _ = env
    await identities(db)
    await mapping(db)
    session, payload = await receiving_session(db)
    before = await get_product_cost_revision(db, OWNER)
    before_invoice = await latest_report_invoice(db, OWNER)
    path = f'/supplier-receiving-v1/sessions/{session["id"]}/close'

    response = await http.post(path, json=payload)
    assert response.status_code == 200, response.text
    invoice = response.json()["supplier_invoice"]
    assert not invoice.get("price_changes")
    assert not invoice.get("added_product_services")
    assert await get_product_cost_revision(db, OWNER) == before
    actual = await latest_report_invoice(db, OWNER)
    assert actual != before_invoice and actual[1] == invoice["id"]

    replay = await http.post(path, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["supplier_invoice"]["id"] == invoice["id"]
    assert await get_product_cost_revision(db, OWNER) == before
    assert await latest_report_invoice(db, OWNER) == actual


@pytest.mark.asyncio
async def test_integrity_failure_rolls_back_invoice_and_report_revision(env, http, monkeypatch):
    import supplier_receiving_routes as receiving

    db, _ = env
    await identities(db)
    await mapping(db)
    session, payload = await receiving_session(db)
    before_revision = await get_product_cost_revision(db, OWNER)
    before_piece = await db[receiving.PIECES].find_one({"user_id": OWNER, "piece_id": "piece"})

    async def fail_integrity(*args, **kwargs):
        raise HTTPException(409, detail={"code": "synthetic_invoice_integrity_failure"})

    monkeypatch.setattr(receiving, "verify_persisted_supplier_invoice", fail_integrity)
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "synthetic_invoice_integrity_failure"
    assert await get_product_cost_revision(db, OWNER) == before_revision
    assert await db[receiving.SUPPLIER_INVOICES].count_documents({"user_id": OWNER}) == 0
    assert await db[receiving.PIECES].find_one({"user_id": OWNER, "piece_id": "piece"}) == before_piece
    assert (await db[receiving.SESSIONS].find_one({"id": session["id"]}))["status"] == "open"


@pytest.mark.asyncio
async def test_experiment_receipt_does_not_invalidate_real_cost_reports(env, http):
    import supplier_receiving_routes as receiving

    db, _ = env
    await identities(db)
    await mapping(db)
    session, payload = await receiving_session(db)
    experiment = {"experiment_mode": True, "experiment_run_id": "synthetic-cost-report-run"}
    for collection in (receiving.PIECES, receiving.RECEIVING_EVENTS, receiving.PIECE_EVENTS):
        await db[collection].update_many({"user_id": OWNER}, {"$set": experiment})
    await db[receiving.SESSIONS].update_one({"id": session["id"]}, {"$set": experiment})
    before = await get_product_cost_revision(db, OWNER)

    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["supplier_invoice"]["status"] == "experiment_completed"
    assert response.json()["financial_integrity_verified"] is False
    assert await get_product_cost_revision(db, OWNER) == before
    assert await db.accounting_general_ledger_v2.count_documents({"user_id": OWNER, "entry_type": "supplier_invoice"}) == 0


@pytest.mark.asyncio
async def test_real_native_invoice_replaces_later_option_estimate_in_report(env, http):
    from order_cost_authority import calculate_catalog_line_cost
    from supplier_invoice_reporting_costs import (
        load_supplier_invoice_reporting_costs, resolve_supplier_invoice_reporting_cost,
    )

    db, _ = env
    await identities(db)
    await mapping(db)
    session, payload = await receiving_session(db)
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
    assert response.status_code == 200, response.text
    item = {"order_item_id": "1", "product_id": "product", "quantity": 1, "options_raw": []}
    orders = [{"order_number": "123456789", "products": [item]}]
    catalog = calculate_catalog_line_cost(
        item, product={"salla_product_id": "product"}, profile={"base_cost": 999},
        product_bindings=[], resources={}, option_bindings=[{
            "id": "later-option-cost", "option_id": "later-option", "value_id": "yes",
            "mode": "direct", "direct_amount": 15, "quantity": 1,
        }], current_report=True,
    )
    assert catalog["calculation_cost_available"] is False
    authority = await load_supplier_invoice_reporting_costs(db, OWNER, orders)
    resolved = resolve_supplier_invoice_reporting_cost(
        item, order_number="123456789", catalog_cost=catalog,
        historical_cost=catalog, supplier_authority=authority,
    )
    assert resolved["calculation_cost_available"] is True, resolved
    assert resolved["line_total"] == 100
    assert resolved["cost_stage"] == "actual"
