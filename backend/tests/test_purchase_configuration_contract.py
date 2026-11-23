"""Native purchase options preserve identity and never write physical quantities."""
from copy import deepcopy
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from pydantic import ValidationError
import pytest

from product_inventory_rules import canonical_specifications
from purchase_configuration_contract import (
    normalize_line_configuration, product_configuration_catalog,
    validate_receipt_configuration,
)
from purchase_invoices_routes import InvoiceLine, PurchaseInvoiceCreate, _draft_values


def product():
    return {
        "user_id": "owner", "mezan_product_id": "pullover", "name": "بلوفر",
        "sku": "PARENT", "main_image": "https://example.test/pullover.webp",
        "options": [
            {"id": "color", "name": "اللون", "type": "color", "required": True,
             "values": [{"id": "green", "name": "أخضر"}, {"id": "red", "name": "أحمر"}]},
            {"id": "size", "name": "المقاس", "type": "select", "required": True,
             "values": [{"id": "m", "name": "M"}, {"id": "l", "name": "L"}]},
            {"id": "letters", "name": "الأحرف", "type": "text", "required": True},
        ],
        "variants": [
            {"id": "green-m", "sku": "P-G-M", "selections": [
                {"option_id": "color", "value_id": "green"}, {"option_id": "size", "value_id": "m"}]},
            {"id": "red-l", "sku": "P-R-L", "selections": [
                {"option_id": "color", "value_id": "red"}, {"option_id": "size", "value_id": "l"}]},
        ],
    }


def line(state="ready"):
    return {"id": "native-line", "item_type": "PRODUCT", "product_id": "pullover",
            "variant_id": "green-m", "quantity": 3, "unit_cost": 10,
            "purchase_configuration": {"state": state, "selections": [
                {"option_id": "color", "value_id": "green"},
                {"option_id": "size", "value_id": "m"}],
                "inputs": [{"option_id": "letters", "value": "  A   B  "}] if state == "ready" else []}}


def assert_code(code, action, status=422):
    with pytest.raises(HTTPException) as error:
        action()
    assert error.value.status_code == status
    assert error.value.detail["code"] == code


def test_catalog_uses_real_photo_and_canonical_variant_option_ids():
    catalog = product_configuration_catalog(product())
    assert catalog["image_url"] == "https://example.test/pullover.webp"
    assert catalog["variants"][0]["variant_id"] == "green-m"
    assert catalog["variants"][0]["selections"] == line()["purchase_configuration"]["selections"]
    assert not catalog["configuration_issues"]
    catalog = product_configuration_catalog({**product(), "main_image": "javascript:alert(1)"})
    assert catalog["image_url"] is None


def test_raw_keeps_physical_color_size_but_no_customer_text_and_ready_derives_labels():
    raw = normalize_line_configuration(line("raw"), product())
    assert raw["purchase_configuration"]["inputs"] == []
    ready = normalize_line_configuration(line(), product())
    assert ready["purchase_configuration"]["inputs"] == [{"option_id": "letters", "value": "A B"}]
    assert {v["name"]: v["value"] for v in ready["purchase_option_labels"]} == {
        "اللون": "أخضر", "المقاس": "M", "الأحرف": "A B"}
    assert len(ready["purchase_configuration_definition_hash"]) == 64


def test_existing_native_line_without_configuration_keeps_legacy_receipt_contract():
    legacy = line()
    legacy.pop("purchase_configuration")
    assert normalize_line_configuration(legacy, product()) == {}
    validate_receipt_configuration(legacy, product(), {"preparation_state": "ready_complete", "specifications": {}})
    assert "purchase_configuration" not in InvoiceLine.model_validate(legacy).model_dump(exclude_none=True)


@pytest.mark.parametrize("mutation,code", [
    (lambda v: v.update(variant_id="red-l"), "purchase_variant_configuration_mismatch"),
    (lambda v: v["purchase_configuration"].update(inputs=[]), "inventory_purchase_required_option"),
    (lambda v: v["purchase_configuration"].update(state="raw"), "inventory_purchase_raw_customization"),
    (lambda v: v["purchase_configuration"].update(note="hidden metadata"), "inventory_purchase_configuration_invalid"),
    (lambda v: v.update(item_type="STOCK_COMPONENT"), "purchase_component_configuration_forbidden"),
])
def test_config_cannot_change_variant_identity_or_hide_extra_values(mutation, code):
    submitted = line()
    mutation(submitted)
    assert_code(code, lambda: normalize_line_configuration(submitted, product()))


def test_incomplete_mappings_and_ambiguous_specification_names_fail_closed():
    incomplete = product()
    incomplete["variants"][1].pop("selections")
    assert_code("purchase_configuration_definition_incomplete", lambda: normalize_line_configuration(line(), incomplete))
    ambiguous = product()
    ambiguous["options"][2]["name"] = "color"
    assert_code("purchase_configuration_option_names_ambiguous", lambda: normalize_line_configuration(line(), ambiguous))


def test_saved_specifications_and_preparation_must_survive_receipt_without_change():
    normalized = normalize_line_configuration(line(), product())
    saved = {**line(), **normalized}
    receipt = {"preparation_state": "ready_complete", "specifications": {
        v["name"]: v["value"] for v in saved["purchase_option_labels"]}}
    validate_receipt_configuration(saved, product(), receipt)
    assert_code("purchase_receipt_configuration_mismatch", lambda: validate_receipt_configuration(
        saved, product(), {**receipt, "specifications": {}}), 409)
    assert_code("purchase_receipt_configuration_mismatch", lambda: validate_receipt_configuration(
        saved, product(), {**receipt, "preparation_state": "requires_preparation"}), 409)
    changed = product()
    changed["options"][2]["name"] = "الاسم المطبوع"
    assert_code("purchase_configuration_definition_changed", lambda: validate_receipt_configuration(saved, changed, receipt), 409)
    changed = product()
    changed["main_image"] = "https://example.test/updated-photo.webp"
    changed["options"][0]["values"][0]["image"] = "https://example.test/new-color.webp"
    validate_receipt_configuration(saved, changed, receipt)


def product_with_option_policy():
    result = product()
    result["options"].append({
        "id": "gift", "name": "كرت إهداء", "type": "radio", "required": False,
        "values": [{"id": "yes", "name": "نعم"}, {"id": "no", "name": "لا"}],
        "visibility": "on_condition", "visibility_condition_type": "=",
        "visibility_condition_option": "color", "visibility_condition_value": "green",
    })
    result["mezan_inventory_option_policy"] = {
        "roles": {"color": "physical", "size": "physical", "letters": "customization",
                  "gift": "informational"},
        "revision": 1, "updated_at": "2026-11-23T00:00:00Z", "updated_by": "owner",
    }
    return result


def test_no_policy_keeps_the_existing_native_definition_fingerprint():
    # Captured from the approved source before adding the local-role contract.
    assert normalize_line_configuration(line(), product())["purchase_configuration_definition_hash"] == (
        "14cc3d0e599db9b560381248332647cd2fd1ff507434ad2f850b83b0ca8b6a2a"
    )


@pytest.mark.parametrize("change", ["role", "visibility", "remove_policy", "add_policy"])
def test_policy_definition_changes_reject_pending_native_receipt_even_when_values_match(change):
    original = product_with_option_policy()
    if change == "add_policy":
        original.pop("mezan_inventory_option_policy")
    saved = {**line(), **normalize_line_configuration(line(), original)}
    receipt = {"preparation_state": "ready_complete", "specifications": {
        value["name"]: value["value"] for value in saved["purchase_option_labels"]}}
    validate_receipt_configuration(saved, original, receipt)
    changed = product_with_option_policy()
    if change == "role":
        changed["mezan_inventory_option_policy"]["roles"]["gift"] = "customization"
    elif change == "visibility":
        changed["options"][-1]["visibility_condition_value"] = "red"
    elif change == "remove_policy":
        changed.pop("mezan_inventory_option_policy")
    current = normalize_line_configuration(saved, changed)
    assert current["purchase_configuration"] == saved["purchase_configuration"]
    assert current["purchase_option_labels"] == saved["purchase_option_labels"]
    assert_code("purchase_configuration_definition_changed",
                lambda: validate_receipt_configuration(saved, changed, receipt), 409)


def test_policy_audit_fields_do_not_change_native_configuration_identity():
    original = product_with_option_policy()
    saved = {**line(), **normalize_line_configuration(line(), original)}
    changed = deepcopy(original)
    changed["mezan_inventory_option_policy"].update(
        revision=2, updated_at="2026-11-24T00:00:00Z", updated_by="another-owner-login")
    assert normalize_line_configuration(line(), changed) == normalize_line_configuration(line(), original)
    validate_receipt_configuration(saved, changed, {"preparation_state": "ready_complete", "specifications": {
        value["name"]: value["value"] for value in saved["purchase_option_labels"]}})


def test_malformed_local_policy_cannot_fall_back_to_a_legacy_native_fingerprint():
    invalid = product_with_option_policy()
    invalid["mezan_inventory_option_policy"]["roles"].pop("gift")
    assert_code("purchase_configuration_definition_incomplete",
                lambda: normalize_line_configuration(line(), invalid))


def test_invoice_schema_accepts_only_new_optional_config_not_server_labels_or_hash():
    InvoiceLine.model_validate(line())
    for extra in ("purchase_option_labels", "purchase_configuration_definition_hash"):
        with pytest.raises(ValidationError):
            InvoiceLine.model_validate({**line(), extra: "untrusted"})


@pytest.mark.asyncio
async def test_real_draft_builder_roundtrip_retains_line_id_config_and_amounts():
    db = AsyncMongoMockClient()["synthetic_native_purchase_config_" + uuid.uuid4().hex]
    await db.mezan_products_v2.insert_one(product())
    await db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": "owner", "company_name": "مورد"})
    payload = PurchaseInvoiceCreate.model_validate({
        "supplier_counterparty_id": "supplier", "invoice_date": "2026-11-20", "tax_treatment": "none", "lines": [line()]})
    first = await _draft_values(db, "owner", payload)
    assert first["total"] == 30
    assert first["lines"][0]["id"] == "native-line"
    assert first["lines"][0]["sku"] == "P-G-M"
    assert first["lines"][0]["purchase_configuration"]["inputs"] == [{"option_id": "letters", "value": "A B"}]
    await db.purchase_invoices.insert_one(deepcopy(first))
    restored = await db.purchase_invoices.find_one({"lines.id": "native-line"})
    assert restored["lines"] == first["lines"]
    normalized = first["lines"][0]
    validate_receipt_configuration(normalized, product(), {"preparation_state": "ready_complete", "specifications": {
        v["name"]: v["value"] for v in normalized["purchase_option_labels"]}})
    assert canonical_specifications({v["name"]: v["value"] for v in normalized["purchase_option_labels"]})
    for collection in ("inventory_receipts", "warehouse_locations", "liabilities", "accounting_journal_groups_v2"):
        assert await db[collection].count_documents({}) == 0
