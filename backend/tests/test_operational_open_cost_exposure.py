"""Historical open-work reporting is a read-only supplement to operational finance."""
from copy import deepcopy
from test_operational_balance_sources import DB, row, run
from operational_open_cost_exposure import project

START = "2026-11-01T00:00:00+00:00"
NOW = "2026-11-02T00:00:00+00:00"
BEFORE = "2026-10-30T10:00:00+00:00"


def native_order(number="OLD", created=BEFORE, status="in_progress", qty=1, shipping=None):
    raw = {
        "id": "salla-" + number,
        "reference_id": number,
        "created_at": created,
        "status": status,
        "currency": "SAR",
        "amounts": {"total": {"amount": "100.00", "currency": "SAR"}},
        "items": [{"id": "line", "product_id": "p1", "quantity": qty}],
    }
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


def seeded_db(order_row=None, physical=None, invoices=None, setup=None):
    return DB({
        "unified_orders": [order_row or native_order()],
        "mezan_product_cost_profiles_v2": [row(salla_product_id="p1", base_cost="40.00")],
        "mezan_suppliers_v2": [row(id="supplier1", name="مورد معروف")],
        "mezan_preparation_pieces_v1": [physical or piece()],
        "mezan_supplier_invoices_v2": invoices or [],
        "mz2_shipping_setup_v2": setup or [],
    })


def operating_state(obligations=None):
    return {"status": "active", "started_at": START,
            "engine": {"as_of": NOW, "obligations": obligations or {}}}


def group(result, period="prestart", kind="supplier"):
    return [g for g in result["groups"] if g["period"] == period and g["kind"] == kind]


def test_old_assigned_unreceived_is_pooled_not_in_supplier_balance_and_is_not_zero():
    db = seeded_db()
    state = operating_state()
    original_db, original_state = deepcopy(db.data), deepcopy(state)
    first = run(project(db, "owner", state, as_of=NOW))
    second = run(project(db, "owner", state, as_of=NOW))
    assert first == second
    assert first["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"
    supplier = group(first)
    assert len(supplier) == 1
    assert supplier[0]["party_name"] == "منتجات لم تُستلم من المورد"
    assert supplier[0]["amount"] == "40.00"
    assert supplier[0]["stage"] == "under_fulfillment"
    assert supplier[0]["units"] == 1
    assert db.data == original_db and state == original_state
    assert first["warning"] == "historical_preview_never_add_to_confirmed_or_expected_payable_without_owner_evidence"


def test_old_waiting_review_is_visible_and_remains_unreceived_pool():
    db = seeded_db(order_row=native_order(status="waiting_review"))
    result = run(project(db, "owner", operating_state(), as_of=NOW))
    assert group(result)[0]["stage"] == "awaiting_review"
    assert group(result)[0]["amount"] == "40.00"


def test_only_durable_supplier_receipt_moves_old_cost_to_supplier_bucket():
    db = seeded_db()
    fake = db.data["mezan_preparation_pieces_v1"][0]
    fake.update(status="received", received_at="2026-11-01T09:00:00+00:00")
    before = run(project(db, "owner", operating_state(), as_of=NOW))
    assert group(before)[0]["party_name"] == "منتجات لم تُستلم من المورد"
    fake.update(supplier_dispatch_status="received", execution_status="received_from_supplier")
    after = run(project(db, "owner", operating_state(), as_of=NOW))
    assert after["currencies"]["SAR"]["prestart_supplier_received_uninvoiced"] == "40.00"
    assert group(after)[0]["party_name"] == "مورد معروف"
    assert group(after)[0]["stage"] == "received_without_approved_invoice"
    assert after["currencies"]["SAR"].get("prestart_supplier_unreceived", "0.00") == "0.00"


def test_prestart_invoiced_piece_not_added_as_second_estimated_debt():
    invoice = row(id="invoice1", supplier_id="supplier1",
                  approved_at="2026-11-01T11:00:00+00:00",
                  lines=[{"piece_ids": ["physical1"], "product_price_authority": "mezan_v2",
                          "product_unit_price_halalas": 4000}])
    db = seeded_db(physical=piece(received=True), invoices=[invoice])
    result = run(project(db, "owner", operating_state(), as_of=NOW))
    assert group(result) == []
    assert any(issue["code"] == "prestart_order_postcutover_invoice_needs_reconciliation"
               for issue in result["issues"])


def test_missing_historical_product_cost_never_silent_zero():
    db = seeded_db()
    db.data["mezan_product_cost_profiles_v2"] = []
    result = run(project(db, "owner", operating_state(), as_of=NOW))
    assert group(result) == []
    assert any(issue["code"] == "historical_supplier_cost_incomplete" for issue in result["issues"])


def test_cancelled_or_completed_historical_orders_excluded_from_open_work():
    db = seeded_db(order_row=native_order(status="delivered"))
    assert group(run(project(db, "owner", operating_state(), as_of=NOW))) == []
    db.data["unified_orders"] = [native_order(status="cancelled")]
    assert group(run(project(db, "owner", operating_state(), as_of=NOW))) == []


def test_current_unreceived_and_historical_unreceived_are_visible_but_not_double_counted():
    obligations = {
        "supplier:new:item": {"id": "supplier:new:item", "kind": "supplier",
                              "party_id": None, "party_type": "supplier", "currency": "SAR",
                              "name": "تكلفة منتجات لم تُستلم من المورد",
                              "expected": "80.00", "confirmed": "0.00"},
        "shipping:new": {"id": "shipping:new", "kind": "shipping",
                         "party_id": "courier1", "party_type": "courier", "currency": "SAR",
                         "name": "شركة شحن", "expected": "17.25", "confirmed": "0.00"},
    }
    db = seeded_db()
    db.data["unified_orders"].append(native_order(number="NEW",created="2026-11-01T07:00:00+00:00"))
    result = run(project(db, "owner", operating_state(obligations), as_of=NOW))
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"
    assert result["currencies"]["SAR"]["poststart_supplier_unreceived"] == "80.00"
    assert result["currencies"]["SAR"]["current_shipping_expected"] == "17.25"
    assert all(g["party_name"] == "منتجات لم تُستلم من المورد"
               for g in group(result, kind="supplier") + group(result, period="poststart", kind="supplier"))
    assert result["currencies"]["SAR"]["current_supplier_expected"] == "80.00"


def test_prestart_shipping_quote_is_independent_of_delivery_and_supplier_cost():
    setup = row(couriers=[{"courier_key": "imile", "name": "إيمايل",
                           "confirmed_by": "owner", "confirmed_at": BEFORE,
                           "salla_carrier_keys": ["native"]}],
                contracts=[{"id": "contract", "status": "approved", "party_type": "courier",
                            "party_id": "imile", "context": "delivery", "effective_from": BEFORE,
                            "confirmed_by": "owner", "confirmed_at": BEFORE,
                            "delivery_fee": "17.25", "vat_included": True,
                            "cod_fixed_fee": "0.00", "cod_percent": "0.00"}])
    db = seeded_db(order_row=native_order(shipping={"company_code": "native"}), setup=[setup])
    result = run(project(db, "owner", operating_state(), as_of=NOW))
    assert result["currencies"]["SAR"]["prestart_shipping_expected"] == "17.25"
    shipping = group(result, kind="courier")
    assert shipping == []
    shipping = group(result, kind="shipping")
    # Shipping appears as a carrier-specific kind in the preview.
    shipping = [g for g in result["groups"] if g["period"] == "prestart" and g["kind"] == "courier"]
    assert shipping[0]["stage"] == "not_delivered"
    assert shipping[0]["amount"] == "17.25"
    assert shipping[0]["party_name"] == "إيمايل"


def test_historical_missing_cost_can_preview_explicit_current_salla_catalog_cost_with_warning():
    db = seeded_db()
    db.data["mezan_product_cost_profiles_v2"] = []
    db.data["mezan_products_v2"] = [row(salla_product_id="p1", cost_price_from_salla="33.50")]
    result = run(project(db, "owner", operating_state(), as_of=NOW))
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "33.50"
    assert group(result)[0]["party_name"] == "منتجات لم تُستلم من المورد"
    assert any(item["code"] == "historical_current_catalog_estimate_used" and item["count"] == 1
               for item in result["issues"])


def test_historical_cost_missing_from_both_sources_stays_incomplete_not_zero():
    db = seeded_db()
    db.data["mezan_product_cost_profiles_v2"] = []
    db.data["mezan_products_v2"] = [row(salla_product_id="p2", cost_price_from_salla="99.00")]
    result = run(project(db, "owner", operating_state(), as_of=NOW))
    assert group(result) == []
    assert any(item["code"] == "historical_supplier_cost_incomplete"
               for item in result["issues"])


def test_incomplete_salla_canonical_sources_are_reported_not_guessed_as_old_orders():
    db = seeded_db()
    db.data["unified_orders"].append(row(order_number="LEGACY-UNKNOWN", status="in_progress"))
    result = run(project(db, "owner", operating_state(), as_of=NOW))
    assert result["currencies"]["SAR"]["prestart_supplier_unreceived"] == "40.00"
    assert any(item["code"] == "canonical_salla_source_missing_for_open_cost_preview"
               for item in result["issues"])
