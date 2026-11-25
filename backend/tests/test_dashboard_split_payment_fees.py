"""Fully paid Salla split tenders price only their external transactions.

Synthetic fixtures retain the two native shapes seen in the dashboard defect:
a card plus the store's customer wallet, and two distinct mada transactions.
"""
from copy import deepcopy

import pytest

from dashboard_payment_fees import (
    FEE_SOURCE_PROJECTION, attach_fee_source_evidence, dashboard_fee_breakdown, load_dashboard_fee_context,
)
from tests.test_dashboard_dated_payment_fees import policy


OWNER = "synthetic-owner"
DAY = "2026-11-24"


def fee_context(**changes):
    policies = []
    for rail, rate in (("mada", "1"), ("credit_card", "2.2")):
        policies.append(policy(id="synthetic-" + rail, provider="salla", payment_method=rail,
            event_type="capture", components=[{"id": "processing", "percentage": rate,
                "fixed_amount": "1.00", "vat_treatment": "exclusive", "vat_rate": "15",
                "refund_fee_treatment": "retain"}]))
    return {"owner_id": OWNER, "policies": policies, "source_mode": "mz2_policies",
            "complete": True, "statement_plans": {}, "as_of": DAY, **changes}


def split_order(kind="wallet"):
    rail, total, legs = (
        ("credit_card", 178.87, [("credit_card", "145.86"), ("customer_wallet", "33.01")])
        if kind == "wallet" else
        ("mada", 528.12, [("mada", "329.40"), ("mada", "198.72")])
    )
    number, oid = "synthetic-" + kind, "native-" + kind
    raw = {"id": oid, "reference_id": number, "currency": "SAR",
        "date": {"date": DAY + " 12:00:00.000000", "timezone": "Asia/Riyadh"},
        "updated_at": {"date": DAY + " 12:30:00.000000", "timezone": "Asia/Riyadh"},
        "amounts": {"total": {"amount": total, "currency": "SAR"}},
        "status": {"slug": "under_review", "name": "بإنتظار المراجعة"},
        "payment_method": rail,
        "payment_methods": [{"payment_method": method, "amount": value,
            "transaction_reference": kind + "-synthetic-ref-" + str(index),
            "provider": None if method == "customer_wallet" else "apple_pay"}
            for index, (method, value) in enumerate(legs)],
        "payment_actions": {
            "remaining_action": {"has_remaining_amount": False,
                "paid_amount": {"amount": total, "currency": "SAR"}, "remaining_amount": None},
            "refund_action": {"paid_amount": {"amount": total, "currency": "SAR"},
                "refund_amount": {"amount": 0, "currency": "SAR"}, "refunded_transactions": [],
                "pending_refund_amount": None, "has_refund_amount": False}},
    }
    return {"user_id": OWNER, "order_id": oid, "order_number": number, "order_date": DAY,
        "order_status": "completed", "payment_method": rail, "currency": "SAR", "total_amount": total,
        "shipping_company": "carrier", "products": [{"product_id": "synthetic-product", "quantity": 1}],
        "total_product_cost": 10,
        "g47_salla_snapshot": {"source_updated_at": DAY + "T12:30:00+03:00"},
        "raw_by_source": {"salla_direct": raw}}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected_net,expected_vat,expected_total", [
    ("wallet", 4.21, .63, 4.84),
    ("mada", 7.28, 1.09, 8.37),
])
async def test_verified_split_prices_external_transactions_without_changing_sale(kind, expected_net, expected_vat, expected_total):
    order, context = split_order(kind), fee_context()
    before = deepcopy((order, context))
    result = await dashboard_fee_breakdown([order], [], context)
    assert result["payment_fees_complete"] is True, result["fee_review_reasons"]
    assert result["total_payment_fees"] == expected_total
    row, = result["payment_breakdown"]
    assert (row["base_commission"], row["vat_amount"]) == (expected_net, expected_vat)
    assert row["total_sales"] == order["total_amount"] and row["orders_count"] == 1
    assert row["fee_sources"] == ["dated_provider_policy_split_tenders"]
    assert row["internal_wallet_paid_sar"] == result["internal_wallet_paid_sar"] == (33.01 if kind == "wallet" else 0)
    assert (order, context) == before


@pytest.mark.asyncio
async def test_identical_duplicate_reference_is_charged_once_and_wallet_alias_has_the_same_cash_basis():
    order = split_order()
    raw = order["raw_by_source"]["salla_direct"]
    raw["payment_methods"].append(deepcopy(raw["payment_methods"][0]))
    raw["payment_methods"][1]["payment_method"] = " Customer_Wallet "
    result = await dashboard_fee_breakdown([order], [], fee_context())
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 4.84
    assert result["internal_wallet_paid_sar"] == 33.01


def replace_path(document, path, value):
    keys = path.split(".")
    current = document
    for key in keys[:-1]:
        key = int(key) if isinstance(current, list) else key
        if isinstance(current, dict):
            current = current.setdefault(key, {})
        else:
            current = current[key]
    current[int(keys[-1]) if isinstance(current, list) else keys[-1]] = value


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [
    ("user_id", "foreign-owner"),
    ("order_id", "different-native-order"),
    ("g47_salla_snapshot.cancelled", True),
    ("g47_salla_snapshot.requires_authoritative_refresh", True),
    ("g47_salla_snapshot.source_updated_at", DAY + "T12:31:00+03:00"),
    ("actual_canceled_amount", 10),
    ("actual_refund_amount", 10),
    ("actual_partial_refund_amount", 10),
    ("actual_refund_amount", None),
    ("actual_refund_amount", {"amount": 0, "currency": "USD"}),
    ("raw_by_source.salla_direct.payment", ["malformed"]),
    ("raw_by_source.salla_direct.payment.cancelled", 10),
    ("raw_by_source.salla_direct.payment.cancelled", None),
    ("raw_by_source.salla_direct.payment.refunded", 10),
    ("raw_by_source.salla_direct.payment.refunded", {"amount": 0, "currency": "USD"}),
    ("raw_by_source.salla_direct.payment.refunds", [{"amount": 10}]),
    ("raw_by_source.salla_direct.refunds", [{"amount": 10}]),
    ("raw_by_source.salla_direct.cancellations", [{"amount": 10}]),
    ("raw_by_source.salla_direct.refunded_transactions", [{"amount": 10}]),
    ("raw_by_source.salla_direct.pending_refund_amount", 10),
    ("raw_by_source.salla_direct.cancelled", 10),
    ("raw_by_source.salla_direct.payment_actions.refund_action.refund_amount.amount", 10),
    ("raw_by_source.salla_direct.payment_actions", {}),
    ("raw_by_source.salla_direct.is_pending_payment", True),
    ("raw_by_source.salla_direct.status.slug", "refunded"),
    ("raw_by_source.salla_direct.payment_status", "failed"),
    ("raw_by_source.salla_direct.payment_methods.1.amount", "33.00"),
    ("raw_by_source.salla_direct.payment_methods.1.amount", "-33.01"),
    ("raw_by_source.salla_direct.payment_methods.1.currency", "USD"),
    ("raw_by_source.salla_direct.payment_methods.1.payment_method", "unknown-wallet"),
    ("raw_by_source.salla_direct.payment_methods.1.transaction_reference", None),
    ("raw_by_source.salla_direct.payment_methods.1.transaction_reference", "wallet-synthetic-ref-0"),
    ("raw_by_source.salla_direct.updated_at.date", DAY + " 11:30:00.000000"),
    ("raw_by_source.salla_direct.updated_at.date", "2026-11-25 12:30:00.000000"),
])
async def test_incomplete_conflicting_or_refunded_split_still_withholds_fees(path, value):
    order = split_order()
    replace_path(order, path, value)
    before = deepcopy(order)
    result = await dashboard_fee_breakdown([order], [], fee_context())
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"]
    assert order == before


@pytest.mark.asyncio
async def test_component_pending_accepts_only_referenced_current_payment_proof():
    order = split_order()
    order["g47_salla_snapshot"].update(component_pending=True, source_updated_at=DAY + "T12:01:00+03:00")
    result = await dashboard_fee_breakdown([order], [], fee_context())
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 4.84


@pytest.mark.asyncio
async def test_cross_order_reference_collision_is_not_two_independent_charges():
    first, second = split_order("mada"), split_order("wallet")
    second["raw_by_source"]["salla_direct"]["payment_methods"][0]["transaction_reference"] = "mada-synthetic-ref-0"
    result = await dashboard_fee_breakdown([first, second], [], fee_context())
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_payment_reference_conflict"]


@pytest.mark.asyncio
async def test_loader_binds_owner_and_keeps_full_cohort_collision_for_single_order_reader():
    first, second = split_order("mada"), split_order("wallet")
    second["raw_by_source"]["salla_direct"]["payment_methods"][0]["transaction_reference"] = "mada-synthetic-ref-0"
    context = await load_dashboard_fee_context(object(), OWNER, [first, second], as_of=DAY,
                                              fee_context=fee_context(owner_id="untrusted-owner"))
    assert context["owner_id"] == OWNER
    result = await dashboard_fee_breakdown([first], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_payment_reference_conflict"]


@pytest.mark.asyncio
async def test_undated_split_must_have_one_tariff_over_its_entire_possible_payment_interval():
    order, context = split_order("mada"), fee_context(as_of="2026-11-26")
    raw = order["raw_by_source"]["salla_direct"]
    raw["updated_at"]["date"] = "2026-11-26 12:30:00.000000"
    order["g47_salla_snapshot"]["source_updated_at"] = "2026-11-26T12:30:00+03:00"
    assert (await dashboard_fee_breakdown([order], [], context))["total_payment_fees"] == 8.37
    old = context["policies"][0]
    new = deepcopy(old)
    old["effective_to"] = DAY
    new.update(id="new-tariff", effective_from="2026-11-25")
    new["components"][0]["percentage"] = "10"
    context["policies"].append(new)
    result = await dashboard_fee_breakdown([order], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_fee_transaction_date_required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_wallet", [False, True])
async def test_actual_statement_supersedes_estimate_without_erasing_or_inventing_wallet_cash_basis(invalid_wallet):
    order = split_order()
    order.update(payment_fee_status="actual", actual_payment_fee=2, actual_payment_vat=.30)
    if invalid_wallet:
        order["raw_by_source"]["salla_direct"]["payment_methods"][1]["amount"] = "32.00"
    result = await dashboard_fee_breakdown([order], [], fee_context(policies=[]))
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 2.30
    assert result["payment_breakdown"][0]["fee_sources"] == ["actual_statement"]
    assert result["internal_wallet_paid_sar"] == (None if invalid_wallet else 33.01)


@pytest.mark.asyncio
async def test_projection_preserves_split_identity_and_amount_guards_without_customer_fields_or_writes():
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient().synthetic_split_projection
    source = split_order()
    raw = source["raw_by_source"]["salla_direct"]
    raw.update(customer={"name": "must-not-be-copied"}, notes="must-not-be-copied")
    raw["amounts"]["unneeded_private_field"] = "must-not-be-copied"
    await db.unified_orders.insert_one(deepcopy(source))
    before = await db.unified_orders.find({}, {"_id": 0}).to_list(10)
    projected = await db.unified_orders.find({"user_id": OWNER}, FEE_SOURCE_PROJECTION).to_list(10)
    order = {key: value for key, value in source.items() if key != "raw_by_source"}
    attach_fee_source_evidence([order], projected)
    assert "must-not-be-copied" not in str(order)
    result = await dashboard_fee_breakdown([order], [], fee_context())
    assert result["payment_fees_complete"] is True and result["total_payment_fees"] == 4.84
    assert result["internal_wallet_paid_sar"] == 33.01
    assert await db.unified_orders.find({}, {"_id": 0}).to_list(10) == before


@pytest.mark.asyncio
async def test_projected_split_retains_contradictory_amounts_currency_guard():
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient().synthetic_split_currency_projection
    source = split_order()
    source["raw_by_source"]["salla_direct"]["amounts"]["currency"] = "USD"
    direct = await dashboard_fee_breakdown([source], [], fee_context())
    assert direct["payment_fees_complete"] is False
    assert direct["fee_review_reasons"] == ["provider_payment_currency_conflict"]
    await db.unified_orders.insert_one(deepcopy(source))
    projected = await db.unified_orders.find({"user_id": OWNER}, FEE_SOURCE_PROJECTION).to_list(10)
    order = {key: value for key, value in source.items() if key != "raw_by_source"}
    attach_fee_source_evidence([order], projected)
    result = await dashboard_fee_breakdown([order], [], fee_context())
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == direct["fee_review_reasons"]
    assert result["internal_wallet_paid_sar"] is None


@pytest.mark.asyncio
async def test_full_cohort_conflict_lookup_work_scales_linearly_for_split_subsets():
    """Pricing a subset must not rehash the full owner's conflicts per tender."""
    class CountedReference(str):
        hash_calls = 0

        def __hash__(self):
            type(self).hash_calls += 1
            return super().__hash__()

    measured = []
    for count in (100, 200):
        orders = []
        for index in range(count):
            order = split_order()
            raw = order["raw_by_source"]["salla_direct"]
            order.update(order_number=f"selected-{index}", order_id=f"native-selected-{index}")
            raw.update(reference_id=order["order_number"], id=order["order_id"])
            for leg_index, leg in enumerate(raw["payment_methods"]):
                leg["transaction_reference"] = f"selected-{index}-{leg_index}"
            orders.append(order)
        # The context belongs to the full owner cohort; these conflicts belong
        # to other rows and must stay available without repricing those rows.
        context = fee_context(split_fee_reference_conflicts=[
            CountedReference(f"outside-subset-{index}") for index in range(count)])
        before = deepcopy(context)
        CountedReference.hash_calls = 0
        result = await dashboard_fee_breakdown(orders, [], context)
        measured.append(CountedReference.hash_calls)
        assert result["payment_fees_complete"] is True
        assert result["total_payment_fees"] == round(4.84 * count, 2)
        assert context == before
    # Count real reference hashing, not timing: double the population and
    # inherited evidence while allowing only a constant number of passes.
    assert measured[0] <= 4 * 100, measured
    assert measured[1] <= 2 * measured[0] + 10, measured


def test_direct_split_helpers_keep_list_context_conflicts_and_inputs_unchanged():
    from dashboard_split_payment_fees import estimate_split_fees, internal_wallet_paid_sar

    order = split_order()
    raw = order["raw_by_source"]["salla_direct"]
    reference = raw["payment_methods"][0]["transaction_reference"]
    context = fee_context(split_fee_reference_conflicts=[reference])
    before = deepcopy((order, context))
    assert internal_wallet_paid_sar(order, context) is None
    with pytest.raises(ValueError, match="^provider_payment_reference_conflict$"):
        estimate_split_fees(order, raw, context)
    assert (order, context) == before


@pytest.mark.asyncio
async def test_conflict_lookup_does_not_leak_between_breakdowns():
    order = split_order()
    context = fee_context(split_fee_reference_conflicts=[])
    assert (await dashboard_fee_breakdown([order], [], context))["payment_fees_complete"] is True
    reference = order["raw_by_source"]["salla_direct"]["payment_methods"][0]["transaction_reference"]
    context["split_fee_reference_conflicts"].append(reference)
    result = await dashboard_fee_breakdown([order], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_payment_reference_conflict"]
    context["split_fee_reference_conflicts"].clear()
    assert (await dashboard_fee_breakdown([order], [], context))["payment_fees_complete"] is True
