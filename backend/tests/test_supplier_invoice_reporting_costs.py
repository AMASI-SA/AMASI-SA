"""Business oracles: received supplier actuals, residual current estimates."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from mongomock_motor import AsyncMongoMockClient

from accounting_ledger_v2 import (
    GROUPS_COLLECTION, AccountingLedgerV2Error, read_supplier_invoice_journal_evidence_v2,
)
import supplier_invoice_reporting_costs as reporting
from order_cost_authority import calculate_catalog_line_cost, line_fingerprint

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
APPROVED = NOW - timedelta(days=2)
OWNER = "owner"


def item(quantity=1, *, selections=None):
    return {"order_item_id": "salla:10:line", "source_item_id": "line", "product_id": "product",
            "quantity": quantity, "name": "Product", "options_raw": selections or []}


def catalog(row, *, base=20, bag=2, future_service=True, direct=False, selected_service=False):
    bindings = []
    if future_service:
        bindings.append({"id": "new-service", "option_id": "new-parent", "value_id": "yes", "mode": "resource", "resource_id": "embroidery", "quantity": 1})
    if direct:
        bindings.append({"id": "new-direct", "option_id": "new-direct", "value_id": "yes", "mode": "direct", "direct_amount": 6})
    if selected_service:
        bindings.append({"id": "printing", "option_id": "printing", "value_id": "yes", "mode": "resource", "resource_id": "printing", "quantity": 1})
    return calculate_catalog_line_cost(row, product={"name": "Product", "cost_price_from_salla": 50},
        profile={"base_cost": base} if base is not None else {},
        product_bindings=[{"id": "bag", "resource_id": "bag", "quantity": 1}] if bag is not False else [],
        option_bindings=bindings,
        resources={"bag": {"name": "Bag", "kind": "component", "unit_cost": bag},
                   "embroidery": {"name": "Embroidery", "kind": "service", "unit_cost": 8},
                   "printing": {"name": "Printing", "kind": "service", "unit_cost": 5}})


def invoice(identity="invoice", *, pids=("p1",), price=1900, services=None, eligible=True):
    services = deepcopy(services or [])
    for service in services:
        service["total_halalas"] = int((Decimal(str(service["unit_price_halalas"])) * Decimal(str(service["quantity_per_piece"])) * len(pids)).quantize(Decimal(1), rounding="ROUND_HALF_UP"))
    product, extras = price * len(pids), sum(service["total_halalas"] for service in services)
    return {"user_id": OWNER, "id": identity, "session_id": identity + "-session", "supplier_id": "supplier",
        "status": "payable_posted", "experiment_mode": False, "approved_at": APPROVED,
        "currency": "SAR", "subtotal_halalas": product + extras, "total_halalas": product + extras,
        "lines": [{"piece_ids": list(pids), "quantity": len(pids), "product_id": "product", "product_charge_eligible": eligible,
            "product_price_authority": "mezan_v2", "product_unit_price_halalas": price,
            "product_total_halalas": product, "services_total_halalas": extras, "total_halalas": product + extras,
            "services": services}]}


def piece(index=1, *, pid=None, complete=True, services=None):
    return {"user_id": OWNER, "id": pid or f"p{index}", "piece_id": pid or f"p{index}",
        "order_number": "10", "order_item_id": "salla:10:line", "unit_index": index, "product_id": "product",
        "services": deepcopy(services or []), "service_plan_status": "completed" if complete else "in_progress",
        "remaining_service_count": 0 if complete else 1, "supplier_dispatch_status": "received" if complete else "partial_received",
        **({"received_at": APPROVED} if complete else {})}


def event(inv, row):
    return {"user_id": OWNER, "id": inv["id"] + ":" + row["piece_id"], "event_type": "supplier_piece_service_recorded",
        "session_id": inv["session_id"], "piece_id": row["piece_id"], "order_number": row["order_number"],
        "order_item_id": row["order_item_id"], "unit_index": row["unit_index"], "product_id": row["product_id"],
        "supplier_invoice_id": inv["id"], "supplier_context": {"id": "supplier"}, "finalized_at": inv["approved_at"],
        "financial_invoice_created": True, "liability_created": True}


class ReadOnlyCollection:
    def __init__(self, collection, reads):
        self.collection, self.reads = collection, reads

    def find(self, query, projection):
        assert query.get("user_id") == OWNER
        self.reads.append(deepcopy(query))
        return self.collection.find(query, projection)

    def __getattr__(self, name):
        raise AssertionError("Reporting must not use " + name)


class ReadOnlyDB:
    def __init__(self, db):
        self.db, self.reads = db, []

    def __getitem__(self, name):
        return ReadOnlyCollection(self.db[name], self.reads)


async def authority(monkeypatch, row, *, invoices=None, pieces=None, events=None, extra=None, orders=None):
    monkeypatch.setattr(reporting, "_now", lambda: NOW)
    db = AsyncMongoMockClient()["supplier-report"]
    invoices = invoices if invoices is not None else [invoice(pids=tuple(f"p{i}" for i in range(1, row["quantity"] + 1)))]
    pieces = pieces if pieces is not None else [piece(i) for i in range(1, row["quantity"] + 1)]
    events = events if events is not None else [event(inv, p) for inv in invoices for p in pieces if p["piece_id"] in inv["lines"][0]["piece_ids"]]
    data = {reporting.SUPPLIERS: [{"id": "supplier", "user_id": OWNER}], reporting.INVOICES: invoices,
            reporting.PIECES: pieces, reporting.EVENTS: events, **(extra or {})}
    before = deepcopy(data)
    for name, documents in data.items():
        if documents:
            await db[name].insert_many(deepcopy(documents))
    readonly = ReadOnlyDB(db)
    result = await reporting.load_supplier_invoice_reporting_costs(readonly, OWNER, orders or [{"order_number": "10", "items": [row]}])
    assert data == before
    assert len(readonly.reads) <= 7
    return result


def resolve(row, cost, proof, *, historical=None):
    return reporting.resolve_supplier_invoice_reporting_cost(row, order_number="10", catalog_cost=cost,
        historical_cost=historical or cost, supplier_authority=proof)


@pytest.mark.asyncio
async def test_later_service_cannot_invalidate_received_units_and_bag_is_retained(monkeypatch):
    row = item(2)
    before = catalog(row)
    assert before["calculation_cost_available"] is False
    assert before["missing_cost_reasons"] == ["option_selection_unresolved:new-service"]
    result = resolve(row, before, await authority(monkeypatch, row))
    assert result["calculation_cost_available"] is True
    assert result["line_total"] == 42  # 2 × (invoice product 19 + internal bag 2).
    assert result["cost_stage"] == "mixed"
    assert result["historical_cost_verified"] is False
    assert result["missing_cost_reasons"] == []
    for entry in result["cost_detail_units"]:
        assert {cell["key"]: cell["status"] for cell in entry["cost_detail_cells"]}["option:new-service"] == "not_required"
        assert entry["cost_detail_scope_complete"] is True


@pytest.mark.asyncio
async def test_fully_received_product_replaces_base_and_direct_options(monkeypatch):
    row = item()
    cost = catalog(row, bag=False, direct=True)
    result = resolve(row, cost, await authority(monkeypatch, row))
    assert result["line_total"] == 19
    assert result["cost_stage"] == "actual"
    assert result["historical_cost_verified"] is True
    assert result["calculation_cost_available"] is True
    assert {cell["key"]: cell["status"] for cell in result["cost_detail_cells"]}["option:new-direct"] == "included_in_base"


@pytest.mark.asyncio
async def test_partial_units_keep_received_actual_and_current_unknown_separately(monkeypatch):
    row = item(3)
    proof = await authority(monkeypatch, row, invoices=[invoice(pids=("p1", "p2"))], pieces=[piece(1), piece(2)])
    result = resolve(row, catalog(row), proof)
    assert result["line_total"] == 64
    assert result["calculation_cost_available"] is False
    assert [entry["line_total"] for entry in result["cost_detail_units"]] == [21, 21, 22]
    assert [entry["calculation_cost_available"] for entry in result["cost_detail_units"]] == [True, True, False]
    assert result["cost_detail_units"][2]["cost_stage"] == "expected"


@pytest.mark.asyncio
async def test_unreceived_uses_current_catalog_even_if_old_snapshot_is_complete(monkeypatch):
    row = item()
    current = catalog(row)
    saved = catalog(row, base=5, future_service=False)
    saved.update(cost_authority="order_snapshot", historical_cost_verified=True)
    proof = await authority(monkeypatch, row, invoices=[], pieces=[], events=[])
    assert resolve(row, current, proof, historical=saved) == {**current, "cost_detail_scope_complete": False}


@pytest.mark.asyncio
async def test_split_service_invoice_and_invoice_only_service_count_once(monkeypatch):
    row = item()
    service = {"service_id": "manual", "service_name": "Manual service", "unit_price_halalas": 800, "quantity_per_piece": 1}
    base, extra = invoice(price=2000), invoice("extra", price=0, services=[service], eligible=False)
    received = piece(services=[{"service_id": "manual", "required_quantity": 1, "completed_quantity": 1,
        "status": "completed", "supplier_invoice_id": "extra"}])
    proof = await authority(monkeypatch, row, invoices=[base, extra], pieces=[received])
    result = resolve(row, catalog(row), proof)
    assert result["line_total"] == 30
    assert result["calculation_cost_available"] is True
    assert next(cell for cell in result["cost_detail_cells"] if cell.get("resource_id") == "manual")["unit_cost"] == 8


@pytest.mark.asyncio
async def test_service_actual_matches_one_binding_and_no_current_cost_is_double_counted(monkeypatch):
    row = item(selections=[{"option_id": "new-parent", "value_id": "yes"}])
    service = {"service_id": "embroidery", "unit_price_halalas": 600, "quantity_per_piece": 1}
    inv = invoice(price=1900, services=[service])
    received = piece(services=[{"service_id": "embroidery", "required_quantity": 1, "completed_quantity": 1,
        "status": "completed", "supplier_invoice_id": "invoice"}])
    result = resolve(row, catalog(row), await authority(monkeypatch, row, invoices=[inv], pieces=[received]))
    assert result["line_total"] == 27
    actual = next(cell for cell in result["cost_detail_cells"] if cell.get("resource_id") == "embroidery")
    assert actual["key"] == "option:new-service"
    assert actual["unit_cost"] == 6


@pytest.mark.asyncio
async def test_partial_service_scope_does_not_clear_uncovered_required_choice(monkeypatch):
    row = item()
    received = piece(complete=False, services=[{"service_id": "embroidery", "required_quantity": 1, "status": "pending", "completed_quantity": 0}])
    result = resolve(row, catalog(row), await authority(monkeypatch, row, pieces=[received]))
    assert result["line_total"] == 21
    assert result["calculation_cost_available"] is False
    assert "option_selection_unresolved:new-service" in result["missing_cost_reasons"]


@pytest.mark.asyncio
async def test_completed_scope_missing_required_service_invoice_stays_unknown(monkeypatch):
    row = item()
    received = piece(services=[{"service_id": "removed-service", "required_quantity": 1, "completed_quantity": 1,
        "status": "completed", "supplier_invoice_id": "missing"}])
    result = resolve(row, catalog(row, future_service=False), await authority(monkeypatch, row, pieces=[received]))
    assert result["calculation_cost_available"] is False
    assert "supplier_receipt_service_invoice_missing:removed-service" in result["missing_cost_reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bag", [None, "NaN"])
async def test_received_supplier_actual_never_erases_missing_material_cost(monkeypatch, bag):
    row = item()
    result = resolve(row, catalog(row, bag=bag), await authority(monkeypatch, row))
    assert result["calculation_cost_available"] is False
    assert "component_cost_or_fx_missing:bag" in result["missing_cost_reasons"]


@pytest.mark.asyncio
async def test_explicit_zero_product_and_material_are_valid_cost_evidence(monkeypatch):
    row = item()
    inv = invoice(price=0)
    # Native invoices require a positive total. A zero-priced selected unit is
    # valid alongside another unit's positive charge, not as a free invoice.
    inv["lines"].append(invoice(pids=("outside",), price=100)["lines"][0])
    inv.update(subtotal_halalas=100, total_halalas=100)
    result = resolve(row, catalog(row, bag=0), await authority(monkeypatch, row, invoices=[inv]))
    assert result["line_total"] == 0
    assert result["calculation_cost_available"] is True


@pytest.mark.asyncio
async def test_purchase_input_vat_does_not_enter_product_profit_cost(monkeypatch):
    row = item(2)
    inv = invoice(pids=("p1", "p2"), price=4000)
    inv["purchase_tax"] = {"treatment": "INPUT_VAT", "amount_halalas": 1200, "entity_id": "vat",
        "evidence_file_id": "proof", "evidence_sha256": "a" * 64, "confirmed": True}
    inv["total_halalas"] = 9200
    result = resolve(row, catalog(row, bag=False), await authority(monkeypatch, row, invoices=[inv]))
    assert result["line_total"] == 80
    assert result["calculation_cost_available"] is True


@pytest.mark.asyncio
async def test_whole_invoice_rounding_includes_pieces_outside_report_cohort(monkeypatch):
    row = item()
    service = {"service_id": "manual", "unit_price_halalas": 1, "quantity_per_piece": "0.5"}
    inv = invoice(pids=("p1", "p2", "p3"), price=2000, services=[service])
    received = piece(services=[{"service_id": "manual", "required_quantity": "0.5", "completed_quantity": "0.5",
        "status": "completed", "supplier_invoice_id": "invoice"}])
    result = resolve(row, catalog(row, bag=False), await authority(monkeypatch, row, invoices=[inv], pieces=[received]))
    assert result["line_total"] == 20.01
    assert result["calculation_cost_available"] is True


@pytest.mark.asyncio
async def test_frozen_zero_internal_cost_does_not_acquire_later_catalog_bag(monkeypatch):
    row = item()
    saved = {"quantity": 1, "line_total": 30, "base_total": 30, "components_total": 0,
        "product_components_total": 0, "selected_options_total": 0, "calculation_cost_available": True,
        "cost_authority": "order_snapshot", "historical_cost_verified": True}
    result = resolve(row, catalog(row, bag=5), await authority(monkeypatch, row, invoices=[invoice(price=4000)]), historical=saved)
    assert result["line_total"] == 40
    assert result["cost_stage"] == "actual"


@pytest.mark.asyncio
async def test_legacy_unsplit_component_cost_is_unknown_not_dropped_or_doubled(monkeypatch):
    row = item()
    saved = {"quantity": 1, "line_total": 35, "base_total": 30, "components_total": 5,
        "calculation_cost_available": True, "cost_authority": "order_snapshot", "historical_cost_verified": True}
    result = resolve(row, catalog(row), await authority(monkeypatch, row), historical=saved)
    assert result["calculation_cost_available"] is False
    assert "supplier_invoice_material_evidence_missing" in result["missing_cost_reasons"]


@pytest.mark.asyncio
async def test_partial_salla_fallback_provenance_survives_actual_receipt(monkeypatch):
    row = item(2)
    proof = await authority(monkeypatch, row, invoices=[invoice(price=4000)], pieces=[piece()])
    cost = catalog(row, base=None, bag=False, future_service=False)
    result = resolve(row, cost, proof)
    assert result["line_total"] == 90
    assert result["calculation_cost_available"] is True
    assert result["uses_salla_fallback"] is True
    assert result["mezan_cost_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["invoice_duplicate", "piece_duplicate", "event_duplicate", "cross_invoice", "foreign", "invalid_money", "bad_unit", "bad_product", "bad_variant", "missing_event", "missing_invoice", "reversed"])
async def test_contradictory_or_missing_received_evidence_stays_unknown(monkeypatch, mutation):
    row, inv, received = item(), invoice(), piece()
    invoices, pieces = [inv], [received]
    events = [event(inv, received)]
    if mutation == "invoice_duplicate":
        invoices.append(deepcopy(inv))
    elif mutation == "piece_duplicate":
        pieces.append(deepcopy(received))
    elif mutation == "event_duplicate":
        events.append(deepcopy(events[0]))
    elif mutation == "cross_invoice":
        extra = invoice("duplicate")
        invoices.append(extra)
        events.append(event(extra, received))
    elif mutation == "foreign":
        inv["user_id"] = "another-owner"
    elif mutation == "invalid_money":
        inv["lines"][0]["product_total_halalas"] = 1
    elif mutation == "bad_unit":
        received["unit_index"] = True
    elif mutation == "bad_product":
        events[0]["product_id"] = "another-product"
    elif mutation == "bad_variant":
        row["variant_id"] = "original-variant"
        events[0]["variant_id"] = "another-variant"
    elif mutation == "missing_event":
        events = []
    elif mutation == "missing_invoice":
        invoices = []
    elif mutation == "reversed":
        inv["status"] = "reversed"
    result = resolve(row, catalog(row, future_service=False), await authority(monkeypatch, row, invoices=invoices, pieces=pieces, events=events))
    assert result["calculation_cost_available"] is False
    assert result["missing_cost_reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"status": "draft", "approved_at": None}, {"experiment_mode": True}, {"experiment_run_id": "test"}, {"approved_at": NOW + timedelta(days=1)}])
async def test_unfinalized_and_experimental_or_future_sources_do_not_make_actuals(monkeypatch, change):
    row, inv, received = item(), invoice(), piece(complete=False)
    inv.update(change)
    proof = await authority(monkeypatch, row, invoices=[inv], pieces=[received], events=[])
    result = resolve(row, catalog(row, future_service=False), proof)
    assert result["cost_authority"] == "catalog_estimate"
    assert result["line_total"] == 22


@pytest.mark.asyncio
async def test_assembly_handoff_preserves_supplier_scope_but_employee_receipt_does_not(monkeypatch):
    row, received = item(), piece()
    received.update(status="ready_for_assembly", execution_status="ready_for_assembly")
    result = resolve(row, catalog(row), await authority(monkeypatch, row, pieces=[received]))
    assert result["calculation_cost_available"] is True
    employee = {**piece(complete=False), "status": "received", "preparation_receipt_status": "received"}
    result = resolve(row, catalog(row, future_service=False), await authority(monkeypatch, row, invoices=[], pieces=[employee], events=[]))
    assert result["cost_authority"] == "catalog_estimate"


@pytest.mark.asyncio
async def test_exact_catalog_product_alias_is_allowed_without_sku_matching(monkeypatch):
    row, received = item(), piece()
    received["product_id"] = "mpv2-product"
    extra = {reporting.PRODUCTS: [{"user_id": OWNER, "id": "mpv2-product", "salla_product_id": "product"}]}
    result = resolve(row, catalog(row), await authority(monkeypatch, row, pieces=[received], extra=extra))
    assert result["calculation_cost_available"] is True


@pytest.mark.asyncio
async def test_duplicate_order_line_and_source_edits_cannot_gain_actual_authority(monkeypatch):
    row = item()
    proof = await authority(monkeypatch, row, orders=[{"order_number": "10", "items": [row, deepcopy(row)]}])
    assert resolve(row, catalog(row, future_service=False), proof)["calculation_cost_available"] is False
    proof = await authority(monkeypatch, row)
    changed = {**row, "options_raw": [{"option_id": "changed", "value_id": "yes"}]}
    result = resolve(changed, catalog(changed), proof)
    assert "supplier_receipt_source_changed" in result["missing_cost_reasons"]


@pytest.mark.parametrize("saved", [{"cost_authority": "bundle_snapshot", "calculation_cost_available": True, "line_total": 123},
    {"cost_authority": "approved_correction", "calculation_cost_available": True, "line_total": 321},
    {"cost_authority": "unresolved_snapshot", "calculation_cost_available": False, "missing_cost_reasons": ["cost_snapshot_line_changed"]}])
def test_existing_bundle_correction_and_source_mismatch_authorities_are_preserved(saved):
    row = item()
    proof = {"lines": {("10", row["order_item_id"]): {"line_fingerprint": line_fingerprint(row), "issues": ["test"], "units": {}}}}
    assert resolve(row, catalog(row), proof, historical=saved) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["piece", "event"])
async def test_original_stable_choice_mismatch_cannot_reuse_received_price(monkeypatch, source):
    row = item(selections=[{"option_id": "color", "value_id": "blue"}])
    received, inv = piece(), invoice()
    receipt = event(inv, received)
    (received if source == "piece" else receipt)["options_raw"] = [{"option_id": "color", "value_id": "red"}]
    proof = await authority(monkeypatch, row, pieces=[received], invoices=[inv], events=[receipt])
    result = resolve(row, catalog(row, future_service=False), proof)
    assert result["calculation_cost_available"] is False
    assert "supplier_receipt_source_mismatch" in result["missing_cost_reasons"]


@pytest.mark.asyncio
async def test_matching_stable_choice_allows_label_rename(monkeypatch):
    row = item(selections=[{"option_id": "color", "value_id": "red", "name": "Color", "value": "Crimson"}])
    received = piece()
    received["options_raw"] = [{"option_id": "color", "value_id": "red", "name": "Color", "value": "Red"}]
    received["service_specifications_snapshot"] = [{"name": "Color", "value": "Red"}]
    result = resolve(row, catalog(row), await authority(monkeypatch, row, pieces=[received]))
    assert result["calculation_cost_available"] is True


@pytest.mark.asyncio
async def test_original_label_proof_detects_change_and_ignores_supplier_display_replacements(monkeypatch):
    row = item(selections=[{"name": "Color", "value": "Red"}])
    received = piece()
    received["service_specifications_snapshot"] = [{"name": "Color", "value": "Red"}]
    received["product_options_snapshot"] = {"Color": "Supplier-only red wording"}
    proof = await authority(monkeypatch, row, pieces=[received])
    assert resolve(row, catalog(row), proof)["calculation_cost_available"] is True
    changed = item(selections=[{"name": "Color", "value": "Blue"}])
    proof = await authority(monkeypatch, changed, pieces=[received])
    assert resolve(changed, catalog(changed), proof)["calculation_cost_available"] is False


@pytest.mark.asyncio
async def test_condition_filled_sentinel_is_not_a_customer_selected_value(monkeypatch):
    row = item(selections=[{"option_id": "text", "name": "Name", "value": "Original name"}])
    received = piece(services=[{"service_id": "embroidery", "required_quantity": 1, "completed_quantity": 1,
        "status": "completed", "supplier_invoice_id": "invoice",
        "condition": {"option_id": "text", "option_name": "Name", "value_id": "filled", "value_name": "When filled"}}])
    inv = invoice(services=[{"service_id": "embroidery", "unit_price_halalas": 800, "quantity_per_piece": 1}])
    result = resolve(row, catalog(row), await authority(monkeypatch, row, pieces=[received], invoices=[inv]))
    assert result["calculation_cost_available"] is True
    assert result["line_total"] == 29


@pytest.mark.asyncio
async def test_receipt_service_identity_overrides_later_resource_kind_change(monkeypatch):
    row = item()
    current = catalog(row)
    next(cell for cell in current["cost_detail_cells"] if cell.get("resource_id") == "embroidery")["kind"] = "component"
    inv = invoice(services=[{"service_id": "embroidery", "unit_price_halalas": 800, "quantity_per_piece": 1}])
    received = piece(services=[{"service_id": "embroidery", "required_quantity": 1, "completed_quantity": 1,
        "status": "completed", "supplier_invoice_id": "invoice"}])
    result = resolve(row, current, await authority(monkeypatch, row, pieces=[received], invoices=[inv]))
    assert result["calculation_cost_available"] is True
    assert result["line_total"] == 29
    assert next(cell for cell in result["cost_detail_cells"] if cell.get("resource_id") == "embroidery")["kind"] == "service"


@pytest.mark.asyncio
async def test_unproved_batch_alias_cannot_clear_a_required_service(monkeypatch):
    row = item()
    actual = piece(complete=False, services=[{"service_id": "embroidery", "status": "pending", "required_quantity": 1, "completed_quantity": 0}])
    alias = piece(pid="unproved-alias", services=[])
    proof = await authority(monkeypatch, row, pieces=[actual, alias])
    result = resolve(row, catalog(row), proof)
    assert result["calculation_cost_available"] is False
    assert proof["lines"][("10", row["order_item_id"])]["units"][1]["scope_complete"] is False


@pytest.mark.asyncio
async def test_legacy_missing_variant_is_not_a_source_contradiction(monkeypatch):
    row = {**item(), "variant_id": "v1"}
    result = resolve(row, catalog(row), await authority(monkeypatch, row))
    assert result["calculation_cost_available"] is True
    inv = invoice()
    inv["lines"][0]["variant_id"] = "v2"
    result = resolve(row, catalog(row), await authority(monkeypatch, row, invoices=[inv]))
    assert result["calculation_cost_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "missing_group", "missing_id", "draft", "reversal", "hash",
    "boolean_id", "object_id", "long_id", "nul_id"])
async def test_native_journal_digest_and_reversal_guard(monkeypatch, bad):
    from supplier_native_invoice_v2 import invoice_digest
    row, inv = item(), invoice()
    inv.update(mz2_financial_contract="mz2_supplier_invoice_v1", mz2_txn_group_id="native-group")
    group = {"user_id": OWNER, "txn_group_id": "native-group", "status": "posted",
        "metadata": {"supplier_invoice_id": "invoice", "invoice_digest": invoice_digest(inv)}}
    groups = [group]
    if bad == "missing_group":
        groups = []
    elif bad == "missing_id":
        inv.pop("mz2_txn_group_id")
    elif bad == "boolean_id":
        inv["mz2_txn_group_id"] = True
    elif bad == "object_id":
        inv["mz2_txn_group_id"] = {"$ne": None}
    elif bad == "long_id":
        inv["mz2_txn_group_id"] = "g" * 241
    elif bad == "nul_id":
        inv["mz2_txn_group_id"] = "bad\x00identity"
    elif bad == "draft":
        group["status"] = "draft"
    elif bad == "reversal":
        groups.append({"user_id": OWNER, "txn_group_id": "reverse", "status": "posted", "reversal_of_txn_group_id": "native-group"})
    elif bad == "hash":
        group["metadata"]["invoice_digest"] = "not-the-invoice"
    result = resolve(row, catalog(row), await authority(monkeypatch, row, invoices=[inv], extra={GROUPS_COLLECTION: groups}))
    assert result["calculation_cost_available"] is (bad is None)


@pytest.mark.asyncio
async def test_large_partial_quantity_has_one_residual_estimate_slice(monkeypatch):
    row = item(1_000_000)
    proof = await authority(monkeypatch, row, invoices=[invoice()], pieces=[piece()])
    result = resolve(row, catalog(row, future_service=False), proof)
    assert len(result["cost_detail_units"]) == 2
    assert result["cost_detail_units"][1]["quantity"] == 999999
    assert result["line_total"] == 21_999_999
    assert result["calculation_cost_available"] is True


@pytest.mark.asyncio
async def test_read_limit_fails_instead_of_silently_truncating_evidence(monkeypatch):
    monkeypatch.setattr(reporting, "MAX_ROWS", 0)
    with pytest.raises(ValueError, match="supplier_invoice_reporting_read_limit"):
        await authority(monkeypatch, item())


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_completed_service_quantity_contradiction_cannot_become_complete(monkeypatch, complete):
    row = item(selections=[{"option_id": "new-parent", "value_id": "yes"}])
    received = piece(complete=complete, services=[{"service_id": "embroidery", "required_quantity": 2, "completed_quantity": 2,
        "status": "completed", "supplier_invoice_id": "invoice"}])
    inv = invoice(services=[{"service_id": "embroidery", "unit_price_halalas": 600, "quantity_per_piece": 1}])
    result = resolve(row, catalog(row), await authority(monkeypatch, row, pieces=[received], invoices=[inv]))
    assert result["line_total"] == 27  # Proven amount remains visible.
    assert result["calculation_cost_available"] is False
    assert "supplier_receipt_service_quantity_mismatch:embroidery" in result["missing_cost_reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["option", "field"])
@pytest.mark.parametrize("new_text", ["New customer text", ""])
async def test_shared_explicit_input_identity_detects_changed_typed_value_without_name(monkeypatch, kind, new_text):
    row, received = item(), piece()
    field, identity = ("custom_fields", "field_id") if kind == "field" else ("options_raw", "option_id")
    row[field] = [{identity: "input1", "value": new_text}]
    received[field] = [{identity: "input1", "value": "Old customer text"}]
    result = resolve(row, catalog(row, future_service=False), await authority(monkeypatch, row, pieces=[received]))
    assert result["calculation_cost_available"] is False
    assert "supplier_receipt_source_mismatch" in result["missing_cost_reasons"]


@pytest.mark.asyncio
async def test_fractional_current_quantity_cannot_hide_received_unit_contradiction(monkeypatch):
    row = item(1.5)
    cost = catalog(row, future_service=False)
    assert cost["calculation_cost_available"] is True
    proof = await authority(monkeypatch, row, invoices=[invoice(pids=("p1", "p2"))], pieces=[piece(), piece(2)])
    result = resolve(row, cost, proof)
    assert result["calculation_cost_available"] is False
    assert "supplier_receipt_quantity_mismatch" in result["missing_cost_reasons"]


class JournalProofDB:
    """The public proof API gets one projected read and no storage mutations."""
    def __init__(self, db):
        self.db, self.calls = db, []

    def __getitem__(self, name):
        assert name == GROUPS_COLLECTION
        return self

    def find(self, query, projection):
        self.calls.append((deepcopy(query), deepcopy(projection)))
        return self.db[GROUPS_COLLECTION].find(query, projection)


class NoJournalReads:
    def __getitem__(self, name):
        raise AssertionError("Empty or invalid proof scopes must not read storage")


@pytest.mark.asyncio
async def test_public_supplier_journal_proof_is_scoped_projected_and_keeps_conflicts():
    db = AsyncMongoMockClient()["supplier-journal-proof"]
    await db[GROUPS_COLLECTION].insert_many([
        {"user_id": OWNER, "txn_group_id": "g1", "status": "posted", "notes": "excluded",
         "metadata": {"invoice_digest": "digest", "supplier_invoice_id": "invoice", "other": "excluded"}},
        {"user_id": OWNER, "txn_group_id": "g1", "status": "draft", "operation_id": "unexpected"},
        {"user_id": OWNER, "txn_group_id": "reverse", "status": "draft", "reversal_of_txn_group_id": "g1"},
        {"user_id": "foreign-owner", "txn_group_id": "g1", "status": "posted"},
        {"user_id": "foreign-owner", "txn_group_id": "foreign-reversal", "reversal_of_txn_group_id": "g1"},
        {"user_id": OWNER, "txn_group_id": "unrelated", "status": "posted"},
    ])
    readonly = JournalProofDB(db)
    rows = await read_supplier_invoice_journal_evidence_v2(readonly, user_id=OWNER, txn_group_ids=["g1", "g1"])
    assert len(rows) == 3
    assert [row["txn_group_id"] for row in rows] == ["g1", "g1", "reverse"]
    assert rows[1]["status"] == "draft"
    assert rows[2]["reversal_of_txn_group_id"] == "g1"
    assert rows[0]["metadata"] == {"invoice_digest": "digest", "supplier_invoice_id": "invoice"}
    assert all(set(row) <= {"txn_group_id", "reversal_of_txn_group_id", "status", "metadata"} for row in rows)
    assert readonly.calls == [(
        {"user_id": OWNER, "$or": [{"txn_group_id": {"$in": ["g1"]}}, {"reversal_of_txn_group_id": {"$in": ["g1"]}}]},
        {"_id": 0, "txn_group_id": 1, "reversal_of_txn_group_id": 1, "status": 1,
         "metadata.invoice_digest": 1, "metadata.supplier_invoice_id": 1},
    )]


@pytest.mark.asyncio
async def test_public_supplier_journal_proof_empty_scope_performs_no_read():
    assert await read_supplier_invoice_journal_evidence_v2(NoJournalReads(), user_id=OWNER, txn_group_ids=[]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", ["g1", {"$ne": None}, [""], [True], [7], [None], [{"$ne": None}]])
async def test_public_supplier_journal_proof_rejects_nonidentities_without_storage_access(ids):
    with pytest.raises(AccountingLedgerV2Error):
        await read_supplier_invoice_journal_evidence_v2(NoJournalReads(), user_id=OWNER, txn_group_ids=ids)


@pytest.mark.asyncio
async def test_public_supplier_journal_proof_rejects_invalid_owner_without_storage_access():
    with pytest.raises(AccountingLedgerV2Error, match="user_id"):
        await read_supplier_invoice_journal_evidence_v2(NoJournalReads(), user_id={"$ne": None}, txn_group_ids=["g1"])


@pytest.mark.asyncio
async def test_public_supplier_journal_proof_bounds_input_and_never_truncates_conflicts(monkeypatch):
    import accounting_ledger_v2 as ledger
    monkeypatch.setattr(ledger, "MAX_SUPPLIER_INVOICE_PROOF_GROUPS", 1)
    with pytest.raises(AccountingLedgerV2Error) as input_error:
        await read_supplier_invoice_journal_evidence_v2(NoJournalReads(), user_id=OWNER, txn_group_ids=["g1", "g2"])
    assert input_error.value.code == "accounting_v2_supplier_invoice_proof_scope_too_large"
    db = AsyncMongoMockClient()["supplier-journal-proof-overflow"]
    await db[GROUPS_COLLECTION].insert_many([
        {"user_id": OWNER, "txn_group_id": "g1", "status": "posted"},
        {"user_id": OWNER, "txn_group_id": "g1", "status": "draft"},
    ])
    with pytest.raises(AccountingLedgerV2Error) as output_error:
        await read_supplier_invoice_journal_evidence_v2(JournalProofDB(db), user_id=OWNER, txn_group_ids=["g1"])
    assert output_error.value.code == "accounting_v2_supplier_invoice_proof_scope_too_large"


@pytest.mark.asyncio
async def test_non_native_invoice_never_calls_journal_proof_api(monkeypatch):
    import accounting_ledger_v2 as ledger

    async def forbidden(*args, **kwargs):
        raise AssertionError("No native transaction IDs means no journal proof call")

    monkeypatch.setattr(ledger, "read_supplier_invoice_journal_evidence_v2", forbidden)
    row = item()
    proof = await authority(monkeypatch, row)
    assert resolve(row, catalog(row), proof)["calculation_cost_available"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_gid, complete", [(0, False), (False, False), ([], False), ({}, False), (None, True), ("", True)])
async def test_explicit_malformed_native_identity_cannot_disappear_as_legacy(monkeypatch, stored_gid, complete):
    row, inv = item(), invoice()
    inv["mz2_txn_group_id"] = stored_gid
    # No native contract tag: only absent/None/empty text retain compatibility
    # with legacy invoices; an explicit malformed ID is contradictory proof.
    proof = await authority(monkeypatch, row, invoices=[inv])
    assert resolve(row, catalog(row), proof)["calculation_cost_available"] is complete
