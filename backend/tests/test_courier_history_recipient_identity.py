"""Saved recipient phone formats cannot change the historical courier's owner."""
from copy import deepcopy

import pytest

from test_courier_history_identity_recovery import KEY, NUMBER, OWNER, PATH, history_route


async def saved_receiver(db, *, buyer_mobile="500000000", receiver_mobile="966500000000"):
    await db.unified_orders.update_one(KEY, {"$set": {
        "raw_by_source.salla_direct.customer.mobile": buyer_mobile,
        "raw_by_source.salla_direct.shipping.receiver": {
            "name": "Synthetic customer", "phone": receiver_mobile,
        },
    }})


async def allow_mobile_completion(db, state):
    employee = "synthetic-recipient-employee"
    await db.users.insert_one({"id": employee, "created_by": OWNER, "role": "employee"})
    await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": OWNER, "user_id": employee,
        "enabled": True, "permissions": ["app.page.assembly_shipping"]})
    state["actor"] = {"id": OWNER, "_session_client": "amasi_mobile",
                      "_mobile_actor_id": employee, "_mobile_owner_id": OWNER}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [PATH, PATH.removesuffix("refresh") + "complete-and-refresh"])
@pytest.mark.parametrize("buyer_mobile,receiver_mobile", [
    pytest.param("500000000", "966500000000", id="national-to-country-code"),
    pytest.param("966500000000", "500000000", id="country-code-to-national"),
    pytest.param("0500000000", "+966500000000", id="local-to-plus-prefix"),
    pytest.param("00966500000000", "966500000000", id="international-dial-prefix"),
    pytest.param(500000000, "966500000000", id="native-integer-buyer"),
    pytest.param("500000000", 966500000000, id="native-integer-receiver"),
    pytest.param(500000000, 966500000000, id="native-integer-buyer-and-receiver"),
])
async def test_saved_same_recipient_phone_formats_allow_id_free_order_qr(
    history_route, endpoint, buyer_mobile, receiver_mobile,
):
    db, client, state = history_route
    await saved_receiver(db, buyer_mobile=buyer_mobile, receiver_mobile=receiver_mobile)
    if endpoint.endswith("complete-and-refresh"):
        await allow_mobile_completion(db, state)
    before = await db.unified_orders.find_one(KEY)
    provider_before = deepcopy({key: state[key] for key in ("order", "rows", "histories")})
    response = await client.post(endpoint)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] is True and result["print_state"] == "ready"
    assert result["label_type"] == "store_courier" and result["courier_name"] == "مندوب الرياض"
    assert result["print_data"]["order_number"] == NUMBER
    assert result["print_data"]["address"]["street"] == "Fresh child street"
    assert result["print_data"]["qr_code"].startswith("data:image/svg+xml;base64,")
    assert all(result.get(key) is None for key in ("shipment_id", "tracking_number", "shipping_number", "label_url"))
    assert "ARCHIVED-MUST-NOT-PRINT" not in response.text and "archived.pdf" not in response.text
    assert state["order_reads"] == 2 and state["list_reads"]["/orders/42/shipments"] == 2
    assert all(method == "GET" for method, _ in state["calls"])
    assert await db.unified_orders.find_one(KEY) == before
    assert {key: state[key] for key in provider_before} == provider_before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "different_name", "different_valid_number", "foreign_number", "letters", "punctuation",
    "embedded_control", "format_control", "receiver_outer_control", "buyer_control", "non_ascii_digits",
    "boolean_phone", "object_phone", "fractional_phone", "missing_buyer_name", "missing_receiver_name",
    "root_gift", "saved_gift", "saved_gift_source", "fresh_gift", "fresh_phone_missing",
    "child_phone_missing", "foreign_child", "fresh_independent_receiver",
])
async def test_saved_recipient_equivalence_never_bypasses_independent_or_unknown_identity(history_route, fault):
    db, client, state = history_route
    await saved_receiver(db)
    patch = {}
    receiver_key = "raw_by_source.salla_direct.shipping.receiver"
    customer_key = "raw_by_source.salla_direct.customer"
    if fault == "different_name":
        patch[f"{receiver_key}.name"] = "Different synthetic recipient"
    elif fault == "different_valid_number":
        patch[f"{receiver_key}.phone"] = "966511111111"
    elif fault in {"foreign_number", "letters", "punctuation", "embedded_control", "format_control", "receiver_outer_control"}:
        patch[f"{receiver_key}.phone"] = {
            "foreign_number": "+971500000000", "letters": "call966500000000",
            "punctuation": "966-500000000", "embedded_control": "9665\n00000000",
            "format_control": "9665\u200e00000000",
            "receiver_outer_control": "966500000000\t",
        }[fault]
    elif fault in {"buyer_control", "non_ascii_digits", "boolean_phone", "object_phone", "fractional_phone"}:
        patch[f"{customer_key}.mobile"] = {
            "buyer_control": "500000000\t", "non_ascii_digits": "٥٠٠٠٠٠٠٠٠", "boolean_phone": True,
            "object_phone": {"number": "500000000"}, "fractional_phone": 500000000.0,
        }[fault]
    elif fault == "missing_buyer_name":
        patch[f"{customer_key}.full_name"] = None
    elif fault == "missing_receiver_name":
        patch[f"{receiver_key}.name"] = None
    elif fault == "root_gift":
        patch["is_gift"] = True
    elif fault == "saved_gift":
        patch["raw_by_source.salla_direct.is_gift"] = True
    elif fault == "saved_gift_source":
        patch["raw_by_source.salla_direct.source"] = "buy_as_gift"
    elif fault == "fresh_gift":
        state["order"]["is_gift"] = True
    elif fault == "fresh_phone_missing":
        state["order"]["customer"]["mobile"] = None
    elif fault == "child_phone_missing":
        state["rows"][0]["ship_to"]["phone"] = None
    elif fault == "foreign_child":
        state["rows"][0]["order_id"] = 43
    elif fault == "fresh_independent_receiver":
        state["order"]["shipping"] = {"receiver": {"name": "Different synthetic recipient", "phone": "0511111111"}}
    if patch:
        await db.unified_orders.update_one(KEY, {"$set": patch})
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(PATH)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {"recipient_address_unavailable", "courier_history_unproven"}
    assert not response.json().get("print_data")
    assert all(method == "GET" for method, _ in state["calls"])
    assert await db.unified_orders.find_one(KEY) == before
