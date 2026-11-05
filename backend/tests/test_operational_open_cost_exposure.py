"""Historical open-work reporting is a read-only supplement to operational finance."""
from copy import deepcopy
from decimal import Decimal
import pytest
from test_operational_balance_sources import DB, row, run
from operational_open_cost_exposure import project

START = "2026-11-01T00:00:00+00:00"
NOW = "2026-11-02T00:00:00+00:00"
BEFORE = "2026-10-30T10:00:00+00:00"


def native_order(number="OLD", created=BEFORE, status="in_progress", qty=1, shipping=None):
    raw = {"id": "salla-" + number, "reference_id": number, "created_at": created,
           "status": status, "currency": "SAR",
           "amounts": {"total": {"amount": "100.00", "currency": "SAR"}},
           "items": [{"id": "line", "product_id": "p1", "quantity": qty}]}
    if shipping:
        raw["shipping"] = shipping
    return row(order_number=number, raw_by_source={"salla_direct": raw})


def piece(number="OLD", status="sent", received=False):
    details = dict(id="physical1", order_number=number, order_item_id="salla:" + number + ":line",
                   unit_index=1, supplier_id="supplier1", supplier_dispatch_status=status,
                   execution_status="assigned")
    if received:
        details.update(supplier_dispatch_status="received", execution_status="received_from_supplier",
                       received_at="2026-11-01T09:00:00+00:00")
    return row(**details)


def shipping_setup():
    return row(couriers=[{"courier_key": "imile", "name": "إيمايل",
                          "confirmed_by": "owner", "confirmed_at": BEFORE,
                          "salla_carrier_keys": ["native"]}],
               contracts=[{"id": "contract", "status": "approved", "party_type": "courier",
                           "party_id": "imile", "context": "delivery", "effective_from": BEFORE,
                           "confirmed_by": "owner", "confirmed_at": BEFORE,
                           "delivery_fee": "17.25", "vat_included": True,
                           "cod_fixed_fee": "0.00", "cod_percent": "0.00"}])


def seeded_db(order_row=None, physical=None, invoices=None, setup=None):
    return DB({"unified_orders": [order_row or native_order()],
        "mezan_product_cost_profiles_v2": [row(salla_product_id="p1", base_cost="40.00")],
        "mezan_suppliers_v2": [row(id="supplier1", name="مورد معروف")],
        "mezan_preparation_pieces_v1": [physical or piece()],
        "mezan_supplier_invoices_v2": invoices or [], "mz2_shipping_setup_v2": setup or []})


def operating_state(obligations=None):
    return {"status": "active", "started_at": START,
            "engine": {"as_of": NOW, "obligations": obligations or {}}}


def group(result, period="prestart", kind="supplier"):
    return [g for g in result["groups"] if g["period"] == period and g["kind"] == kind]


def preview(db, state=None):
    return run(project(db, "owner", state or operating_state(), as_of=NOW))


def codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_old_assigned_unreceived_is_pooled_not_in_supplier_balance_and_is_not_zero():
    db, state = seeded_db(), operating_state()
    before_db, before_state = deepcopy(db.data), deepcopy(state)
    first, second = preview(db, state), preview(db, state)
    assert first == second
    assert first["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"
    supplier = group(first)
    assert len(supplier) == 1
    assert supplier[0]["party_name"] == "منتجات لم تُستلم من المورد"
    assert supplier[0]["party_id"] == ""
    assert supplier[0]["amount"] == "40.00"
    assert supplier[0]["stage"] == "under_fulfillment" and supplier[0]["units"] == 1
    assert db.data == before_db and state == before_state
    assert first["warning"] == "historical_preview_never_add_to_confirmed_or_expected_payable_without_owner_evidence"


def test_old_waiting_review_is_visible_and_remains_unreceived_pool():
    for status in ("waiting_review", "under_review", "بانتظار المراجعة"):
        result = preview(seeded_db(order_row=native_order(status=status)))
        assert group(result)[0]["stage"] == "awaiting_review"
        assert group(result)[0]["amount"] == "40.00"


def test_only_durable_supplier_receipt_moves_old_cost_to_supplier_bucket():
    db = seeded_db()
    actual = db.data["mezan_preparation_pieces_v1"][0]
    actual.update(status="received", received_at="2026-11-01T09:00:00+00:00")
    assert group(preview(db))[0]["party_name"] == "منتجات لم تُستلم من المورد"
    actual.update(supplier_dispatch_status="received", execution_status="received_from_supplier")
    after = preview(db)
    assert after["currencies"]["SAR"]["prestart_supplier_received_uninvoiced"] == "40.00"
    assert group(after)[0]["party_name"] == "مورد معروف"
    assert group(after)[0]["stage"] == "received_without_approved_invoice"
    assert after["currencies"]["SAR"].get("prestart_supplier_unreceived", "0.00") == "0.00"


def test_prestart_invoiced_piece_not_added_as_second_estimated_debt():
    invoice = row(id="invoice1", supplier_id="supplier1", approved_at="2026-11-01T11:00:00+00:00",
        lines=[{"piece_ids": ["physical1"], "product_price_authority": "mezan_v2", "product_unit_price_halalas": 4000}])
    result = preview(seeded_db(physical=piece(received=True), invoices=[invoice]))
    assert group(result) == []
    assert "prestart_order_postcutover_invoice_needs_reconciliation" in codes(result)


def test_old_invoice_with_invalid_tax_evidence_does_not_create_second_estimated_debt():
    invoice = row(id="invoice-bad", supplier_id="supplier1", approved_at="2026-11-01T11:00:00+00:00",
        purchase_tax={"treatment": "INPUT_VAT", "amount_halalas": 600},
        lines=[{"piece_ids": ["physical1"], "product_price_authority": "mezan_v2", "product_unit_price_halalas": 4000}])
    result = preview(seeded_db(physical=piece(received=True), invoices=[invoice]))
    assert group(result) == []
    assert "supplier_invoice_tax_or_amount_incomplete" in codes(result)
    assert "historical_supplier_component_ambiguous" in codes(result)


def test_missing_historical_product_cost_never_silent_zero():
    db = seeded_db()
    db.data["mezan_product_cost_profiles_v2"] = []
    result = preview(db)
    assert group(result) == []
    assert "historical_supplier_cost_incomplete" in codes(result)
    assert result["complete"] is False


def test_cancelled_or_completed_historical_orders_excluded_from_open_work():
    for status in ("delivered", "cancelled", "refunded"):
        assert group(preview(seeded_db(order_row=native_order(status=status)))) == []


def test_current_unreceived_and_historical_unreceived_are_visible_but_not_double_counted():
    obligations = {
        "supplier:new:item": {"id": "supplier:new:item", "kind": "supplier", "party_id": None,
            "party_type": "supplier", "currency": "SAR", "expected": "80.00", "confirmed": "0.00"},
        "shipping:new": {"id": "shipping:new", "kind": "shipping", "party_id": "courier1",
            "party_type": "courier", "currency": "SAR", "name": "شركة شحن", "expected": "17.25", "confirmed": "0.00"}}
    db = seeded_db()
    db.data["unified_orders"].append(native_order(number="NEW", created="2026-11-01T07:00:00+00:00"))
    result = preview(db, operating_state(obligations))
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"
    assert result["currencies"]["SAR"]["poststart_supplier_unreceived"] == "80.00"
    assert result["currencies"]["SAR"]["current_shipping_expected"] == "17.25"
    assert all(g["party_name"] == "منتجات لم تُستلم من المورد"
               for g in group(result) + group(result, period="poststart"))
    assert result["currencies"]["SAR"]["current_supplier_expected"] == "80.00"


def test_prestart_shipping_quote_is_independent_of_delivery_and_supplier_cost():
    result = preview(seeded_db(order_row=native_order(shipping={"company_code": "native"}), setup=[shipping_setup()]))
    assert result["currencies"]["SAR"]["prestart_shipping_expected"] == "17.25"
    shipping = group(result, kind="courier")
    assert len(shipping) == 1
    assert shipping[0]["stage"] == "not_delivered"
    assert shipping[0]["amount"] == "17.25" and shipping[0]["party_name"] == "إيمايل"
    assert group(result)[0]["amount"] == "40.00"


def test_external_delivery_evidence_excludes_historical_shipping_quote_but_not_unreceived_product():
    old = native_order(shipping={"company_code": "native", "delivered_at": "2026-10-31T20:00:00+00:00"})
    result = preview(seeded_db(order_row=old, setup=[shipping_setup()]))
    assert group(result)[0]["amount"] == "40.00"
    assert not [g for g in result["groups"] if g["kind"] in {"courier", "store_driver"}]
    assert result["currencies"]["SAR"].get("prestart_shipping_expected", "0.00") == "0.00"


def test_historical_missing_cost_can_preview_explicit_current_salla_catalog_cost_with_warning():
    db = seeded_db()
    db.data["mezan_product_cost_profiles_v2"] = []
    db.data["mezan_products_v2"] = [row(salla_product_id="p1", cost_price_from_salla="33.50")]
    result = preview(db)
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "33.50"
    assert group(result)[0]["party_name"] == "منتجات لم تُستلم من المورد"
    assert "historical_current_catalog_estimate_used" in codes(result)


def test_historical_cost_missing_from_both_sources_stays_incomplete_not_zero():
    db = seeded_db()
    db.data["mezan_product_cost_profiles_v2"] = []
    db.data["mezan_products_v2"] = [row(salla_product_id="p2", cost_price_from_salla="99.00")]
    result = preview(db)
    assert group(result) == [] and "historical_supplier_cost_incomplete" in codes(result)


def test_incomplete_salla_canonical_sources_are_reported_not_guessed_as_old_orders():
    db = seeded_db()
    db.data["unified_orders"].append(row(order_number="LEGACY-UNKNOWN", status="in_progress"))
    result = preview(db)
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"
    assert "canonical_salla_source_missing_for_open_cost_preview" in codes(result)


def test_partial_receipt_moves_only_received_unit_out_of_unreceived_pool():
    db = seeded_db(order_row=native_order(qty=2), physical=piece(received=True))
    db.data["mezan_preparation_pieces_v1"].append(dict(piece(), id="physical2", unit_index=2))
    result = preview(db)
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"
    assert result["currencies"]["SAR"]["prestart_supplier_received_uninvoiced"] == "40.00"
    assert sum(float(g["amount"]) for g in group(result)) == 80


def test_unfinished_service_stays_pooled_even_when_physical_product_received():
    actual = piece(received=True)
    actual["services"] = [{"service_id": "print", "required_quantity": "1", "reference_unit_cost": "7.00", "status": "pending"}]
    result = preview(seeded_db(physical=actual))
    assert result["currencies"]["SAR"]["prestart_supplier_received_uninvoiced"] == "40.00"
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "7.00"


def test_unknown_assigned_supplier_does_not_hide_unreceived_product_cost():
    actual = piece()
    actual["supplier_id"] = "not-in-native-suppliers"
    result = preview(seeded_db(physical=actual))
    assert group(result)[0]["party_name"] == "منتجات لم تُستلم من المورد"
    assert group(result)[0]["amount"] == "40.00"


def test_future_receipt_never_attributes_current_estimate_to_supplier():
    actual = piece(received=True)
    actual["received_at"] = "2026-11-03T00:00:00+00:00"
    result = preview(seeded_db(physical=actual))
    assert group(result)[0]["party_id"] == ""
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"


def test_duplicate_canonical_identity_across_closed_and_open_rows_fails_closed():
    db = seeded_db()
    db.data["unified_orders"].append(native_order(status="delivered"))
    result = preview(db)
    assert group(result) == []
    assert "historical_order_identity_ambiguous" in codes(result)


def test_duplicate_line_or_physical_piece_identity_does_not_pick_one_arbitrarily():
    db = seeded_db()
    raw = db.data["unified_orders"][0]["raw_by_source"]["salla_direct"]
    raw["items"].append(deepcopy(raw["items"][0]))
    result = preview(db)
    assert group(result) == [] and "historical_order_item_identity_ambiguous" in codes(result)
    db = seeded_db(order_row=native_order(qty=2))
    db.data["mezan_preparation_pieces_v1"].append(dict(piece(), unit_index=2))
    result = preview(db)
    assert group(result) == [] and "historical_supplier_cost_incomplete" in codes(result)


def test_received_suppliers_with_identical_names_remain_separate_identities():
    db = seeded_db(physical=piece(received=True))
    db.data["unified_orders"].append(native_order(number="SECOND"))
    db.data["mezan_suppliers_v2"].append(row(id="supplier2", name="مورد معروف"))
    db.data["mezan_preparation_pieces_v1"].append(dict(piece(number="SECOND", received=True), id="physical2", supplier_id="supplier2"))
    result = preview(db)
    assert len(group(result)) == 2
    assert {g["party_id"] for g in group(result)} == {"supplier1", "supplier2"}


def test_frozen_system_never_reads_live_sources_as_frozen_historical_data():
    db = seeded_db()
    state = operating_state()
    state["status"] = "frozen"
    result = preview(db, state)
    assert result["status"] == "frozen" and db.reads == []


def test_engine_source_gaps_mark_current_total_partial_instead_of_zero_complete():
    state = operating_state()
    state["engine"]["issues"] = [{"code": "mz2_product_cost_incomplete"}]
    result = preview(seeded_db(), state)
    assert result["complete"] is False
    assert "current_mz2_product_cost_incomplete" in codes(result)


def test_shipping_still_quoted_when_product_cost_is_missing():
    db = seeded_db(order_row=native_order(shipping={"company_code": "native"}), setup=[shipping_setup()])
    db.data["mezan_product_cost_profiles_v2"] = []
    result = preview(db)
    assert not group(result)
    assert result["currencies"]["SAR"]["prestart_shipping_expected"] == "17.25"
    assert result["complete"] is False


def test_stale_assigned_engine_estimate_is_pooled_without_mutating_stored_finance():
    source = {"id": "supplier:salla-NEW:physical1", "kind": "supplier", "party_id": "supplier1",
              "currency": "SAR", "name": "مورد معروف", "expected": "40.00", "confirmed": "0.00"}
    state = operating_state({source["id"]: source})
    db = seeded_db(order_row=native_order(number="NEW", created="2026-11-01T01:00:00+00:00"), physical=piece(number="NEW"))
    original = deepcopy(state)
    result = preview(db, state)
    assert result["currencies"]["SAR"]["poststart_supplier_unreceived"] == "40.00"
    assert group(result, period="poststart")[0]["party_id"] == ""
    assert state == original


def test_invalid_or_future_engine_snapshot_is_incomplete_not_presented_as_current():
    obligation = {"id": "supplier:new:x", "kind": "supplier", "party_id": None, "currency": "SAR", "expected": "12", "confirmed": "0"}
    state = operating_state({obligation["id"]: obligation})
    state["engine"]["as_of"] = "2026-11-03T00:00:00+00:00"
    result = preview(seeded_db(), state)
    assert group(result, period="poststart") == []
    assert "current_cost_snapshot_time_incomplete" in codes(result)


def test_received_order_split_between_suppliers_counts_once_in_the_report():
    from operational_financial_position import build_financial_position

    db = seeded_db(order_row=native_order(qty=2), physical=piece(received=True))
    db.data["mezan_suppliers_v2"].append(row(id="supplier2", name="مورد آخر"))
    db.data["mezan_preparation_pieces_v1"].append(dict(piece(received=True), id="physical2", supplier_id="supplier2", unit_index=2))
    from operational_balance_store import initial
    snapshot = {**initial("owner"), **operating_state()}
    exposure = preview(db, snapshot)
    data = build_financial_position(snapshot, cost_exposure=exposure)["currencies"]["SAR"]
    table = next(t for t in data["tables"] if t["id"] == "products_received_uninvoiced")
    assert [r["orders"] for r in table["rows"]] == [1, 1]
    assert table["totals"]["amount"] == "80.00"
    assert table["totals"]["orders"] == 1
    exposure["bucket_completeness"]["products_received_uninvoiced"] = False
    partial = build_financial_position(snapshot, cost_exposure=exposure)["currencies"]["SAR"]
    assert next(t for t in partial["tables"] if t["id"] == table["id"])["totals"]["orders"] is None


def embroidery_db(*, old=False, with_ids=False, binding_value="نعم"):
    """28 synthetic units in 21 orders, including two repeated-product lines."""
    db = DB({
        "unified_orders": [],
        "mezan_product_cost_profiles_v2": [row(salla_product_id="p1", base_cost="15", cost_currency="SAR")],
        "mezan_product_resource_bindings_v2": [row(id="bag-link", salla_product_id="p1", resource_id="bag", quantity="1")],
        "mezan_product_option_cost_bindings_v2": [row(id="name-link", salla_product_id="p1",
            option_id="embroidery", value_id="yes", option_name="تطريز اسم", value_name=binding_value,
            mode="resource", resource_id="name-service", quantity="1")],
        "mezan_cost_resources_v2": [row(id="bag", kind="component", unit_cost="1.20", cost_currency="SAR"),
            row(id="name-service", kind="service", unit_cost="8", cost_currency="SAR")],
        "mezan_suppliers_v2": [row(id="gulf-supplier", name="Synthetic Gulf supplier", currency="KWD")],
    })
    for index, quantities in enumerate([[1]] * 19 + [[2, 2], [3, 2]], 1):
        items = []
        for line, quantity in enumerate(quantities, 1):
            choice = {"name": "تطريز اسم", "value": {"name": "Yes" if index <= 4 else "نعم"}}
            if with_ids:
                choice["id"], choice["value"]["id"] = "embroidery", "yes"
            items.append({"id": f"line{line}", "product_id": "p1", "quantity": quantity, "options": [choice]})
        created = BEFORE if old else "2026-11-01T01:00:00+00:00"
        raw = {"id": f"order-{index}", "reference_id": f"SYN-{index}", "created_at": created,
            "updated_at": created, "status": "in_progress", "currency": "KWD", "payment_method": "mada",
            "amounts": {"total": {"amount": "1000", "currency": "KWD"}}, "items": items}
        db.data["unified_orders"].append(row(order_number=raw["reference_id"], raw_by_source={"salla_direct": raw}))
    return db


def embroidery_line(db, quantity=1):
    db.data["unified_orders"] = db.data["unified_orders"][:1]
    item = db.data["unified_orders"][0]["raw_by_source"]["salla_direct"]["items"][0]
    item["quantity"] = quantity
    return item


def estimate_embroidery(db, previous=None):
    from operational_balance_sources import collect_sources
    from operational_balance_engine import reconcile
    from operational_balance_store import initial
    state = previous or {**initial("owner"), **operating_state()}
    before, original_state = deepcopy(db.data), deepcopy(state)
    sources = run(collect_sources(db, "owner", START, NOW, operational_state=state))
    updated = reconcile(state, sources, NOW)
    exposure = preview(db, updated)
    assert db.data == before and state == original_state
    assert reconcile(updated, sources, NOW) == updated
    return sources, updated, exposure


@pytest.mark.parametrize("old", [False, True])
@pytest.mark.parametrize("with_ids", [False, True])
@pytest.mark.parametrize("binding_value", ["نعم", "Yes"])
def test_yes_name_cost_counts_all_28_units_and_repeated_product_lines(old, with_ids, binding_value):
    db = embroidery_db(old=old, with_ids=with_ids, binding_value=binding_value)
    _, updated, exposure = estimate_embroidery(db)
    period = "prestart" if old else "poststart"
    assert exposure["currencies"]["SAR"][period + "_supplier_unreceived"] == "677.60"
    assert exposure["distinct_order_counts"]["SAR"]["products_unreceived"] == 21
    assert exposure["bucket_completeness"]["products_unreceived"] is True
    assert {g["currency"] for g in exposure["groups"] if g["kind"] == "supplier"} == {"SAR"}
    assert all(r["confirmed"] == "0.00" for r in updated["engine"]["obligations"].values() if r["kind"] == "supplier")


@pytest.mark.parametrize("with_ids", [False, True])
def test_raw_and_normalized_yes_apply_service_quantity_once(with_ids):
    db = embroidery_db(with_ids=with_ids)
    item = embroidery_line(db, 4)
    item["options_normalized"] = {"تطريز_اسم": ["نعم", " YES "]}
    db.data["mezan_product_option_cost_bindings_v2"][0]["quantity"] = "2"
    _, _, exposure = estimate_embroidery(db)
    assert exposure["currencies"]["SAR"]["poststart_supplier_unreceived"] == "128.80"
    assert exposure["bucket_completeness"]["products_unreceived"] is True


@pytest.mark.parametrize("value", ["No", "لا", " NO "])
def test_no_does_not_apply_yes_service(value):
    db = embroidery_db()
    embroidery_line(db)["options"][0]["value"]["name"] = value
    _, _, exposure = estimate_embroidery(db)
    assert exposure["currencies"]["SAR"]["poststart_supplier_unreceived"] == "16.20"
    assert exposure["bucket_completeness"]["products_unreceived"] is True


def test_unrelated_option_name_is_not_translated_or_treated_as_a_missing_service():
    db = embroidery_db()
    embroidery_line(db)["options"][0]["name"] = "تغليف هدية"
    _, _, exposure = estimate_embroidery(db)
    assert exposure["currencies"]["SAR"]["poststart_supplier_unreceived"] == "16.20"
    assert exposure["bucket_completeness"]["products_unreceived"] is True


@pytest.mark.parametrize("problem", ["unknown_boolean", "unknown_service", "option_id_conflict", "value_id_conflict",
    "normalized_conflict", "normalized_conflict_without_raw_labels", "native_value_conflict_without_raw_labels"])
def test_uncertain_yes_cost_is_visible_and_preserves_previous_estimate(problem):
    db = embroidery_db(with_ids=True)
    item = embroidery_line(db)
    _, prior, _ = estimate_embroidery(db)
    choice = item["options"][0]
    if problem == "unknown_boolean":
        choice.pop("id"); choice["value"] = {"name": "غير محدد"}
    elif problem == "unknown_service":
        choice.pop("id"); choice["value"].pop("id")
        db.data["mezan_cost_resources_v2"] = db.data["mezan_cost_resources_v2"][:1]
    elif problem == "option_id_conflict":
        choice["id"] = "different-option"
    elif problem == "value_id_conflict":
        choice["value"]["id"] = "different-value"
    elif problem == "normalized_conflict_without_raw_labels":
        choice.pop("name"); choice["value"].pop("name")
        item["options_normalized"] = {"تطريز اسم": "لا"}
    elif problem == "native_value_conflict_without_raw_labels":
        choice.pop("name"); choice["value"] = {"id": "different-value"}
        item["options_normalized"] = {"تطريز اسم": "نعم"}
    else:
        item["options_normalized"] = {"تطريز اسم": "لا"}
    sources, updated, exposure = estimate_embroidery(db, prior)
    assert sources["orders"][0]["items_complete"] is False
    assert "mz2_product_cost_incomplete" in {r["code"] for r in sources["issues"]}
    assert exposure["bucket_completeness"]["products_unreceived"] is False
    assert sum(Decimal(r["expected"]) for r in updated["engine"]["obligations"].values()
               if r["kind"] == "supplier") == Decimal("24.20")


@pytest.mark.parametrize("with_ids,extra_raw", [(False, False), (True, False), (True, True)])
def test_aliases_do_not_combine_distinct_option_identities(with_ids, extra_raw):
    db = embroidery_db(with_ids=with_ids)
    item = embroidery_line(db)
    item["options_normalized"] = {"تطريز اسم": "نعم"}
    if extra_raw:
        item["options"].append({"name": "تطريز اسم", "value": {"name": "نعم"}})
    first = db.data["mezan_product_option_cost_bindings_v2"][0]
    db.data["mezan_product_option_cost_bindings_v2"].append(dict(first, id="other-link",
        option_id="different-option", value_id="different-yes", value_name="Yes", resource_id="other-service"))
    db.data["mezan_cost_resources_v2"].append(row(id="other-service", kind="service", unit_cost="30"))
    sources, _, exposure = estimate_embroidery(db)
    if with_ids and not extra_raw:
        assert exposure["currencies"]["SAR"]["poststart_supplier_unreceived"] == "24.20"
        assert exposure["bucket_completeness"]["products_unreceived"] is True
    else:
        assert sources["orders"][0]["items_complete"] is False
        assert exposure["bucket_completeness"]["products_unreceived"] is False


def embroidery_pieces(db, *, received=4, service_done=True):
    embroidery_line(db, 4)
    db.data["mezan_preparation_pieces_v1"] = [row(id=f"piece-{i}", order_number="SYN-1",
        order_item_id="salla:SYN-1:line1", unit_index=i, supplier_id="gulf-supplier",
        supplier_dispatch_status="received" if i <= received else "sent",
        execution_status="received_from_supplier" if i <= received else "assigned", received_at="2026-11-01T10:00:00+00:00",
        services=[{"service_id": "name-service", "required_quantity": "1", "reference_unit_cost": "8",
            "status": "completed" if i <= received and service_done else "pending",
            "completed_by_supplier_id": "gulf-supplier" if i <= received and service_done else None}],
        supplier_service_ids=["name-service"]) for i in range(1, 5)]


@pytest.mark.parametrize("old", [False, True])
@pytest.mark.parametrize("received,service_done,remaining,done", [(2, True, "48.40", "48.40"),
    (4, True, "0.00", "96.80"), (4, False, "32.00", "64.80")])
def test_receipt_removes_only_received_product_and_completed_service(old, received, service_done, remaining, done):
    db = embroidery_db(old=old)
    embroidery_pieces(db, received=received, service_done=service_done)
    _, _, exposure = estimate_embroidery(db)
    period = "prestart" if old else "poststart"
    assert exposure["currencies"]["SAR"].get(period + "_supplier_unreceived", "0.00") == remaining
    assert exposure["currencies"]["SAR"][period + "_supplier_received_uninvoiced"] == done


def test_confirmed_product_and_service_are_not_repriced_or_estimated_twice():
    db = embroidery_db()
    embroidery_pieces(db)
    db.data["mezan_supplier_invoices_v2"] = [row(id="confirmed-invoice", supplier_id="gulf-supplier",
        approved_at="2026-11-01T11:00:00+00:00", lines=[{"piece_ids": [f"piece-{i}" for i in range(1, 5)],
            "product_price_authority": "mezan_v2", "product_unit_price_halalas": 1620,
            "services": [{"service_id": "name-service", "unit_price_halalas": 800, "quantity_per_piece": "1"}]}])]
    _, prior, first = estimate_embroidery(db)
    assert first["currencies"]["SAR"]["current_supplier_confirmed"] == "96.80"
    db.data["mezan_product_cost_profiles_v2"][0]["base_cost"] = "99"
    db.data["mezan_cost_resources_v2"][1]["unit_cost"] = "99"
    _, updated, exposure = estimate_embroidery(db, prior)
    assert exposure["currencies"]["SAR"]["current_supplier_confirmed"] == "96.80"
    assert exposure["currencies"]["SAR"].get("poststart_supplier_unreceived", "0.00") == "0.00"
    assert updated["engine"]["facts"] == prior["engine"]["facts"]
