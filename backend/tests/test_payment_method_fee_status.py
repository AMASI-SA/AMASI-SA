"""Current setup must use dated tariffs without claiming order-level pricing."""
from copy import deepcopy
from datetime import date

import pytest
from bson import BSON
from mongomock_motor import AsyncMongoMockClient
from pydantic import ValidationError
from pymongo.errors import ExecutionTimeout

from accounting_onboarding_ssot import POLICIES, _key
from payment_method_fee_status import load_payment_method_setup
from payment_method_setup import PaymentMethodCommissionIn

OWNER = "fee-status-owner"
TODAY = date(2026, 11, 21)


def tariff(provider="emkan", **overrides):
    return {"id": "synthetic-" + provider, "user_id": OWNER, "provider": provider,
            "currency": "SAR", "status": "active", "event_type": "capture",
            "effective_from": "2026-11-01", "percentage": "6.99", "fixed_amount": "1.50",
            "vat_rate": "15", "vat_treatment": "exclusive", "evidence": "synthetic contract",
            "confirmed_by": OWNER, "confirmed_at": "2026-11-01T00:00:00Z", **overrides}


async def seed(names, policies=(), *, mode="legacy_settings", legacy=()):
    db = AsyncMongoMockClient().fee_status_synthetic
    await db.settings.insert_one({"user_id": OWNER, "payment_methods": list(legacy)})
    await db.unified_orders.insert_many([{"user_id": OWNER, "order_number": f"synthetic-{index}",
        "payment_method": name, "order_status": "under_review", "payment_status": "pending"}
        for index, name in enumerate(names)])
    if policies:
        await db[POLICIES].insert_one({"user_id": OWNER, "policies": list(policies)})
    await db[POLICIES].insert_one({"_id": _key(OWNER, "fee_source_mode"), "user_id": OWNER,
                                 "fee_source_mode": mode})
    return db


async def frozen(db):
    return {name: [BSON.encode(row) for row in await db[name].find({}).to_list(None)]
            for name in await db.list_collection_names()}


@pytest.mark.asyncio
async def test_current_emkan_aliases_and_credit_card_use_tariffs_not_conflicting_legacy_rows():
    legacy = [{"name": name, "commission_percent": rate, "fixed_fee": 1, "vat_percent": 15}
              for name, rate in [("EmkanInstallment", 5), ("إمكان", 6),
                                  ("credit card", 2), ("بطاقة ائتمانية", 4)]]
    db = await seed(["EmkanInstallment", "emkan_installment", "EMKAN_INSTALLMENT", "credit_card"],
        [tariff(), tariff("salla", payment_method="credit_card", percentage="2.2", fixed_amount="1")],
        legacy=legacy)
    before = await frozen(db)
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["complete"] is True and result["pending_count"] == result["review_count"] == 0
    assert all(row["status"] == "configured" and row["fee_source"] == "mz2_policies" for row in result["items"])
    assert {row["policy_id"] for row in result["items"]} == {"synthetic-emkan", "synthetic-salla"}
    assert await frozen(db) == before


@pytest.mark.asyncio
async def test_waiting_is_not_discovered_or_saved_as_a_commission_and_orders_stay_unchanged():
    db = await seed(["waiting", " WAITING ", "Waiting", "wating", " WATING ", "paypal", "cod", "waiting_gateway"])
    before = await frozen(db)
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert {row["name"] for row in result["items"]} == {"paypal", "cod", "waiting_gateway"}
    for name in ("waiting", " WAITING ", "Waiting", "wating", " WATING "):
        with pytest.raises(ValidationError):
            PaymentMethodCommissionIn(name=name, commission_percent=0, fixed_fee=0, vat_percent=0,
                                      expected_revision="0" * 64)
    assert await frozen(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"effective_from": "2026-12-01"}, {"effective_to": "2026-11-20"},
    {"status": "inactive"}, {"currency": "USD"}, {"event_type": "payout"},
])
async def test_policy_mode_cannot_inherit_legacy_when_current_sar_capture_tariff_is_missing(overrides):
    db = await seed(["emkan_installment"], [tariff(**overrides)], mode="mz2_policies",
        legacy=[{"name": "إمكان", "commission_percent": 1, "fixed_fee": 0, "vat_percent": 0}])
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["pending_count"] == 1
    assert result["items"][0]["status"] == "needs_commission"
    assert result["items"][0]["commission_percent"] is None


@pytest.mark.asyncio
async def test_current_rail_specificity_and_missing_apple_pay_match_engine():
    db = await seed(["credit_card", "Apple Pay"], [tariff("salla", payment_method="credit_card")])
    rows = {row["name"]: row for row in (await load_payment_method_setup(db, OWNER, current_day=TODAY))["items"]}
    assert rows["credit_card"]["status"] == "configured"
    assert rows["Apple Pay"]["status"] == "needs_commission"
    await db[POLICIES].update_one({"policies": {"$exists": True}}, {"$push": {"policies": tariff("salla", id="generic")}})
    rows = {row["name"]: row for row in (await load_payment_method_setup(db, OWNER, current_day=TODAY))["items"]}
    assert rows["credit_card"]["policy_id"] == "synthetic-salla"
    assert rows["Apple Pay"]["policy_id"] == "generic"


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_count", [1, 2])
async def test_valid_plans_are_configured_without_claiming_an_order_plan_or_rate(plan_count):
    policies = [tariff("tabby", id=f"tabby-{index}", plan_id=str(index)) for index in range(plan_count)]
    db = await seed(["tabby_installment"], [*policies, tariff("tabby", id="generic")])
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["pending_count"] == result["review_count"] == 0
    row = result["items"][0]
    assert row["status"] == "configured" and row["configuration_kind"] == "plan_specific"
    assert row["plan_count"] == plan_count
    assert row["commission_percent"] is None
    # Setup coverage must not loosen the calculator's order-specific proof.
    from operational_provider_fees import provider_projection
    with pytest.raises(ValueError, match="provider_payment_plan_required"):
        provider_projection({"provider": "tabby", "currency": "SAR", "gross": "100.00"}, policies, TODAY)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{"vat_treatment": "unknown"}, {}])
async def test_invalid_or_overlapping_individual_plans_stay_in_review(invalid):
    policies = [tariff("tabby", plan_id="4"), tariff("tabby", id="other", plan_id="4", **invalid)]
    db = await seed(["tabby_installment"], policies)
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["review_count"] == 1 and result["items"][0]["status"] == "needs_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [None, 123])
async def test_malformed_policy_array_remains_unverified_instead_of_failing_the_endpoint(malformed):
    db = await seed(["emkan_installment"])
    await db[POLICIES].insert_one({"user_id": OWNER, "policies": malformed})
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["complete"] is False and result["items"][0]["status"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize("policies", [[tariff(), tariff(id="overlap")],
    [tariff(vat_treatment="unknown")], [tariff(fee_model_version=2, components=[])],
    [tariff(effective_from="bad date")]])
async def test_invalid_or_ambiguous_policy_stays_in_review(policies):
    db = await seed(["EmkanInstallment"], policies)
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["items"][0]["status"] == "needs_review"
    assert result["review_count"] == 1


@pytest.mark.asyncio
async def test_foreign_embedded_policy_and_failed_policy_reads_are_not_configuration_success(monkeypatch):
    db = await seed(["EmkanInstallment"], [tariff(user_id="foreign-owner")])
    foreign = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert foreign["complete"] is False and foreign["items"][0]["status"] == "unverified"

    async def unavailable(*args):
        raise ExecutionTimeout("synthetic unavailable")
    monkeypatch.setattr("payment_method_fee_status.list_fee_policies", unavailable)
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["complete"] is False and result["items"][0]["status"] == "unverified"


@pytest.mark.asyncio
async def test_paypal_bank_and_cod_keep_saved_terms_even_in_policy_mode():
    methods = [{"name": name, "commission_percent": rate, "fixed_fee": fixed, "vat_percent": 0}
               for name, rate, fixed in [("paypal", 4.9, 1.13), ("bank", 0, 0), ("cod", 0, 0)]]
    db = await seed(["paypal", "bank", "cod"], [tariff()], mode="mz2_policies", legacy=methods)
    result = await load_payment_method_setup(db, OWNER, current_day=TODAY)
    assert result["pending_count"] == result["review_count"] == 0
    assert all(row["status"] == "configured" for row in result["items"])
    assert next(row for row in result["items"] if row["name"] == "paypal")["commission_percent"] == 4.9
