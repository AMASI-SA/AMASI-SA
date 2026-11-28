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


@pytest.mark.asyncio
async def test_incomplete_payment_fee_explains_exact_owner_order_and_preserves_known_subset():
    good, failed = split_order("mada"), split_order("wallet")
    failed["raw_by_source"]["salla_direct"]["payment_methods"][1]["amount"] = "31.00"
    failed["customer"] = {"name": "PRIVATE-CUSTOMER-MUST-NOT-ESCAPE"}
    failed["raw_by_source"]["salla_direct"]["payment_methods"][0]["transaction_reference"] = (
        "PRIVATE-TRANSACTION-MUST-NOT-ESCAPE")
    original = deepcopy([good, failed])
    result = await dashboard_fee_breakdown([good, failed], [], fee_context())
    assert result["payment_fees_complete"] is False
    assert result["total_payment_fees"] == 8.37  # Known subset, NOT final fees.
    assert result["fee_review_order_count"] == 1
    assert result["fee_review_rows_truncated"] == 0
    assert result["fee_review_orders"] == [{
        "order_number": "synthetic-wallet",
        "payment_method": "credit_card",
        "reason_code": "provider_payment_amount_conflict",
        "evidence_state": "review_required",
    }]
    assert "PRIVATE-CUSTOMER" not in str(result)
    assert "PRIVATE-TRANSACTION" not in str(result)
    assert [good, failed] == original


@pytest.mark.asyncio
async def test_fee_review_order_diagnostics_are_bounded_and_owner_scoped():
    sources = []
    for index in range(43):
        item = split_order("wallet")
        item["order_number"] = f"synthetic-{index}"
        item["order_id"] = f"native-{index}"
        raw = item["raw_by_source"]["salla_direct"]
        raw["reference_id"], raw["id"] = item["order_number"], item["order_id"]
        raw["payment_methods"][1]["amount"] = "31.00"
        for leg_index, leg in enumerate(raw["payment_methods"]):
            leg["transaction_reference"] = f"ref-{index}-{leg_index}"
        sources.append(item)
    foreign = deepcopy(sources[0])
    foreign["user_id"] = "different-tenant"
    foreign["order_number"] = "foreign-secret-order"
    result = await dashboard_fee_breakdown([*sources, foreign], [], fee_context())
    assert result["payment_fees_complete"] is False
    assert result["fee_review_order_count"] == 43
    assert len(result["fee_review_orders"]) == 40
    assert result["fee_review_rows_truncated"] == 3
    assert "foreign-secret-order" not in str(result)
    assert result["fee_review_orders"][0]["order_number"] == "synthetic-0"


def synthetic_bnpl_wallet(provider):
    source = split_order("wallet")
    method = provider + "_installment"
    source.update(payment_method=method, order_number="synthetic-" + provider,
                  order_id="native-" + provider)
    raw = source["raw_by_source"]["salla_direct"]
    raw.update(payment_method=method, reference_id=source["order_number"], id=source["order_id"])
    raw["payment_methods"][0].update(payment_method=method, provider=None)
    return source


def synthetic_bnpl_wallet_policy(provider):
    return policy(id="synthetic-" + provider, provider=provider, payment_method=None,
        event_type="capture", components=[{"id": "processing", "percentage": "0",
            "fixed_amount": "2.00", "vat_treatment": "exclusive", "vat_rate": "15",
            "refund_fee_treatment": "retain"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tabby", "tamara", "emkan"])
async def test_verified_bnpl_plus_wallet_prices_only_external_provider_share(provider):
    source = synthetic_bnpl_wallet(provider)
    context = fee_context(policies=[synthetic_bnpl_wallet_policy(provider)])
    before = deepcopy((source, context))
    result = await dashboard_fee_breakdown([source], [], context)
    assert result["payment_fees_complete"] is True, result["fee_review_reasons"]
    assert result["total_payment_fees"] == 2.30
    assert result["internal_wallet_paid_sar"] == 33.01
    row, = result["payment_breakdown"]
    assert (row["base_commission"], row["vat_amount"]) == (2.0, 0.3)
    assert row["total_sales"] == source["total_amount"] == 178.87
    assert row["fee_sources"] == ["dated_provider_policy_split_tenders"]
    assert result["fee_review_order_count"] == 0
    assert (source, context) == before


@pytest.mark.asyncio
async def test_bnpl_wallet_unproved_plan_or_tariff_change_remains_incomplete():
    source = synthetic_bnpl_wallet("tabby")
    base = synthetic_bnpl_wallet_policy("tabby")
    context = fee_context(policies=[{**base, "plan_id": "plan-a"}])
    result = await dashboard_fee_breakdown([source], [], context)
    assert not result["payment_fees_complete"]
    assert result["fee_review_reasons"] == ["provider_payment_plan_required"]
    assert result["fee_review_orders"][0]["order_number"] == source["order_number"]

    source["raw_by_source"]["salla_direct"]["updated_at"]["date"] = (
        "2026-11-26 12:30:00.000000")
    source["g47_salla_snapshot"]["source_updated_at"] = "2026-11-26T12:30:00+03:00"
    old = {**base, "effective_to": DAY}
    new = deepcopy(base)
    new.update(id="synthetic-tabby-new", effective_from="2026-11-25")
    new["components"][0]["fixed_amount"] = "4.00"
    result = await dashboard_fee_breakdown([source], [], fee_context(
        policies=[old, new], as_of="2026-11-26"))
    assert not result["payment_fees_complete"]
    assert result["fee_review_reasons"] == ["provider_fee_transaction_date_required"]


@pytest.mark.asyncio
async def test_repeated_order_page_paid_at_is_not_independent_historical_tariff_proof():
    order, context = split_order("mada"), fee_context(as_of="2026-11-26")
    raw = order["raw_by_source"]["salla_direct"]
    raw["updated_at"]["date"] = "2026-11-26 12:30:00.000000"
    order["g47_salla_snapshot"]["source_updated_at"] = "2026-11-26T12:30:00+03:00"
    old = context["policies"][0]
    newer = deepcopy(old)
    old["effective_to"] = DAY
    newer.update(id="different-future-fee", effective_from="2026-11-25")
    newer["components"][0]["percentage"] = "10"
    context["policies"].append(newer)

    # A single payment.paid_at is not independent capture proof.
    paid_at = DAY + " 12:15:00.000000"
    raw["payment"] = {"paid_at": {"date": paid_at, "timezone": "Asia/Riyadh"}}
    uncorroborated = await dashboard_fee_breakdown([order], [], context)
    assert uncorroborated["payment_fees_complete"] is False
    assert uncorroborated["fee_review_reasons"] == ["provider_fee_transaction_date_required"]

    # A repeated date from the same mutable order page has the same provenance.
    # It must NOT resolve a historical tariff change or fabricate a fee.
    for leg in raw["payment_methods"]:
        leg["paid_at"] = {"date": paid_at, "timezone": "Asia/Riyadh"}
    original = deepcopy((order, context))
    result = await dashboard_fee_breakdown([order], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_fee_transaction_date_required"]
    assert (order, context) == original


@pytest.mark.asyncio
async def test_conflicting_native_tender_capture_dates_never_select_a_cheaper_tariff():
    order, context = split_order("mada"), fee_context(as_of="2026-11-26")
    raw = order["raw_by_source"]["salla_direct"]
    raw["updated_at"]["date"] = "2026-11-26 12:30:00.000000"
    order["g47_salla_snapshot"]["source_updated_at"] = "2026-11-26T12:30:00+03:00"
    raw["payment"] = {"paid_at": {"date": DAY + " 12:15:00.000000", "timezone": "Asia/Riyadh"}}
    for leg in raw["payment_methods"]:
        leg["paid_at"] = {"date": DAY + " 12:15:00.000000", "timezone": "Asia/Riyadh"}
    raw["payment_methods"][1]["paid_at"]["date"] = "2026-11-25 12:15:00.000000"
    result = await dashboard_fee_breakdown([order], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_capture_timestamp_conflict"]


def bound_native_salla_captures(order, paid_at="2026-11-24T12:15:00+03:00"):
    raw = order["raw_by_source"]["salla_direct"]
    records = []
    for index, method in enumerate(raw["payment_methods"]):
        if method["payment_method"] == "customer_wallet":
            continue
        transaction = f"native-transaction-{index}"
        records.append({
            "user_id": order["user_id"], "source": "salla_transactions",
            "order_number": order["order_number"], "provider_order_id": order["order_id"],
            "transaction_id": transaction, "native_transaction_evidence": [{
                "schema": "salla_transaction_evidence_v1",
                "transaction_id": transaction,
                "provider_order_id": order["order_id"],
                "reference": method["transaction_reference"],
                "provider": "salla", "currency": "SAR",
                "amount": method["amount"], "status": "captured", "paid_at": paid_at,
            }],
        })
    return records


def historical_split_context():
    context = fee_context(as_of="2026-11-26")
    previous = context["policies"][0]
    revised = deepcopy(previous)
    previous["effective_to"] = DAY
    revised.update(id="revised-historical-tariff", effective_from="2026-11-25")
    revised["components"][0]["percentage"] = "10"
    context["policies"].append(revised)
    return context


def late_updated_mada_order():
    order = split_order("mada")
    order["raw_by_source"]["salla_direct"]["updated_at"]["date"] = "2026-11-26 12:30:00.000000"
    order["g47_salla_snapshot"]["source_updated_at"] = "2026-11-26T12:30:00+03:00"
    return order


@pytest.mark.asyncio
async def test_independent_bound_capture_evidence_resolves_old_tariff_without_using_shipping_update():
    order, context = late_updated_mada_order(), historical_split_context()
    unknown = await dashboard_fee_breakdown([order], [], context)
    assert unknown["payment_fees_complete"] is False
    assert unknown["fee_review_reasons"] == ["provider_fee_transaction_date_required"]

    context["split_fee_transaction_rows"] = {
        order["order_number"]: bound_native_salla_captures(order)}
    before = deepcopy((order, context))
    resolved = await dashboard_fee_breakdown([order], [], context)
    assert resolved["payment_fees_complete"] is True, resolved["fee_review_reasons"]
    assert resolved["total_payment_fees"] == 8.37
    assert resolved["payment_breakdown"][0]["fee_sources"] == [
        "native_capture_dated_provider_policy_split_tenders"]
    assert (order, context) == before


@pytest.mark.asyncio
async def test_partial_or_unbound_native_capture_cannot_resolve_historical_fee():
    source = late_updated_mada_order()
    rows = bound_native_salla_captures(source)
    for sample in (rows[:1], [{**rows[0], "native_transaction_evidence": [
            {**rows[0]["native_transaction_evidence"][0], "provider_order_id": "different-order"}]},
            rows[1]]):
        context = historical_split_context()
        context["split_fee_transaction_rows"] = {source["order_number"]: sample}
        result = await dashboard_fee_breakdown([source], [], context)
        assert result["payment_fees_complete"] is False
        assert result["fee_review_reasons"]


@pytest.mark.asyncio
async def test_fee_loader_reads_native_evidence_only_for_a_bounded_historical_tariff_change(monkeypatch):
    import operational_provider_transaction_evidence as transaction_reader
    source = late_updated_mada_order()
    rows = bound_native_salla_captures(source)
    calls = []

    async def fake_load(db, owner, raw_sources, start, now, *, to_instant, max_rows):
        calls.append((owner, len(raw_sources), max_rows, to_instant))
        assert owner == OWNER
        assert raw_sources[0]["id"] == source["order_id"]
        return {source["order_number"]: rows}

    monkeypatch.setattr(transaction_reader, "load_transaction_rows", fake_load)
    context = await load_dashboard_fee_context(object(), OWNER, [source],
        as_of="2026-11-26", fee_context=historical_split_context())
    result = await dashboard_fee_breakdown([source], [], context)
    assert calls and calls[0][:3] == (OWNER, 1, 800)
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 8.37

    # Normal fee windows avoid the extra transaction-table query entirely.
    calls.clear()
    normal = split_order("mada")
    await load_dashboard_fee_context(object(), OWNER, [normal], as_of=DAY, fee_context=fee_context())
    assert calls == []


@pytest.mark.asyncio
async def test_duplicate_native_capture_observation_is_priced_once_and_is_replay_stable():
    source = late_updated_mada_order()
    context = historical_split_context()
    rows = bound_native_salla_captures(source)
    context["split_fee_transaction_rows"] = {source["order_number"]: [
        rows[0], deepcopy(rows[0]), rows[1]]}
    original = deepcopy((source, context))
    first = await dashboard_fee_breakdown([source], [], context)
    second = await dashboard_fee_breakdown([source], [], context)
    assert first == second
    assert first["payment_fees_complete"] is True
    assert first["total_payment_fees"] == 8.37
    assert (source, context) == original


@pytest.mark.asyncio
async def test_malformed_native_evidence_mapping_is_not_a_fee_or_a_dashboard_exception():
    source = late_updated_mada_order()
    context = historical_split_context()
    context["split_fee_transaction_rows"] = [bound_native_salla_captures(source)]
    result = await dashboard_fee_breakdown([source], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_transaction_history_conflict"]


@pytest.mark.asyncio
async def test_failed_native_evidence_read_preserves_historical_tariff_unknown(monkeypatch):
    import operational_provider_transaction_evidence as transaction_reader
    source = late_updated_mada_order()

    async def fails_safely(*args, **kwargs):
        raise TimeoutError("source temporarily unavailable")

    monkeypatch.setattr(transaction_reader, "load_transaction_rows", fails_safely)
    context = await load_dashboard_fee_context(object(), OWNER, [source],
        as_of="2026-11-26", fee_context=historical_split_context())
    assert "split_fee_transaction_rows" not in context
    result = await dashboard_fee_breakdown([source], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_fee_transaction_date_required"]


@pytest.mark.asyncio
async def test_older_report_date_can_use_later_known_source_only_for_proven_earlier_capture():
    source = late_updated_mada_order()
    context = historical_split_context()
    context.update(as_of=DAY, evidence_as_of="2026-11-26")
    # A later shipping update is not by itself proof of an old payment.
    unknown = await dashboard_fee_breakdown([source], [], context)
    assert unknown["payment_fees_complete"] is False
    assert unknown["total_payment_fees"] == 0

    context["split_fee_transaction_rows"] = {
        source["order_number"]: bound_native_salla_captures(source)}
    original = deepcopy((source, context))
    resolved = await dashboard_fee_breakdown([source], [], context)
    assert resolved["payment_fees_complete"] is True, resolved["fee_review_reasons"]
    assert resolved["total_payment_fees"] == 8.37
    assert (source, context) == original


@pytest.mark.asyncio
async def test_future_dated_native_capture_never_enters_an_older_report_day():
    source = late_updated_mada_order()
    context = historical_split_context()
    context.update(as_of=DAY, evidence_as_of="2026-11-26")
    context["split_fee_transaction_rows"] = {
        source["order_number"]: bound_native_salla_captures(
            source, paid_at="2026-11-25T12:15:00+03:00")}
    result = await dashboard_fee_breakdown([source], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_capture_timestamp_conflict"]


@pytest.mark.asyncio
async def test_historical_report_loader_reuses_later_source_proof_without_moving_capture_day(monkeypatch):
    from datetime import timezone, datetime
    import operational_provider_transaction_evidence as transaction_reader

    source = late_updated_mada_order()
    captured = bound_native_salla_captures(source)
    requested = []

    async def fake_load(db, owner, raw_sources, start, now, *, to_instant, max_rows):
        requested.append((owner, now, max_rows))
        assert owner == OWNER and max_rows == 800
        assert now > datetime(2026, 11, 26, tzinfo=timezone.utc)
        return {source["order_number"]: captured}

    monkeypatch.setattr(transaction_reader, "load_transaction_rows", fake_load)
    context = await load_dashboard_fee_context(object(), OWNER, [source],
        as_of=DAY, fee_context=historical_split_context())
    result = await dashboard_fee_breakdown([source], [], context)
    assert len(requested) == 1
    assert context.get("split_fee_source_as_of") == requested[0][1]
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 8.37


@pytest.mark.asyncio
async def test_historical_loader_rejects_evidence_accepted_after_report_date(monkeypatch):
    import operational_provider_transaction_evidence as transaction_reader
    source = late_updated_mada_order()
    future = bound_native_salla_captures(source, paid_at="2026-11-25T12:15:00+03:00")

    async def fake_load(db, owner, raw_sources, start, now, *, to_instant, max_rows):
        return {source["order_number"]: future}

    monkeypatch.setattr(transaction_reader, "load_transaction_rows", fake_load)
    context = await load_dashboard_fee_context(object(), OWNER, [source],
        as_of=DAY, fee_context=historical_split_context())
    result = await dashboard_fee_breakdown([source], [], context)
    assert result["payment_fees_complete"] is False
    assert "provider_capture_timestamp_conflict" in result["fee_review_reasons"]


@pytest.mark.asyncio
async def test_accepted_actual_fee_supersedes_native_historical_tariff_without_double_counting():
    order, context = late_updated_mada_order(), historical_split_context()
    context["split_fee_transaction_rows"] = {
        order["order_number"]: bound_native_salla_captures(order)}
    assert (await dashboard_fee_breakdown([order], [], context))["total_payment_fees"] == 8.37

    order.update(payment_fee_status="actual", actual_payment_fee=3.5, actual_payment_vat=0.52)
    before = deepcopy((order, context))
    result = await dashboard_fee_breakdown([order], [], context)
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 4.02
    assert result["payment_breakdown"][0]["fee_sources"] == ["actual_statement"]
    assert (order, context) == before


@pytest.mark.asyncio
async def test_another_merchant_native_capture_never_proves_this_owners_old_tariff():
    order, context = late_updated_mada_order(), historical_split_context()
    foreign = bound_native_salla_captures(order)
    for row in foreign:
        row["user_id"] = "other-merchant"
    context["split_fee_transaction_rows"] = {order["order_number"]: foreign}
    result = await dashboard_fee_breakdown([order], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_fee_transaction_date_required"]



def accepted_wallet_provider_fee(source, provider):
    raw = source["raw_by_source"]["salla_direct"]
    external = next(row for row in raw["payment_methods"]
                    if row["payment_method"] != "customer_wallet")
    return {"available": True, "provider": provider, "native_currency": "SAR",
        "tender_scope": "single_provider_wallet_split",
        "principal_amount": external["amount"], "wallet_principal_amount": "33.01",
        "refunded_amount": "0.00", "fee_net": "2.00", "fee_vat": "0.30",
        "fee_total": "2.30", "capture_references": [external["transaction_reference"]],
        "merchant_account": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tabby", "tamara"])
async def test_frozen_actual_wallet_split_fee_replaces_estimate_exactly_once(provider):
    source = synthetic_bnpl_wallet(provider)
    context = fee_context(policies=[synthetic_bnpl_wallet_policy(provider)],
        operational_statement_fees={source["order_number"]:
            accepted_wallet_provider_fee(source, provider)})
    original = deepcopy((source, context))
    first = await dashboard_fee_breakdown([source], [], context)
    repeated = await dashboard_fee_breakdown([source], [], context)
    assert first == repeated
    assert first["payment_fees_complete"] is True, first["fee_review_reasons"]
    assert first["total_payment_fees"] == 2.30
    assert first["internal_wallet_paid_sar"] == 33.01
    assert first["payment_breakdown"][0]["fee_sources"] == ["actual_statement"]
    assert (source, context) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["wrong_reference", "wrong_principal", "wrong_wallet",
                                      "changed_wallet_leg", "unknown_wallet", "refunded"])
async def test_accepted_wallet_split_must_still_match_current_salla_order(problem):
    source = synthetic_bnpl_wallet("tabby")
    accepted = accepted_wallet_provider_fee(source, "tabby")
    if problem == "wrong_reference":
        accepted["capture_references"] = ["not-the-native-reference"]
    elif problem == "wrong_principal":
        accepted["principal_amount"] = "178.87"
    elif problem == "wrong_wallet":
        accepted["wallet_principal_amount"] = "0.00"
    elif problem == "changed_wallet_leg":
        source["raw_by_source"]["salla_direct"]["payment_methods"][1]["amount"] = "31.00"
    elif problem == "unknown_wallet":
        source["raw_by_source"]["salla_direct"]["payment_methods"][1]["amount"] = "unverified"
    else:
        accepted["refunded_amount"] = "1.00"
    context = fee_context(policies=[], operational_statement_fees={
        source["order_number"]: accepted})
    result = await dashboard_fee_breakdown([source], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"]


@pytest.mark.asyncio
async def test_actual_wallet_split_rejects_reference_reuse_across_two_owner_orders():
    source = synthetic_bnpl_wallet("tabby")
    other = synthetic_bnpl_wallet("tabby")
    other.update(order_number="second-tabby", order_id="second-native")
    raw = other["raw_by_source"]["salla_direct"]
    raw["reference_id"], raw["id"] = other["order_number"], other["order_id"]
    raw["payment_methods"][1]["transaction_reference"] = "second-wallet-ref"
    ctx = fee_context(policies=[], operational_statement_fees={
        source["order_number"]: accepted_wallet_provider_fee(source, "tabby"),
        other["order_number"]: accepted_wallet_provider_fee(other, "tabby")})
    result = await dashboard_fee_breakdown([source, other], [], ctx)
    assert result["payment_fees_complete"] is False
    assert "provider_payment_reference_conflict" in result["fee_review_reasons"]



@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tamara"])
async def test_tamara_emkan_loader_uses_verified_operational_fee_index_once(monkeypatch, provider):
    import dashboard_payment_fees as reader

    source = synthetic_bnpl_wallet(provider)
    accepted = accepted_wallet_provider_fee(source, provider)
    calls = []

    async def accepted_index(db, owner, numbers, *, source_state=None, accepted_as_of=None):
        calls.append((owner, tuple(numbers), accepted_as_of))
        assert owner == OWNER
        assert tuple(numbers) == (source["order_number"],)
        return {source["order_number"]: accepted}

    monkeypatch.setattr(reader, "load_operational_statement_fee_index", accepted_index)
    ctx = await load_dashboard_fee_context(object(), OWNER, [source], as_of=DAY,
        fee_context=fee_context(policies=[synthetic_bnpl_wallet_policy(provider)]))
    assert len(calls) == 1
    assert ctx["operational_statement_fees"][source["order_number"]] == accepted
    result = await dashboard_fee_breakdown([source], [], ctx)
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 2.30
    assert result["payment_breakdown"][0]["fee_sources"] == ["actual_statement"]


@pytest.mark.asyncio
async def test_unavailable_operational_statement_never_falls_back_to_a_plausible_tariff(monkeypatch):
    import dashboard_payment_fees as reader

    source = synthetic_bnpl_wallet("tamara")

    async def accepted_index(*args, **kwargs):
        return {source["order_number"]: {
            "available": False, "reason_code": "provider_statement_fee_evidence_changed",
            "plan_id": None}}

    monkeypatch.setattr(reader, "load_operational_statement_fee_index", accepted_index)
    ctx = await load_dashboard_fee_context(object(), OWNER, [source], as_of=DAY,
        fee_context=fee_context(policies=[synthetic_bnpl_wallet_policy("tamara")]))
    result = await dashboard_fee_breakdown([source], [], ctx)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_statement_fee_evidence_changed"]



@pytest.mark.asyncio
async def test_tamara_emkan_combined_statement_index_reads_snapshot_once(monkeypatch):
    import dashboard_payment_fees as reader

    sources = [synthetic_bnpl_wallet(provider) for provider in ("tamara", "emkan")]
    for source in sources:
        provider = source["payment_method"].split("_")[0]
        for index, leg in enumerate(source["raw_by_source"]["salla_direct"]["payment_methods"]):
            leg["transaction_reference"] = f"{provider}-unique-{index}"
    accepted = {source["order_number"]:
        accepted_wallet_provider_fee(source, source["payment_method"].split("_")[0])
        for source in sources}
    calls = []

    async def verified_once(db, owner, numbers, *, source_state=None, accepted_as_of=None):
        calls.append(tuple(numbers))
        assert owner == OWNER
        return deepcopy(accepted)

    monkeypatch.setattr(reader, "load_operational_statement_fee_index", verified_once)
    ctx = await load_dashboard_fee_context(object(), OWNER, sources, as_of=DAY,
        fee_context=fee_context(policies=[
            synthetic_bnpl_wallet_policy("tamara"),
            synthetic_bnpl_wallet_policy("emkan")]))
    assert calls == [tuple(sorted(accepted))]
    result = await dashboard_fee_breakdown(sources, [], ctx)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_statement_fee_source_unsupported"]
    assert result["total_payment_fees"] == 2.30  # Tamara only; Emkan is unknown.
    assert result["internal_wallet_paid_sar"] == 66.02



@pytest.mark.asyncio
async def test_unverified_emkan_operational_statement_cannot_be_accepted_as_actual():
    source = synthetic_bnpl_wallet("emkan")
    ctx = fee_context(policies=[], operational_statement_fees={
        source["order_number"]: accepted_wallet_provider_fee(source, "emkan")})
    result = await dashboard_fee_breakdown([source], [], ctx)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_statement_fee_source_unsupported"]
