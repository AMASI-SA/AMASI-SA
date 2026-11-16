"""Observed payment methods require explicit merchant terms, never invented fees."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json

import pytest
import pytest_asyncio
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from pydantic import ValidationError
from pymongo.errors import ExecutionTimeout

from dashboard_ads_costs import DashboardAdsCostCollector
from excel_parser import match_settings, normalize_name
import payment_method_setup as setup
from payment_methods import DEFAULT_PAYMENT_METHODS
from payment_method_setup import (
    PaymentMethodCommissionIn,
    load_payment_method_setup,
    payment_settings_revision,
    save_payment_method_commission,
)


OWNER = "payment-setup-synthetic-owner"


@pytest_asyncio.fixture
async def db():
    database = AsyncMongoMockClient().payment_setup_synthetic
    await database.settings.create_index("user_id", unique=True)
    return database


def terms(name, **overrides):
    return {"name": name, "commission_percent": 3, "fixed_fee": 1,
            "vat_percent": 15, **overrides}


async def seed(db, names=("PayPal",), methods=()):
    settings = {"user_id": OWNER, "payment_methods": list(methods),
                "shipping_companies": [{"name": "carrier", "cost_per_order": 0,
                                        "vat_percent": 0}],
                "unrelated_preference": "preserve"}
    await db.settings.insert_one(deepcopy(settings))
    if names:
        await db.unified_orders.insert_many([
            {"user_id": OWNER, "order_number": f"synthetic-{index}",
             "payment_method": name, "actual_payment_fee": 0, "payment_status": "paid"}
            for index, name in enumerate(names)
        ])
    return settings


@pytest.mark.asyncio
async def test_unknown_method_stays_pending_until_explicit_fee_save(db):
    settings = await seed(db)
    before = await load_payment_method_setup(db, OWNER)
    assert before["complete"] is True
    assert before["pending_count"] == 1
    method = before["items"][0]
    assert method["name"] == "PayPal"
    assert method["status"] == "needs_commission"
    assert all(method[field] is None for field in ("commission_percent", "fixed_fee", "vat_percent"))
    assert (await db.settings.find_one({"user_id": OWNER}))["payment_methods"] == []

    payload = PaymentMethodCommissionIn(name="PayPal", commission_percent=3,
        fixed_fee=1, vat_percent=15, expected_revision=before["revision"])
    response = await save_payment_method_commission(db, OWNER, "synthetic-owner-actor", payload)
    after = response["payment_method_setup"]
    assert after["complete"] is True and after["pending_count"] == 0
    assert after["items"] == [{**method, "status": "configured", "commission_percent": 3,
                               "fixed_fee": 1, "vat_percent": 15}]
    saved = await db.settings.find_one({"user_id": OWNER})
    assert saved["payment_methods"] == [terms("PayPal")]
    assert saved["shipping_companies"] == settings["shipping_companies"]
    assert saved["unrelated_preference"] == "preserve"
    assert after["revision"] != before["revision"]
    assert sorted(await db.list_collection_names()) == ["settings", "unified_orders"]


@pytest.mark.asyncio
async def test_collector_payment_completeness_changes_only_after_real_terms(db):
    settings = await seed(db)
    order = {"payment_method": "PayPal", "shipping_company": "carrier",
             "currency": "SAR", "total_amount": 100, "actual_payment_fee": 0,
             "products": [{"product_id": "synthetic-product", "quantity": 1}]}

    async def collect(config):
        collector = DashboardAdsCostCollector()
        collector.capture_settings(config)
        collector.begin_order(order)
        collector.observe_line(order, order["products"][0], {"base_complete": True, "quantity": 1})
        collector.finish_order(order, 10)
        return await collector.summary([order], scope_complete=True)

    unknown = await collect(settings)
    assert unknown["payment_fees_complete"] is False
    assert unknown["payment_fees_sar"] is None and unknown["total_cost_sar"] is None
    assert "payment_fees_unverified" in unknown["reason_codes"]
    await save_payment_method_commission(db, OWNER, "synthetic-owner-actor",
        PaymentMethodCommissionIn(name="PayPal", commission_percent=3, fixed_fee=1,
            vat_percent=15, expected_revision=payment_settings_revision(settings)))
    known = await collect(await db.settings.find_one({"user_id": OWNER}))
    assert known["payment_fees_complete"] is True
    assert known["payment_fees_sar"] == 4.60
    assert known["total_cost_sar"] == 14.60
    assert known["complete"] is True


class ReadOnlyCollection:
    def __init__(self, collection, reads):
        self.collection, self.reads = collection, reads

    def find(self, *args, **kwargs):
        self.reads.append((self.collection.name, "find", args, kwargs))
        return self.collection.find(*args, **kwargs)

    def aggregate(self, *args, **kwargs):
        self.reads.append((self.collection.name, "aggregate", args, kwargs))
        return self.collection.aggregate(*args, **kwargs)

    def __getattr__(self, name):
        raise AssertionError(f"discovery cannot call {self.collection.name}.{name}")


class ReadOnlyDB:
    def __init__(self, db):
        self.reads = []
        self.settings = ReadOnlyCollection(db.settings, self.reads)
        self.unified_orders = ReadOnlyCollection(db.unified_orders, self.reads)


@pytest.mark.asyncio
async def test_discovery_is_owner_wide_names_only_bounded_and_read_only(db):
    config = await seed(db, names=("PayPal", "mada"), methods=(terms("مدى"),))
    await db.unified_orders.insert_one({"user_id": "another-synthetic-owner", "payment_method": "Secret foreign gateway"})
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        "order_date": "2001-01-01", "order_status": "cancelled", "customer_name": "DO NOT READ"}})
    await db.settings.insert_one({"user_id": "another-synthetic-owner", "payment_methods": [terms("PayPal", commission_percent=99)]})
    readonly = ReadOnlyDB(db)
    result = await load_payment_method_setup(readonly, OWNER)
    assert result["complete"] is True and result["pending_count"] == 1
    assert {row["name"] for row in result["items"]} == {"PayPal", "mada"}
    assert "DO NOT READ" not in json.dumps(result)
    assert "foreign" not in json.dumps(result)
    assert len(readonly.reads) == 2
    collection, operation, args, kwargs = readonly.reads[-1]
    assert (collection, operation) == ("unified_orders", "aggregate")
    assert args[0][0]["$match"]["user_id"] == OWNER
    assert args[0][1] == {"$group": {"_id": "$payment_method"}}
    assert args[0][-1] == {"$limit": setup.MAX_OBSERVED_METHODS + 1}
    assert 0 < kwargs["maxTimeMS"] <= 5000
    assert kwargs["allowDiskUse"] is False
    readonly.reads.clear()
    assert await load_payment_method_setup(readonly, OWNER, settings=config) == result
    assert [row[:2] for row in readonly.reads] == [("unified_orders", "aggregate")]


@pytest.mark.asyncio
async def test_case_space_dedup_is_stable_and_full_names_do_not_share_slug_identity(db):
    names = [" paypal ", "PayPal", "PAYPAL", "Gateway-" + "x" * 65 + "A",
             "Gateway-" + "x" * 65 + "B", "Other-Gateway", "Other_Gateway",
             None, "", "null", "\\N", "غير محدد", "غير معروف", {"name": "not a scalar"}]
    await seed(db, names=names)
    first = await load_payment_method_setup(db, OWNER)
    assert first["complete"] is True
    assert len(first["items"]) == 5
    assert len({row["key"] for row in first["items"]}) == 5
    for row in first["items"]:
        assert row["key"] == sha256(normalize_name(row["name"]).encode()).hexdigest()
        assert len(row["key"]) == 64
    await db.unified_orders.delete_many({"user_id": OWNER})
    await db.unified_orders.insert_many([{"user_id": OWNER, "payment_method": name} for name in reversed(names)])
    assert await load_payment_method_setup(db, OWNER) == first


@pytest.mark.asyncio
async def test_aliases_use_calculator_precedence_without_claiming_the_alias_is_editable(db):
    methods = [terms("مدى", commission_percent=1), terms("بطاقة ائتمانية", commission_percent=2),
               terms("Visa", commission_percent=4), terms("Custom processor", commission_percent=6)]
    names = ["mada", "Visa", "GooglePay", "Custom processor checkout", "PayPal"]
    await seed(db, names, methods)
    result = await load_payment_method_setup(db, OWNER)
    indexed = {row["name"]: row for row in result["items"]}
    calculator = match_settings({"payment_methods": [{"name": name, "orders_count": 1, "total_sales": 100}
        for name in names], "shipping_companies": []}, methods, [])["payment_breakdown"]
    for row in calculator:
        actual = indexed[row["name"]]
        assert (actual["status"] == "configured") == row["matched"]
        if row["matched"]:
            assert actual["commission_percent"] == row["commission_percent"]
    assert indexed["Visa"]["editable"] is True
    assert indexed["PayPal"]["editable"] is True
    assert all(indexed[name]["editable"] is False for name in ("mada", "GooglePay", "Custom processor checkout"))
    assert result["complete"] is True and result["pending_count"] == 1


@pytest.mark.asyncio
async def test_partial_exact_terms_do_not_borrow_fallback_and_explicit_zero_is_configured(db):
    await seed(db, ["Visa", "PayPal", "Free gateway"], [
        terms("بطاقة ائتمانية"), terms("Visa", commission_percent=None),
        {"name": "PayPal", "fixed_fee": 0, "vat_percent": 0},
        terms("Free gateway", commission_percent=0, fixed_fee=0, vat_percent=0),
    ])
    result = await load_payment_method_setup(db, OWNER)
    assert result["complete"] is True and result["pending_count"] == 2
    rows = {row["name"]: row for row in result["items"]}
    for name in ("Visa", "PayPal"):
        assert rows[name]["status"] == "needs_commission"
        assert all(rows[name][key] is None for key in ("commission_percent", "fixed_fee", "vat_percent"))
    assert rows["Free gateway"]["status"] == "configured"
    assert rows["Free gateway"]["commission_percent"] == 0


def test_revision_tracks_actual_method_array_not_shipping_or_owner_metadata():
    config = {"payment_methods": [terms("PayPal"), terms("Other gateway")], "shipping_companies": ["first"]}
    original = payment_settings_revision(config)
    assert payment_settings_revision({**config, "shipping_companies": ["second"], "updated_at": "different"}) == original
    assert payment_settings_revision({**config, "payment_methods": list(reversed(config["payment_methods"]))}) != original
    altered = deepcopy(config)
    altered["payment_methods"][0]["merchant_note"] = "added"
    assert payment_settings_revision(altered) != original
    assert len({payment_settings_revision(value) for value in ({}, {"payment_methods": None}, {"payment_methods": []})}) == 3


def valid_payload(**overrides):
    return PaymentMethodCommissionIn(**{
        "name": "PayPal", "commission_percent": 3, "fixed_fee": 1, "vat_percent": 15,
        "expected_revision": "a" * 64, **overrides,
    })


@pytest.mark.parametrize("field", ["commission_percent", "fixed_fee", "vat_percent"])
@pytest.mark.parametrize("value", [None, "", True, float("nan"), float("inf"), -1])
def test_fee_contract_rejects_implicit_nonfinite_and_negative_values(field, value):
    with pytest.raises(ValidationError):
        valid_payload(**{field: value})


@pytest.mark.parametrize("overrides", [
    {"commission_percent": 101}, {"vat_percent": 101}, {"fixed_fee": "1.001"},
    {"name": "غير محدد"}, {"name": "null"}, {"name": "  "},
    {"expected_revision": "not-a-revision"}, {"provider": "paypal"},
])
def test_fee_contract_rejects_invalid_terms_name_revision_or_provider_claim(overrides):
    with pytest.raises(ValidationError):
        valid_payload(**overrides)


def test_all_three_fee_terms_are_required_and_explicit_zero_is_valid():
    for missing in ("commission_percent", "fixed_fee", "vat_percent"):
        values = {"name": "PayPal", "commission_percent": 0, "fixed_fee": 0,
                  "vat_percent": 0, "expected_revision": "a" * 64}
        values.pop(missing)
        with pytest.raises(ValidationError):
            PaymentMethodCommissionIn(**values)
    zero = valid_payload(commission_percent="0", fixed_fee="0.00", vat_percent="0")
    assert zero.commission_percent == zero.fixed_fee == zero.vat_percent == 0


@pytest.mark.asyncio
async def test_group_limit_is_after_raw_grouping_and_incomplete_is_explicit(db, monkeypatch):
    monkeypatch.setattr(setup, "MAX_OBSERVED_METHODS", 3)
    await seed(db, ["Gateway one"] * 10 + ["Gateway two", "Gateway three"])
    assert (await load_payment_method_setup(db, OWNER))["complete"] is True
    await db.unified_orders.insert_one({"user_id": OWNER, "payment_method": "Gateway four"})
    result = await load_payment_method_setup(db, OWNER)
    assert result["complete"] is False and len(result["items"]) == 3
    assert result["pending_count"] == 3
    assert all(not row["editable"] for row in result["items"])


@pytest.mark.asyncio
async def test_timeout_is_incomplete_and_cannot_authorize_a_save(db):
    config = await seed(db)

    class UnavailableOrders:
        def aggregate(self, *args, **kwargs):
            raise ExecutionTimeout("synthetic query timeout")

    class UnavailableDB:
        settings = db.settings
        unified_orders = UnavailableOrders()

    result = await load_payment_method_setup(UnavailableDB(), OWNER)
    assert result["complete"] is False
    with pytest.raises(HTTPException) as failed:
        await save_payment_method_commission(UnavailableDB(), OWNER, "actor",
            valid_payload(expected_revision=payment_settings_revision(config)))
    assert failed.value.status_code == 503
    assert (await db.settings.find_one({"user_id": OWNER}))["payment_methods"] == []


@pytest.mark.asyncio
async def test_foreign_settings_and_duplicate_owner_documents_never_supply_terms(db):
    await seed(db)
    wrong = await load_payment_method_setup(db, OWNER, settings={"user_id": "foreign", "payment_methods": [terms("PayPal")]})
    assert wrong["complete"] is False and wrong["pending_count"] == 1
    assert wrong["items"][0]["commission_percent"] is None and not wrong["items"][0]["editable"]

    duplicate_db = AsyncMongoMockClient().duplicate_synthetic_settings
    await duplicate_db.unified_orders.insert_one({"user_id": OWNER, "payment_method": "PayPal"})
    await duplicate_db.settings.insert_many([{"user_id": OWNER, "payment_methods": [terms("PayPal")]},
                                             {"user_id": OWNER, "payment_methods": []}])
    duplicate = await load_payment_method_setup(duplicate_db, OWNER)
    assert duplicate["complete"] is False
    with pytest.raises(HTTPException) as conflict:
        await save_payment_method_commission(duplicate_db, OWNER, "actor", valid_payload(expected_revision=duplicate["revision"]))
    assert conflict.value.detail["code"] == "payment_method_settings_owner_ambiguous"


@pytest.mark.asyncio
async def test_save_rejects_stale_revision_unobserved_method_and_inherited_alias(db):
    config = await seed(db, ["mada", "PayPal"], [terms("مدى")])
    foreign = {"user_id": "foreign", "payment_method": "Foreign processor"}
    await db.unified_orders.insert_one(foreign)
    before = await db.settings.find_one({"user_id": OWNER})
    for payload, code in [
        (valid_payload(), "payment_method_settings_revision_conflict"),
        (valid_payload(name="Foreign processor", expected_revision=payment_settings_revision(config)), "payment_method_not_observed"),
        (valid_payload(name="mada", expected_revision=payment_settings_revision(config)), "payment_method_settings_alias_conflict"),
    ]:
        with pytest.raises(HTTPException) as conflict:
            await save_payment_method_commission(db, OWNER, "actor", payload)
        assert conflict.value.detail["code"] == code
    assert await db.settings.find_one({"user_id": OWNER}) == before


@pytest.mark.asyncio
async def test_exact_edit_preserves_custom_metadata_and_other_method_terms(db):
    config = await seed(db, ["PayPal", "Other processor"], [terms("PayPal", merchant_note="retain"), terms("Other processor")])
    result = await save_payment_method_commission(db, OWNER, "actor", valid_payload(
        commission_percent=0, fixed_fee=0, vat_percent=0, expected_revision=payment_settings_revision(config)))
    stored = await db.settings.find_one({"user_id": OWNER})
    assert stored["payment_methods"] == [terms("PayPal", commission_percent=0, fixed_fee=0, vat_percent=0,
        merchant_note="retain"), terms("Other processor")]
    assert result["payment_method_setup"]["pending_count"] == 0


@pytest.mark.asyncio
async def test_duplicate_exact_settings_are_a_visible_conflict_without_choosing_a_rate(db):
    config = await seed(db, ["PayPal"], [terms("PayPal"), terms(" paypal ", commission_percent=99)])
    result = await load_payment_method_setup(db, OWNER)
    assert result["complete"] is False
    assert result["items"][0]["commission_percent"] is None
    assert not result["items"][0]["editable"]
    with pytest.raises(HTTPException) as conflict:
        await save_payment_method_commission(db, OWNER, "actor", valid_payload(expected_revision=payment_settings_revision(config)))
    assert conflict.value.detail["code"] == "payment_method_settings_alias_conflict"


@pytest.mark.asyncio
async def test_concurrent_saves_use_cas_and_keep_only_the_winning_terms(db):
    config = await seed(db, ["PayPal", "Other processor"])
    arrived = 0
    both_ready = asyncio.Event()

    class RacingSettings:
        def find(self, *args, **kwargs):
            return db.settings.find(*args, **kwargs)

        async def update_one(self, *args, **kwargs):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                both_ready.set()
            await asyncio.wait_for(both_ready.wait(), timeout=2)
            return await db.settings.update_one(*args, **kwargs)

    class RacingDB:
        settings = RacingSettings()
        unified_orders = db.unified_orders

    revision = payment_settings_revision(config)
    results = await asyncio.gather(*[
        save_payment_method_commission(RacingDB(), OWNER, "actor", valid_payload(name=name, expected_revision=revision))
        for name in ("PayPal", "Other processor")
    ], return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(failures) == 1 and failures[0].detail["code"] == "payment_method_settings_revision_conflict"
    assert len((await db.settings.find_one({"user_id": OWNER}))["payment_methods"]) == 1


@pytest.mark.asyncio
async def test_concurrent_unrelated_settings_edit_survives_the_narrow_update(db):
    config = await seed(db)

    class SettingsWithConcurrentShippingEdit:
        def find(self, *args, **kwargs):
            return db.settings.find(*args, **kwargs)

        async def update_one(self, *args, **kwargs):
            await db.settings.update_one({"user_id": OWNER}, {"$set": {"shipping_companies": ["new shipping preference"]}})
            return await db.settings.update_one(*args, **kwargs)

    class ConcurrentDB:
        settings = SettingsWithConcurrentShippingEdit()
        unified_orders = db.unified_orders

    await save_payment_method_commission(ConcurrentDB(), OWNER, "actor", valid_payload(expected_revision=payment_settings_revision(config)))
    assert (await db.settings.find_one({"user_id": OWNER}))["shipping_companies"] == ["new shipping preference"]


@pytest.mark.asyncio
async def test_first_settings_document_can_be_created_for_an_observed_method(db):
    await db.unified_orders.insert_one({"user_id": OWNER, "payment_method": "PayPal"})
    await save_payment_method_commission(db, OWNER, "actor", valid_payload(expected_revision=payment_settings_revision(None)))
    assert await db.settings.count_documents({"user_id": OWNER}) == 1
    assert (await db.settings.find_one({"user_id": OWNER}))["payment_methods"] == DEFAULT_PAYMENT_METHODS + [terms("PayPal")]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [None, {"user_id": OWNER, "shipping_companies": ["preserve existing shipping"]}])
async def test_absent_method_settings_use_reporting_defaults_without_writes_and_save_preserves_them(db, initial):
    if initial is not None:
        await db.settings.insert_one(deepcopy(initial))
    await db.unified_orders.insert_many([{"user_id": OWNER, "payment_method": name} for name in ("mada", "PayPal")])
    before = await load_payment_method_setup(ReadOnlyDB(db), OWNER)
    rows = {row["name"]: row for row in before["items"]}
    assert before["complete"] is True and before["pending_count"] == 1
    assert rows["mada"]["status"] == "configured" and rows["mada"]["commission_percent"] == 1
    assert rows["PayPal"]["status"] == "needs_commission"
    assert before["revision"] == payment_settings_revision(initial)
    persisted = await db.settings.find_one({"user_id": OWNER})
    assert persisted is None if initial is None else "payment_methods" not in persisted
    await save_payment_method_commission(db, OWNER, "actor", valid_payload(expected_revision=before["revision"]))
    saved = await db.settings.find_one({"user_id": OWNER})
    assert saved["payment_methods"] == DEFAULT_PAYMENT_METHODS + [terms("PayPal")]
    if initial is not None:
        assert saved["shipping_companies"] == initial["shipping_companies"]


@pytest.mark.asyncio
async def test_discovery_does_not_hide_programming_errors_as_empty_availability(db):
    await seed(db)

    class BrokenOrders:
        def aggregate(self, *args, **kwargs):
            raise AssertionError("synthetic programming error")

    class BrokenDB:
        settings = db.settings
        unified_orders = BrokenOrders()

    with pytest.raises(AssertionError, match="synthetic programming error"):
        await load_payment_method_setup(BrokenDB(), OWNER)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unreadable", "foreign"])
async def test_unknown_settings_never_supply_default_rates_even_for_known_rails(db, failure):
    await seed(db, ["mada", "Visa", "PayPal"], [terms("مدى", commission_percent=7)])

    class UnreadableSettings:
        def find(self, *args, **kwargs):
            raise ExecutionTimeout("synthetic settings unavailable")

    class UnreadableDB:
        settings = UnreadableSettings()
        unified_orders = db.unified_orders

    result = await load_payment_method_setup(UnreadableDB(), OWNER) if failure == "unreadable" else (
        await load_payment_method_setup(db, OWNER,
            settings={"user_id": "foreign", "payment_methods": DEFAULT_PAYMENT_METHODS}))
    assert result["complete"] is False and len(result["items"]) == 3
    for item in result["items"]:
        assert item["status"] == "needs_commission" and item["editable"] is False
        assert all(item[field] is None for field in ("commission_percent", "fixed_fee", "vat_percent"))


@pytest.mark.asyncio
async def test_successful_save_followed_by_unreadable_settings_returns_unknown_not_default_rates(db):
    config = await seed(db, ["mada", "PayPal"], [terms("مدى", commission_percent=7)])

    class SettingsFailAfterSave:
        saved = False

        def find(self, *args, **kwargs):
            if self.saved:
                raise ExecutionTimeout("synthetic post-save read unavailable")
            return db.settings.find(*args, **kwargs)

        async def update_one(self, *args, **kwargs):
            result = await db.settings.update_one(*args, **kwargs)
            self.saved = True
            return result

    class PostSaveFailureDB:
        settings = SettingsFailAfterSave()
        unified_orders = db.unified_orders

    result = (await save_payment_method_commission(PostSaveFailureDB(), OWNER, "actor",
        valid_payload(expected_revision=payment_settings_revision(config))))["payment_method_setup"]
    assert result["complete"] is False
    assert all(item["commission_percent"] is None and not item["editable"] for item in result["items"])
    assert (await db.settings.find_one({"user_id": OWNER}))["payment_methods"] == [terms("مدى", commission_percent=7), terms("PayPal")]
