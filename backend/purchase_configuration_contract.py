"""Optional native purchase descriptions; never a stock or accounting writer.

Older invoices omit purchase_configuration and retain their existing receipt
contract. New configured drafts carry validated product option IDs on the native
invoice line, so a reload or approval retry cannot lose the intended lettering.
"""
from __future__ import annotations

from hashlib import sha256
import json

from fastapi import HTTPException

from operational_balance_inventory import image_url
from operational_purchase_metadata import combinations, configuration, option_contract
from product_inventory_rules import canonical_specifications


def fail(code: str, status: int = 422):
    raise HTTPException(status, detail={"code": code})


def _image(value):
    try:
        return image_url(value)
    except ValueError:
        return None


def product_configuration_catalog(product: dict) -> dict:
    options, issues = option_contract(product)
    raw_variants = product.get("variants") or []
    maps = combinations(product, options)
    valid_maps = bool(raw_variants) and len(maps) == len(raw_variants)
    variants = []
    for index, variant in enumerate(raw_variants):
        if not isinstance(variant, dict) or not variant.get("id"):
            continue
        variants.append({
            "variant_id": str(variant["id"]),
            "name": variant.get("name") or variant.get("sku") or str(variant["id"]),
            "sku": variant.get("sku"), "barcode": variant.get("barcode"),
            "image_url": _image(variant.get("image_url")) or _image(variant.get("image")) or _image(product.get("main_image")) or _image(product.get("image_url")),
            "selections": maps[index] if valid_maps else [],
        })
    if raw_variants and not valid_maps and options:
        issues = [*issues, "تركيبات خيارات المنتج غير مكتملة في ميزان 2"]
    return {
        "image_url": _image(product.get("main_image")) or _image(product.get("image_url")), "options": options,
        "configuration_issues": issues, "variants": variants,
    }


def normalize_line_configuration(line: dict, product: dict) -> dict:
    submitted = line.get("purchase_configuration")
    if submitted is None:
        return {}
    if line.get("item_type") != "PRODUCT":
        fail("purchase_component_configuration_forbidden")
    catalog = product_configuration_catalog(product)
    if catalog["configuration_issues"]:
        fail("purchase_configuration_definition_incomplete")
    contract = {
        "kind": "product", "options": catalog["options"],
        "configuration_issues": catalog["configuration_issues"],
        "selection_combinations": [v["selections"] for v in catalog["variants"] if v["selections"]],
    }
    normalized, labels = configuration(submitted, contract)
    variants = catalog["variants"]
    if variants:
        selected = next((v for v in variants if v["variant_id"] == str(line.get("variant_id") or "")), None)
        if not selected or not selected["selections"]:
            fail("purchase_variant_configuration_unavailable")
        if any(value not in normalized["selections"] for value in selected["selections"]):
            fail("purchase_variant_configuration_mismatch")
    specs = canonical_specifications({value["name"]: value["value"] for value in labels})
    if len(specs) != len(labels):
        fail("purchase_configuration_option_names_ambiguous")
    definition = {"options": [{
        **{key: option[key] for key in ("id", "name", "type", "required")},
        "values": [{key: value[key] for key in ("id", "name")} for value in option["values"]],
    } for option in catalog["options"]], "variants": [
        {"variant_id": v["variant_id"], "selections": v["selections"]} for v in variants
    ]}
    if product.get("mezan_inventory_option_policy") is not None:
        # option_contract already validates the local policy. Bind its effective
        # stock/condition semantics, not its audit revision or timestamps. Keep
        # the legacy definition bytes unchanged when no local policy exists.
        definition["inventory_option_policy"] = [{
            key: option[key]
            for key in ("id", "inventory_role", "visibility_rules", "defer_allowed", "stock_identity")
        } for option in catalog["options"]]
    fingerprint = sha256(json.dumps(definition, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()
    return {
        "purchase_configuration": normalized,
        "purchase_option_labels": labels,
        "purchase_configuration_definition_hash": fingerprint,
    }


def validate_receipt_configuration(line: dict, product: dict, receipt: dict) -> None:
    if line.get("purchase_configuration") is None:
        return
    current = normalize_line_configuration(line, product)
    if line.get("purchase_configuration_definition_hash") != current["purchase_configuration_definition_hash"]:
        fail("purchase_configuration_definition_changed", 409)
    expected_state = "ready_complete" if current["purchase_configuration"]["state"] == "ready" else "requires_preparation"
    expected_specs = canonical_specifications({v["name"]: v["value"] for v in current["purchase_option_labels"]})
    if receipt.get("preparation_state") != expected_state or canonical_specifications(receipt.get("specifications") or {}) != expected_specs:
        fail("purchase_receipt_configuration_mismatch", 409)
