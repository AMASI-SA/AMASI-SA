"""Real isolated-Mongo regression for customer-selected supplier embroidery."""
from datetime import datetime, timezone
import pytest
import supplier_receiving_routes as r
from test_supplier_product_selection import env, seed, installed_live_cost_support

@pytest.mark.asyncio
@pytest.mark.parametrize("answer,completed,expected", [
    ("نعم", False, 1), ("لا", False, 0), (None, False, 0), ("نعم", True, 0),
])
async def test_embroidery_first_scan_replay_recovery_session_and_refresh(
    env, installed_live_cost_support, answer, completed, expected
):
    installed_live_cost_support()
    db, http = env
    piece = await seed(env, order="synthetic-embroidery")
    choices = {"هل تريد تطريز إسم ع الفروه": answer} if answer is not None else {}
    saved = ([{"service_id": "service-1", "status": "completed",
               "required_quantity": 1, "completed_quantity": 1,
               "source": "option", "customer_selected": True,
               "supplier_invoice_required": True}] if completed else [])
    await db[r.PIECES].update_one({"piece_id": piece["piece_id"]}, {"$set": {
        "services": saved, "product_options_snapshot": choices, "options_normalized": choices,
    }})
    await db[r.PRODUCT_RESOURCE_BINDINGS].delete_many({"user_id": "merchant"})
    await db[r.COST_PROFILES].update_one(
        {"salla_product_id": "salla-1"}, {"$set": {"base_cost": 35}})
    await db[r.RESOURCES].update_one(
        {"id": "service-1"}, {"$set": {"unit_cost": 10, "name": "تطريز الاسم"}})
    await db[r.BINDINGS].insert_one({
        "user_id": "merchant", "salla_product_id": "salla-1",
        "mode": "resource", "option_name": "هل تريد تطريز إسم ع الفروه",
        "value_name": "نعم", "resource_id": "service-1", "quantity": 1,
    })
    request = {"barcode": "MEZAN-PIECE:" + piece["piece_id"], "quantity": 1,
               "client_request_id": "synthetic-embroidery-request"}
    first = await http.post("/supplier-receiving-v1/sessions/session-1/scan", json=request)
    assert first.status_code == 200, first.text
    replay = await http.post("/supplier-receiving-v1/sessions/session-1/scan", json=request)
    recovery = await http.get("/supplier-receiving-v1/sessions/session-1/scan-requests/" + request["client_request_id"])
    session = await http.get("/supplier-receiving-v1/sessions/session-1")
    refresh = await http.post("/supplier-receiving-v1/sessions/session-1/refresh", json={})
    for response in (first, replay, recovery, session, refresh):
        assert response.status_code == 200, response.text
        row = response.json()["scans"][0]
        services = row["invoice_services"]
        assert len(services) == expected, (answer, completed, row)
        if expected:
            assert services[0]["service_id"] == "service-1"
            assert services[0]["customer_selected"] is True
            assert services[0]["reference_unit_price_halalas"] == 1000
            assert services[0]["required_quantity"] == 1
        assert row["reference_product_unit_price_halalas"] == 3500
    if not completed:
        saved_session = await db[r.SESSIONS].find_one({"id": "session-1"})
        requested_services = ([
            r.SupplierReceivingInvoiceServiceRequest(
                service_id="service-1", unit_price_halalas=1000,
            ),
        ] if expected else [])
        draft = r.build_supplier_receiving_invoice(
            session=saved_session,
            scans=[first.json()["scans"][0]],
            saved_at=datetime.now(timezone.utc),
            requested_lines=[r.SupplierReceivingInvoiceLineRequest(
                piece_ids=[piece["piece_id"]],
                product_unit_price_halalas=3500,
                services=requested_services,
            )],
        )
        assert draft["total_halalas"] == (4500 if expected else 3500)
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"event_type": "supplier_piece_scanned", "session_id": "session-1"}) == 1
    assert await db[r.PIECES].count_documents(
        {"supplier_receiving_session_id": "session-1"}) == 1
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0


def test_live_invoice_service_deduplicates_without_suppressing_other_eligible_items():
    live = [
        {"service_id": "embroidery", "customer_selected": True,
         "reference_unit_price_halalas": 1000, "required_quantity": 1},
        {"service_id": "unselected", "customer_selected": False,
         "reference_unit_price_halalas": 9900},
    ]
    existing = [
        {"service_id": "embroidery", "supplier_invoice_required": True,
         "reference_unit_price_halalas": 500},
        {"service_id": "other", "supplier_invoice_required": True,
         "reference_unit_price_halalas": 250},
    ]
    merged = r._merge_supplier_selected_invoice_services(existing, live, [])
    assert [row["service_id"] for row in merged] == ["embroidery", "other"]
    assert [row["reference_unit_price_halalas"] for row in merged] == [1000, 250]
    completed = [{"service_id": "embroidery", "status": "completed",
                  "required_quantity": 1, "completed_quantity": 1}]
    assert [row["service_id"] for row in
            r._merge_supplier_selected_invoice_services(existing, live, completed)] == ["other"]
