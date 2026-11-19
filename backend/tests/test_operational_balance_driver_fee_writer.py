"""The real driver delivery route's frozen price and due earning feed the position."""
import pytest

from accounting_shipping_native_contract import SETUP
from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_balance_sources import collect_sources
from store_delivery_domain import carrier_assignment_snapshot
from test_mz2_driver_physical_cash import cash, manual, prepare_delivery
from test_mz2_driver_pos_manual_review import snapshot as database_snapshot
from test_mz2_shipping_native import OWNER, AT


async def prepare_city_courier_delivery(cash, balance, method):
    db = cash.db
    assignment_id, payload = await prepare_delivery(cash, expected=balance, method=method)
    driver = await db.store_drivers.find_one({"user_id": OWNER, "id": "driver-f"})
    driver.update(delivery_fee="17.25", shipping_company="مندوب الرياض")
    snapshot = carrier_assignment_snapshot(driver=driver, shipping_company="مندوب الرياض")
    await db.store_delivery_assignments.update_one({"user_id": OWNER, "id": assignment_id}, {"$set": snapshot})
    # The shared physical-cash fixture starts with SMSA. This test exercises
    # an unchanged city-courier shipment, so its source and snapshot must agree.
    await db.unified_orders.update_one({"user_id": OWNER, "order_number": "1"}, {"$set": {
        "shipping_company": "مندوب الرياض",
        "salla_shipping_current.company_name": "مندوب الرياض",
        "salla_shipping_current.company_code": "mandoob-riyadh",
        "raw_by_source.salla_direct.shipping.company": {"name": "مندوب الرياض", "code": "mandoob-riyadh"},
        "raw_by_source.salla_direct.currency": "SAR", "raw_by_source.salla_direct.total": "500.00",
        "raw_by_source.salla_direct.paid_amount": "0.00" if method else "500.00",
        "raw_by_source.salla_direct.payment_method": "cod" if method else "tamara",
        "raw_by_source.salla_direct.amounts.total.amount": "500.00"}})
    if method:
        payload.update(physical_cash_amount=balance, physical_cash_confirmed=True)
    return assignment_id, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("balance,method", [("500.00", "cash"), ("0.00", None)])
async def test_mobile_delivery_posts_frozen_fee_once_without_a_second_contract(cash, balance, method):
    db = cash.db
    await db[SETUP].update_one({"user_id": OWNER}, {"$pull": {"contracts": {"party_type": "store_driver"}}})
    assignment_id, payload = await prepare_city_courier_delivery(cash, balance, method)
    # Editing the current profile changes neither the assigned carrier nor fee.
    await db.store_drivers.update_one({"user_id": OWNER, "id": "driver-f"}, {"$set": {
        "delivery_fee": 26, "shipping_company": "مندوب جدة"}})
    delivered = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert delivered.status_code == 200, delivered.text
    assignment = await db.store_delivery_assignments.find_one({"user_id": OWNER, "id": assignment_id})
    assert assignment["shipping_company_snapshot"] == "مندوب الرياض"
    assert assignment["delivery_fee_snapshot"] == 17.25
    cash.salla.assert_awaited_once()
    state = {"owner_id": OWNER, "status": "active", "started_at": "2026-09-02T20:00:00Z",
             "openings": {}, "movements": []}
    originals = await db.store_delivery_driver_earnings.find({"user_id": OWNER}).to_list(10)
    assert len(originals) == 1 and originals[0]["amount"] == 17.25

    async def calculate(prior):
        sources = await collect_sources(db, OWNER, state["started_at"], AT, operational_state=prior)
        return reconcile(prior, sources, AT)

    result = await calculate(state)
    fees = result["engine"]["obligations"]
    assert fees["shipping:salla-1:base_shipping"]["confirmed"] == "17.25"
    assert "shipping:salla-1:cod_commission" not in fees
    assert result["engine"]["facts"]["native_driver_earning:salla-1"]["earning_id"] == originals[0]["id"]
    driver = next(r for r in report(result)["parties"] if r["party_type"] == "store_driver")
    assert driver["outstanding_payable"] == "17.25"
    assert driver["outstanding_receivable"] == balance
    assert await calculate(result) == result
    assert result["movements"] == [] and result["openings"] == {}
    assert await db.store_delivery_driver_earnings.find({"user_id": OWNER}).to_list(10) == originals


@pytest.mark.asyncio
@pytest.mark.parametrize("balance,method", [("500.00", "cash"), ("0.00", None)])
@pytest.mark.parametrize("carrier,code", [("SMSA", "smsa-code"), ("iMile للتوصيل", "imile")])
async def test_replaced_carrier_cannot_deliver_or_post_frozen_fee(cash, balance, method, carrier, code):
    db = cash.db
    assignment_id, payload = await prepare_city_courier_delivery(cash, balance, method)
    # A genuine replacement is authoritative even while the flat carrier cache
    # still contains the local courier's old label.
    await db.unified_orders.update_one({"user_id": OWNER, "order_number": "1"}, {"$set": {
        "salla_shipping_current.company_name": carrier,
        "salla_shipping_current.company_code": code,
        "raw_by_source.salla_direct.shipping.company": {"name": carrier, "code": code},
    }})
    before = await database_snapshot(db)
    refused = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "shipment_shipping_company_changed"
    cash.salla.assert_not_awaited()
    assert await database_snapshot(db) == before
    for name in ("store_delivery_driver_earnings", "store_delivery_collections"):
        assert await db[name].count_documents({"user_id": OWNER, "assignment_id": assignment_id}) == 0
