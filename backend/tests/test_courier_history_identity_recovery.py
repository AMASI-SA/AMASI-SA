"""The real print route may recover a carrier, never resurrect an archived AWB."""
from copy import deepcopy

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest

import operational_atomic
import preparation_piece_operations as pieces
from fulfillment_v2_routes import make_fulfillment_v2_router
from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as shipping
from order_engine.mapper import map_salla_order
from salla_shipping import CURRENT_SHIPPING
from salla_integration.service import SallaError


OWNER = "synthetic-history-owner"
NUMBER = "910000002"
KEY = {"user_id": OWNER, "order_number": NUMBER}
PATH = f"/fulfillment-v2/completed/{NUMBER}/carrier-label/v1/refresh"
VERSION = "2026-01-02T12:00:00+00:00"
NOTE = "تم تعديل بيانات الشحن من iMile للتوصيل إلى: مندوب الرياض"


def page(rows):
    return {"success": True, "data": deepcopy(rows), "pagination": {
        "total": len(rows), "count": len(rows), "currentPage": 1,
        "totalPages": 1, "links": [],
    }}


@pytest.fixture
async def history_route(monkeypatch):
    mongo = AsyncMongoMockClient()
    db = mongo.courier_history_identity_test
    order = {"id": 42, "reference_id": NUMBER, "status": {"slug": "completed"},
             "delivery_method": "shipping", "updated_at": VERSION,
             "date": "2026-01-02T04:00:00+00:00",
             "items": [{"id": "history-line", "name": "Synthetic product", "quantity": 1}],
             "customer": {"full_name": "Synthetic customer", "mobile": "0500000000"}}
    child = {"id": 51, "order_id": 42, "type": "shipment", "status": "created",
             "courier_name": "مندوب الرياض", "courier_id": 77,
             "meta": {"app_id": 0}, "created_at": None,
             "tracking_number": "ARCHIVED-MUST-NOT-PRINT",
             "label_url": "https://example.invalid/archived.pdf",
             "ship_to": {"name": "Synthetic customer", "phone": "+966500000000",
                         "city": "الرياض", "street": "Fresh child street", "short_address": "TEST1234"}}
    histories = [{"id": index + 100, "type": "activity", "note": note,
                  "status": status, "customized": {"id": index + 1, "name": status},
                  "created_by": {"id": 50, "name": "Synthetic operator"},
                  "created_at": {"date": f"2026-01-02 08:{index * 10:02d}:00", "timezone": "Asia/Riyadh"}}
                 for index, (note, status) in enumerate([
                     ("تم إنشاء الطلب", "بإنتظار المراجعة"),
                     (NOTE, "بإنتظار المراجعة"),
                     ("", "قيد التنفيذ"),
                     ("", "تم التنفيذ"),
                 ])]
    await db.unified_orders.insert_one({
        **KEY, "order_id": "42", "order_status": "completed", "order_status_slug": "completed",
        "raw_by_source": {"salla_direct": deepcopy(order)},
        "g47_salla_snapshot": {"revision": "synthetic-revision", "source_updated_at": VERSION},
        "shipping_company": "shipping", "salla_shipment_id": None,
        CURRENT_SHIPPING: {"company_name": "shipping", "company_code": None,
            "source_path": "delivery_method", "source_kind": "order", "event_name": "order.snapshot",
            "shipment_id": None, "status": "shipping_ready", "superseded_shipment_ids": ["51"],
            "provider_updated_at": VERSION, "carrier_updated_at": VERSION},
        "shipping_address_raw": {"city": "الرياض", "street": "Verified order street"},
        "shipping_address_source_path": "webhook.shipping.address",
        "salla_address_verified_at": "2026-01-02T06:00:00+00:00",
        "salla_address_provider_updated_at": "2026-01-02T06:00:00+00:00",
    })
    await db.order_review_workflows.insert_one({**KEY, "stage": "completed", "assembly_status": "completed"})
    await db[pieces.PIECES].insert_one({**KEY, "piece_id": "synthetic-history-piece", "status": "received",
        "assembly_status": "ready", "order_item_id": map_salla_order(order).items[0].order_item_id, "unit_index": 1})
    state = {"order": order, "rows": [child], "histories": histories, "calls": [],
             "history_page": None, "shipment_page": None, "on_read": None, "order_reads": 0,
             "list_reads": {}, "actor": {"id": OWNER, "role": "owner"},
             "unavailable": set(), "allow_completion_control": False,
             "store": {"name": {"ar": "متجر الاختبار", "en": None}}}

    async def provider(database, owner, method, path, **kwargs):
        assert database is db and owner == OWNER
        assert method == "GET" or state["allow_completion_control"], "History recovery must never change Salla"
        state["calls"].append((method, path))
        if method == "POST":
            assert state["allow_completion_control"] and path == "/orders/42/status"
            assert kwargs == {"json": {"slug": "completed"}, "single_post_attempt": True}
            return {"success": True}
        if path == "/orders/42":
            state["order_reads"] += 1
        if path in {"/orders/42/shipments", "/shipments"}:
            state["list_reads"][path] = state["list_reads"].get(path, 0) + 1
        if state["on_read"] is not None:
            await state["on_read"](path)
        if path in state["unavailable"]:
            raise SallaError("synthetic scope unavailable", status_code=403)
        if path == "/orders/42":
            return {"success": True, "data": deepcopy(state["order"])}
        if path in {"/orders/42/shipments", "/shipments"}:
            return deepcopy(state["shipment_page"]) if state["shipment_page"] is not None else page(state["rows"])
        if path == "/orders/42/histories":
            return deepcopy(state["history_page"]) if state["history_page"] is not None else page(state["histories"])
        if path in {"/shipments/51", "/shipments/51/tracking"}:
            return {"success": True, "data": deepcopy(state["rows"][0])}
        if path == "/store/info":
            return {"success": True, "data": deepcopy(state["store"])}
        raise AssertionError(f"Unexpected provider path: {path}")

    async def local_owner(database, owner, callback, **kwargs):
        # In-memory tests exercise real fences, not Mongo transaction isolation.
        assert database is db and owner == OWNER
        return await callback(database)

    monkeypatch.setattr(compat, "call_salla", provider)
    monkeypatch.setattr(shipping, "call_salla", provider)
    monkeypatch.setattr(operational_atomic, "operational_owner", local_owner)
    monkeypatch.setattr(shipping, "operational_owner", local_owner)
    async def actor():
        return state["actor"]
    app = FastAPI()
    app.include_router(make_fulfillment_v2_router(db, actor))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield db, client, state
    mongo.close()


@pytest.mark.asyncio
async def test_native_history_recovers_order_qr_without_reactivating_archived_shipment(history_route):
    db, client, state = history_route
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(PATH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] is True and result["print_state"] == "ready"
    assert result["courier_name"] == "مندوب الرياض"
    assert result["label_type"] == "store_courier"
    assert all(result.get(key) is None for key in ("shipment_id", "tracking_number", "shipping_number", "label_url"))
    assert result["print_data"]["address"]["street"] == "Fresh child street"
    assert result["print_data"]["order_number"] == NUMBER
    assert result["print_data"]["store_name"] == "متجر الاختبار"
    assert result["print_data"]["qr_code"].startswith("data:image/svg+xml;base64,")
    assert "ARCHIVED-MUST-NOT-PRINT" not in response.text and "archived.pdf" not in response.text
    assert state["order_reads"] == 2
    assert ("GET", "/orders/42/histories") in state["calls"]
    assert await db.unified_orders.find_one(KEY) == before
    workflow = await db.order_review_workflows.find_one(KEY)
    assert workflow["stage"] == workflow["assembly_status"] == "completed"
    assert "assembly_delivery" not in workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("aliases", [
    pytest.param({}, id="missing"),
    pytest.param({"company_name": None, "shipping_company_name": None, "external_company_name": None}, id="null"),
    pytest.param({"external_company_name": ""}, id="native-blank-external-company"),
    pytest.param({"company_name": "", "shipping_company_name": "", "external_company_name": ""}, id="blank"),
    pytest.param({"company_name": "مندوب الرياض"}, id="matching-company"),
    pytest.param({"shipping_company_name": "مندوب الرياض"}, id="matching-shipping-company"),
    pytest.param({"external_company_name": "مندوب الرياض"}, id="matching-external-company"),
    pytest.param({"company_name": "", "shipping_company_name": None, "external_company_name": "مندوب الرياض"}, id="mixed"),
])
async def test_optional_child_carrier_aliases_allow_absence_or_matching_name(history_route, aliases):
    db, client, state = history_route
    state["rows"][0].update(aliases)
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(PATH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["contract_version"] == 1 and result["print_state"] == "ready" and result["ready"] is True
    assert result["label_type"] == "store_courier" and result["courier_name"] == "مندوب الرياض"
    assert result["print_data"]["order_number"] == NUMBER
    assert result["print_data"]["qr_code"].startswith("data:image/svg+xml;base64,")
    assert all(result.get(key) is None for key in ("shipment_id", "tracking_number", "shipping_number", "label_url"))
    assert "ARCHIVED-MUST-NOT-PRINT" not in response.text and "archived.pdf" not in response.text
    assert state["order_reads"] == 2 and state["list_reads"]["/orders/42/shipments"] == 2
    assert all(method == "GET" for method, _ in state["calls"])
    assert await db.unified_orders.find_one(KEY) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["company_name", "shipping_company_name", "external_company_name"])
@pytest.mark.parametrize("value", [
    pytest.param(" ", id="whitespace"),
    pytest.param(" مندوب الرياض", id="padded-name"),
    pytest.param("مندوب\x00 الرياض", id="control-character"),
    pytest.param("مندوب\u200e الرياض", id="directional-control"),
    pytest.param(False, id="boolean"),
    pytest.param(0, id="number"),
    pytest.param([], id="list"),
    pytest.param({}, id="object"),
    pytest.param("مندوب جدة", id="other-city-courier"),
    pytest.param("iMile للتوصيل", id="external-carrier"),
])
async def test_optional_child_carrier_aliases_reject_invalid_or_conflicting_values(history_route, alias, value):
    db, client, state = history_route
    state["rows"][0].update(company_name="", shipping_company_name=None, external_company_name="")
    state["rows"][0][alias] = value
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(PATH)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "courier_history_unproven"
    assert response.json()["detail"]["print_state"] == "failed"
    assert not response.json().get("print_data")
    assert all(method == "GET" for method, _ in state["calls"])
    assert await db.unified_orders.find_one(KEY) == before


@pytest.mark.asyncio
async def test_existing_explicit_completion_remains_available_outside_completed_cohort(history_route):
    db, client, state = history_route
    employee = "synthetic-history-employee"
    await db.users.insert_one({"id": employee, "created_by": OWNER, "role": "employee"})
    await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": OWNER, "user_id": employee,
        "enabled": True, "permissions": ["app.page.assembly_shipping"]})
    state["actor"] = {"id": OWNER, "_session_client": "amasi_mobile",
                      "_mobile_actor_id": employee, "_mobile_owner_id": OWNER}
    state["order"]["status"] = {"slug": "in_progress"}
    state["allow_completion_control"] = True
    await db.unified_orders.update_one(KEY, {"$set": {
        "order_status": "in_progress", "order_status_slug": "in_progress",
        "raw_by_source.salla_direct.status": {"slug": "in_progress"},
    }})
    response = await client.post(PATH.removesuffix("refresh") + "complete-and-refresh")
    assert response.status_code == 200, response.text
    assert state["calls"].count(("POST", "/orders/42/status")) == 1
    assert ("GET", "/orders/42/histories") not in state["calls"]
    assert response.json()["ready"] is False


@pytest.mark.asyncio
async def test_native_numeric_references_and_localized_store_names_are_supported(history_route):
    _, client, state = history_route
    state["order"]["reference_id"] = int(NUMBER)
    state["order"]["updated_at"] = {"date": "2026-01-02 15:00:00.000000", "timezone_type": 3, "timezone": "Asia/Riyadh"}
    state["rows"][0].update(order_reference_id=int(NUMBER), order={"id": 42, "reference_id": int(NUMBER)})
    for index, row in enumerate(state["histories"]):
        row["order_id"] = 42
        row["reference_id"] = int(NUMBER)
        row["customized"].update(type="original", slug=["under_review", "under_review", "in_progress", "completed"][index],
            sort=index, message=None, icon="synthetic", is_active=True, translations={}, original=None, parent=None)
    state["store"]["name"] = {"ar": "", "en": "Synthetic localized store", "name": "Fallback"}
    response = await client.post(PATH)
    assert response.status_code == 200 and response.json()["ready"] is True, response.text
    assert response.json()["print_data"]["store_name"] == "Synthetic localized store"


@pytest.mark.asyncio
async def test_saved_archived_destination_never_overrides_fresh_child(history_route):
    db, client, _ = history_route
    await db.unified_orders.update_one(KEY, {"$set": {
        "shipping_address_raw": {"city": "الرياض", "street": "ARCHIVED STREET MUST NOT WIN"},
        "shipping_address_source_path": "shipment.ship_to",
    }})
    response = await client.post(PATH)
    assert response.status_code == 200 and response.json()["ready"] is True, response.text
    assert response.json()["print_data"]["address"]["street"] == "Fresh child street"
    assert "ARCHIVED STREET MUST NOT WIN" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [PATH, PATH.removesuffix("refresh") + "complete-and-refresh"])
@pytest.mark.parametrize("include_blank_external_alias", [False, True])
async def test_fresh_child_destination_is_bound_without_archive_or_provider_write(history_route, endpoint, include_blank_external_alias):
    db, client, state = history_route
    if include_blank_external_alias:
        state["rows"][0]["external_company_name"] = ""
    if endpoint.endswith("complete-and-refresh"):
        employee = "synthetic-history-employee"
        await db.users.insert_one({"id": employee, "created_by": OWNER, "role": "employee"})
        await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": OWNER, "user_id": employee,
            "enabled": True, "permissions": ["app.page.assembly_shipping"]})
        state["actor"] = {"id": OWNER, "_session_client": "amasi_mobile",
                          "_mobile_actor_id": employee, "_mobile_owner_id": OWNER}
    await db.unified_orders.update_one(KEY, {"$set": {
        "shipping_address_raw": {"city": "الرياض"},
        "shipping_address_source_path": "order.customer",
        "salla_shipment_snapshot": {"ship_to": {"street": "DO NOT USE THIS OLD ADDRESS"}},
    }})
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(endpoint)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] is True
    assert result["print_data"]["address"]["street"] == "Fresh child street"
    assert result["print_data"]["address"]["short_address"] == "TEST1234"
    assert all(result.get(key) is None for key in ("shipment_id", "tracking_number", "shipping_number", "label_url"))
    assert "DO NOT USE" not in response.text and "ARCHIVED-MUST-NOT-PRINT" not in response.text
    assert await db.unified_orders.find_one(KEY) == before
    assert state["order_reads"] == 2
    assert state["list_reads"]["/orders/42/shipments"] == 2
    assert all(method == "GET" for method, _ in state["calls"])


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable, selected", [
    ({"/shipments", "/shipments/51", "/shipments/51/tracking"}, "/orders/42/shipments"),
    ({"/orders/42/shipments", "/shipments/51"}, "/shipments"),
])
async def test_complete_selected_list_survives_unavailable_other_endpoints(history_route, unavailable, selected):
    _, client, state = history_route
    state["unavailable"] = unavailable
    response = await client.post(PATH)
    assert response.status_code == 200 and response.json()["ready"] is True, response.text
    assert state["list_reads"][selected] == 2
    assert all(path not in {"/shipments/51", "/shipments/51/tracking"} for _, path in state["calls"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "comment_change", "free_note_after", "later_carrier_change", "second_reassignment",
    "missing_change", "duplicate_event", "history_incomplete", "history_next_page", "history_bad_total",
    "foreign_history", "contradictory_customized_status", "future_history", "ambiguous_time",
    "second_child", "inactive_second_child", "foreign_child", "child_ref_mismatch", "child_cancelled",
    "child_return", "child_app_nonzero", "child_other_city_courier", "list_incomplete",
    "missing_history", "both_lists_unavailable", "extra_retired_id",
])
async def test_incomplete_or_contradictory_history_never_authorizes_qr(history_route, fault):
    db, client, state = history_route
    if fault == "comment_change":
        state["histories"][1]["type"] = "comment"
    elif fault == "free_note_after":
        state["histories"][-1]["note"] = "A manually added note"
    elif fault == "later_carrier_change":
        state["histories"][-1]["note"] = "تم تعديل بيانات الشحن من مندوب الرياض إلى: SMSA"
    elif fault == "second_reassignment":
        state["histories"][-1]["note"] = NOTE
    elif fault == "missing_change":
        state["histories"][1]["note"] = "تم تحديث الطلب"
    elif fault == "duplicate_event":
        state["histories"][-1]["id"] = state["histories"][0]["id"]
    elif fault.startswith("history_"):
        state["history_page"] = page(state["histories"])
        change = {"history_incomplete": {"totalPages": 2}, "history_next_page": {"links": {"next": "more"}},
                  "history_bad_total": {"total": 5}}[fault]
        state["history_page"]["pagination"].update(change)
    elif fault == "foreign_history":
        state["histories"][1]["order_id"] = 43
    elif fault == "contradictory_customized_status":
        state["histories"][-1]["customized"]["name"] = "ملغي"
    elif fault == "future_history":
        state["histories"][-1]["created_at"] = "2099-01-01T00:00:00+00:00"
    elif fault == "ambiguous_time":
        state["histories"][-1]["created_at"] = deepcopy(state["histories"][-2]["created_at"])
    elif fault in {"second_child", "inactive_second_child"}:
        state["rows"].append({**deepcopy(state["rows"][0]), "id": 52,
                              "status": "cancelled" if fault == "inactive_second_child" else "created"})
    elif fault == "foreign_child":
        state["rows"][0]["order_id"] = 43
    elif fault == "child_ref_mismatch":
        state["rows"][0]["order_reference_id"] = "foreign-order"
    elif fault == "child_cancelled":
        state["rows"][0]["status"] = "cancelled"
    elif fault == "child_return":
        state["rows"][0]["type"] = "return"
    elif fault == "child_app_nonzero":
        state["rows"][0]["meta"]["app_id"] = 3
    elif fault == "child_other_city_courier":
        state["rows"][0]["courier_name"] = "مندوب جدة"
    elif fault == "list_incomplete":
        state["shipment_page"] = page(state["rows"])
        state["shipment_page"]["pagination"]["total"] = 2
    elif fault == "missing_history":
        state["unavailable"].add("/orders/42/histories")
    elif fault == "both_lists_unavailable":
        state["unavailable"].update({"/orders/42/shipments", "/shipments"})
    else:
        await db.unified_orders.update_one(KEY, {"$set": {f"{CURRENT_SHIPPING}.superseded_shipment_ids": ["51", "52"]}})
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(PATH)
    assert response.status_code in {200, 409}, response.text
    assert response.json().get("ready") is not True
    assert not response.json().get("print_data")
    assert await db.unified_orders.find_one(KEY) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["city_only", "wrong_city", "wrong_name", "wrong_phone", "sender_only", "gift",
                                  "saved_independent_recipient", "saved_raw_gift", "saved_raw_gift_source"])
async def test_current_recipient_and_street_are_required(history_route, fault):
    db, client, state = history_route
    await db.unified_orders.update_one(KEY, {"$set": {
        "shipping_address_raw": {"city": "الرياض"}, "shipping_address_source_path": "order.customer",
    }})
    child = state["rows"][0]
    if fault == "city_only":
        child["ship_to"].pop("street")
        child["ship_to"].pop("short_address")
    elif fault == "wrong_city":
        child["ship_to"]["city"] = "جدة"
    elif fault == "wrong_name":
        child["ship_to"]["name"] = "Another recipient"
    elif fault == "wrong_phone":
        child["ship_to"]["phone"] = "+966511111111"
    elif fault == "sender_only":
        child["ship_from"] = child.pop("ship_to")
    elif fault == "saved_independent_recipient":
        await db.unified_orders.update_one(KEY, {"$set": {
            "raw_by_source.salla_direct.receiver": {"name": "Independent synthetic recipient", "mobile": "0511111111"},
        }})
    elif fault in {"saved_raw_gift", "saved_raw_gift_source"}:
        key, value = ("is_gift", True) if fault == "saved_raw_gift" else ("source", "buy_as_gift")
        await db.unified_orders.update_one(KEY, {"$set": {f"raw_by_source.salla_direct.{key}": value}})
    else:
        state["order"]["source"] = "buy_as_gift"
    response = await client.post(PATH)
    assert response.status_code == 409, response.text
    assert response.json().get("ready") is not True and not response.json().get("print_data")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["street", "phone"])
async def test_second_complete_list_must_preserve_child_material_even_without_clock_change(history_route, field):
    db, client, state = history_route
    before = await db.unified_orders.find_one(KEY)
    async def on_read(path):
        if path == "/orders/42/shipments" and state["list_reads"].get(path) == 2:
            state["rows"][0]["ship_to"][field] = "Changed destination" if field == "street" else "+966511111111"
    state["on_read"] = on_read
    response = await client.post(PATH)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
    assert not response.json().get("print_data")
    assert await db.unified_orders.find_one(KEY) == before
