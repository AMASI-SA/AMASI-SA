"""The real driver delivery route's frozen price and due earning feed the position."""
import pytest

from accounting_shipping_native_contract import SETUP
from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_balance_sources import collect_sources
from store_delivery_domain import carrier_assignment_snapshot
from test_mz2_driver_physical_cash import cash, manual, prepare_delivery
from test_mz2_shipping_native import OWNER, AT


@pytest.mark.asyncio
@pytest.mark.parametrize("balance,method", [("500.00", "cash"), ("0.00", None)])
async def test_mobile_delivery_posts_frozen_fee_once_without_a_second_contract(cash, balance, method):
    db = cash.db
    await db[SETUP].update_one({"user_id": OWNER}, {"$pull": {"contracts": {"party_type": "store_driver"}}})
    assignment_id, payload = await prepare_delivery(cash, expected=balance, method=method)
    driver = await db.store_drivers.find_one({"user_id": OWNER, "id": "driver-f"})
    driver.update(delivery_fee="17.25", shipping_company="مندوب الرياض")
    snapshot = carrier_assignment_snapshot(driver=driver, shipping_company="مندوب الرياض")
    await db.store_delivery_assignments.update_one({"user_id": OWNER, "id": assignment_id}, {"$set": snapshot})
    # A later change to the current profile must not reprice the agreed assignment.
    await db.store_drivers.update_one({"user_id": OWNER, "id": "driver-f"}, {"$set": {"delivery_fee": 26}})
    await db.unified_orders.update_one({"user_id": OWNER, "order_number": "1"}, {"$set": {
        "raw_by_source.salla_direct.currency": "SAR", "raw_by_source.salla_direct.total": "500.00",
        "raw_by_source.salla_direct.paid_amount": "0.00" if method else "500.00",
        "raw_by_source.salla_direct.payment_method": "cod" if method else "tamara",
        "raw_by_source.salla_direct.amounts.total.amount": "500.00"}})
    if method:
        payload.update(physical_cash_amount=balance, physical_cash_confirmed=True)
    delivered = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert delivered.status_code == 200, delivered.text
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
