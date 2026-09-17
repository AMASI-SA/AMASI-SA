"""Shared test-only Salla contracts. No network or database write executor."""
from __future__ import annotations
import hashlib
import json
import math
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


NOT_CONFIGURED = "SALLA_SANDBOX_NOT_CONFIGURED"

ENDPOINT_NOT_ALLOWED = "SALLA_P0_ENDPOINT_NOT_ALLOWED"

AMASI_MANIFEST_CLASSIFICATION = "AMASI_TEST_ORDER_MANIFEST"

AMASI_CASE_CLASSIFICATION = "AMASI_TEST_ORDER_CASE"

OFFICIAL_BASE_URL = "https://api.salla.dev/admin/v2"

REQUIRED_ORDER_STATES = {"pending", "under_review", "in_progress", "paid", "partially_paid", "completed", "cancelled"}

REQUIRED_PRODUCT_KINDS = {"simple", "size_color_variant", "text_option", "checkbox_yes_no", "multi_quantity", "replacement"}

SENSITIVE_KEYS = {"access_token", "authorization", "bearer", "card", "customer", "email", "mobile", "name", "phone", "receiver", "refresh_token", "token"}

CASE_INVALID = "SALLA_P0_CASE_INVALID"

UPDATE_MUTATION_REQUIRED = "SALLA_P0_UPDATE_MUTATION_REQUIRED"

REDACTION_KEY_COLLISION = "SALLA_P0_REDACTION_KEY_COLLISION"

UPDATE_MUTABLE_FIELDS = frozenset({"quantity", "options", "branch_id", "price", "cost", "weight"})

class ContractRunnerError(RuntimeError):
    pass

@dataclass(frozen=True)
class SandboxConfig:
    base_url: str
    demo_store_id: str
    token_scopes: frozenset[str]
    seed_manifest: Path
    evidence_dir: Path
    demo_confirmed: bool
    writes_enabled: bool
    destructive_retry_enabled: bool
    write_approval_id: str
    write_approval_issued_at: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SandboxConfig":
        source = env if env is not None else os.environ
        required = {
            "base_url": source.get("SALLA_SANDBOX_BASE_URL", "").strip(),
            "demo_store_id": source.get("SALLA_DEMO_STORE_ID", "").strip() or source.get("SALLA_SANDBOX_STORE_ID", "").strip(),
            "token_scopes": source.get("SALLA_DEMO_TOKEN_SCOPES", "").strip(),
            "seed_manifest": source.get("SALLA_SANDBOX_SEED_MANIFEST", "").strip(),
            "evidence_dir": source.get("SALLA_SANDBOX_EVIDENCE_DIR", "").strip(),
        }
        if any(not value for value in required.values()):
            raise ContractRunnerError(NOT_CONFIGURED)
        if required["base_url"].rstrip("/") != OFFICIAL_BASE_URL:
            raise ContractRunnerError("SALLA_DEMO_BASE_URL_NOT_ALLOWED")
        scopes = frozenset(x.strip() for x in required["token_scopes"].split(",") if x.strip())
        return cls(
            OFFICIAL_BASE_URL, required["demo_store_id"], scopes,
            Path(required["seed_manifest"]), Path(required["evidence_dir"]),
            source.get("SALLA_DEMO_STORE_CONFIRMED", "").casefold() == "true",
            source.get("SALLA_SANDBOX_RUN_WRITES", "").casefold() == "true",
            source.get("SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY", "").casefold() == "true",
            source.get("SALLA_P0_WRITE_APPROVAL_ID", "").strip(),
            source.get("SALLA_P0_WRITE_APPROVAL_ISSUED_AT", "").strip(),
        )

@dataclass(frozen=True)
class AmasiTestConfig(SandboxConfig):
    """Separate opt-in; inherited store-id field is only a compatibility name."""
    expected_store_type: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AmasiTestConfig":
        source = env if env is not None else os.environ
        keys = ("STORE_ID", "STORE_TYPE", "TOKEN_SCOPES", "MANIFEST", "EVIDENCE_DIR")
        values = {key: source.get(f"SALLA_AMASI_TEST_{key}", "").strip() for key in keys}
        if not all(values.values()):
            raise ContractRunnerError("AMASI_TEST_NOT_CONFIGURED")
        if values["STORE_TYPE"].casefold() == "demo":
            raise ContractRunnerError("AMASI_TEST_STORE_TYPE_INVALID")
        return cls(
            OFFICIAL_BASE_URL, values["STORE_ID"],
            frozenset(x.strip() for x in values["TOKEN_SCOPES"].split(",") if x.strip()),
            Path(values["MANIFEST"]), Path(values["EVIDENCE_DIR"]), False,
            source.get("SALLA_AMASI_TEST_RUN_WRITES", "").casefold() == "true", False,
            source.get("SALLA_P0_WRITE_APPROVAL_ID", "").strip(),
            source.get("SALLA_P0_WRITE_APPROVAL_ISSUED_AT", "").strip(),
            values["STORE_TYPE"],
        )

def _is_sensitive(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return normalized in SENSITIVE_KEYS or any(x in normalized for x in ("secret", "password", "authorization", "token"))

def sanitize(value: Any, *, key: str = "", exact_secrets: tuple[str, ...] | frozenset[str] = ()) -> Any:
    if key and _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            source_key = str(raw_key)
            sanitized_key = sanitize(source_key, exact_secrets=exact_secrets)
            if sanitized_key in result:
                raise ContractRunnerError(REDACTION_KEY_COLLISION)
            result[sanitized_key] = sanitize(raw_value, key=source_key, exact_secrets=exact_secrets)
        return result
    if isinstance(value, list):
        return [sanitize(x, exact_secrets=exact_secrets) for x in value]
    if isinstance(value, str):
        for secret in sorted({secret for secret in exact_secrets if isinstance(secret, str) and secret}, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)(access_token|refresh_token|token)=([^&\s]+)", r"\1=[REDACTED]", value)
    return value

def validate_endpoint(method: str, path: str) -> None:
    parsed = urllib.parse.urlsplit(path)
    clean = parsed.path.rstrip("/") or "/"
    allowed = (
        method == "GET" and clean == "/store/info"
        or method == "GET" and bool(re.fullmatch(r"/orders/[^/]+", clean))
        or method == "GET" and clean == "/orders/items" and bool(urllib.parse.parse_qs(parsed.query).get("order_id"))
        or method == "GET" and bool(re.fullmatch(r"/products/[^/]+", clean))
        or method == "POST" and clean == "/orders/items"
        or method in {"PUT", "DELETE"} and bool(re.fullmatch(r"/orders/items/[^/]+", clean))
    )
    if not allowed:
        raise ContractRunnerError(ENDPOINT_NOT_ALLOWED)

def _data(response: dict[str, Any]) -> Any:
    if not isinstance(response, dict):
        return None
    body = response.get("body")
    return body.get("data") if isinstance(body, dict) else None

def _item_product_id(item: dict[str, Any]) -> Any:
    nested = item.get("product")
    return item.get("product_id") or (nested.get("id") if isinstance(nested, dict) else None)

def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != []

def _id_text(value: Any, error: str = CASE_INVALID) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ContractRunnerError(error)
    text = str(value).strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._:-]+", text):
        raise ContractRunnerError(error)
    return text

def _strict_object(value: Any, *, allowed: set[str], required: set[str] = set(), error: str = CASE_INVALID) -> dict[str, Any]:
    if type(value) is not dict or not required.issubset(value) or not set(value).issubset(allowed):
        raise ContractRunnerError(error)
    return value

def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)

def _parse_option_tuple_list(raw: Any, *, allowed_options: set[str], allowed_values: set[str], allow_text: bool) -> frozenset[tuple[str, str]]:
    if not isinstance(raw, list):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    pairs: set[tuple[str, str]] = set()
    for entry in raw:
        if type(entry) is not dict or set(entry) not in ({"option_id", "value_id"}, {"option_id", "value_kind"}):
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        option_id = _id_text(entry["option_id"], "BLOCKED_SEED_MISMATCH")
        if option_id not in allowed_options:
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        if "value_id" in entry:
            value_key = _id_text(entry["value_id"], "BLOCKED_SEED_MISMATCH")
            if value_key not in allowed_values:
                raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        else:
            if not allow_text or entry["value_kind"] != "text":
                raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
            value_key = "@text"
        pair = (option_id, value_key)
        if pair in pairs:
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        pairs.add(pair)
    return frozenset(pairs)

def _seed_product_relations(row: dict[str, Any]) -> tuple[frozenset[tuple[str, str]], tuple[dict[str, Any], ...]]:
    option_ids = {_id_text(x, "BLOCKED_SEED_MISMATCH") for x in row.get("option_ids", [])}
    value_ids = {_id_text(x, "BLOCKED_SEED_MISMATCH") for x in row.get("value_ids", [])}
    option_pairs = _parse_option_tuple_list(
        row.get("option_value_tuples", []),
        allowed_options=option_ids,
        allowed_values=value_ids,
        allow_text=row.get("kind") == "text_option",
    )
    if {option_id for option_id, _ in option_pairs} != option_ids:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    if {value_id for _, value_id in option_pairs if value_id != "@text"} != value_ids:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")

    variant_ids = {_id_text(x, "BLOCKED_SEED_MISMATCH") for x in row.get("variant_ids", [])}
    raw_variants = row.get("variant_tuples", [])
    if not isinstance(raw_variants, list):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    variants: list[dict[str, Any]] = []
    seen_variant_ids: set[str] = set()
    seen_variant_skus: set[str] = set()
    for variant in raw_variants:
        _strict_object(
            variant,
            allowed={"product_id", "variant_id", "sku", "option_value_tuples"},
            required={"product_id", "variant_id", "sku", "option_value_tuples"},
            error="BLOCKED_SEED_MISMATCH",
        )
        product_id = _id_text(variant["product_id"], "BLOCKED_SEED_MISMATCH")
        variant_id = _id_text(variant["variant_id"], "BLOCKED_SEED_MISMATCH")
        sku = _id_text(variant["sku"], "BLOCKED_SEED_MISMATCH")
        variant_pairs = _parse_option_tuple_list(
            variant["option_value_tuples"],
            allowed_options=option_ids,
            allowed_values=value_ids,
            allow_text=False,
        )
        if product_id != str(row["product_id"]) or variant_id not in variant_ids or not variant_pairs or not variant_pairs.issubset(option_pairs):
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        if variant_id in seen_variant_ids or sku in seen_variant_skus:
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        seen_variant_ids.add(variant_id)
        seen_variant_skus.add(sku)
        variants.append({"product_id": product_id, "variant_id": variant_id, "sku": sku, "option_pairs": variant_pairs})
    if seen_variant_ids != variant_ids:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    return option_pairs, tuple(variants)

def validate_seed_structure(config: SandboxConfig, seed: dict[str, Any]) -> None:
    amasi = isinstance(config, AmasiTestConfig)
    if amasi:
        _strict_object(seed, allowed={"classification", "store_id", "orders", "products", "downstream_reviewed"},
                       required={"classification", "store_id", "orders", "products", "downstream_reviewed"}, error="BLOCKED_SEED_MISMATCH")
        if seed["classification"] != AMASI_MANIFEST_CLASSIFICATION or seed["downstream_reviewed"] is not True:
            raise ContractRunnerError("AMASI_TEST_FIXTURE_REVIEW_REQUIRED")
    if str(seed.get("store_id", "")) != config.demo_store_id:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    orders = seed.get("orders", [])
    required_fields = {"state", "order_id", "order_number", "item_id", "product_id", "sku", "payment_method", "branch_id"}
    if (
        not isinstance(orders, list)
        or len(orders) != (1 if amasi else len(REQUIRED_ORDER_STATES))
        or any(type(x) is not dict for x in orders)
        or (not amasi and {str(x.get("state", "")) for x in orders} != REQUIRED_ORDER_STATES)
        or any(not all(_nonempty(x.get(k)) for k in required_fields) for x in orders)
    ):
        raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
    if amasi:
        for row in orders:
            fields = required_fields | {"test_customer_id", "disposable_test_order", "preserve_item_id"}
            _strict_object(row, allowed=fields | {"pending_store_courier", "synthetic_receipt"}, required=fields, error="BLOCKED_SEED_MISMATCH")
            if not isinstance(row["state"], str) or row["state"] not in {"pending", "under_review"} or row["payment_method"] != "bank" or row["disposable_test_order"] is not True:
                raise ContractRunnerError("AMASI_TEST_UNPAID_BANK_ORDER_REQUIRED")
            for key in ("test_customer_id", "preserve_item_id", "order_number", "branch_id"):
                _id_text(row[key], "BLOCKED_SEED_MISMATCH")
            if "synthetic_receipt" in row:
                policy = row["synthetic_receipt"]
                receipt_fields = {"owner_confirmed", "receipt_image_sha256"}
                _strict_object(policy, allowed=receipt_fields, required=receipt_fields, error="BLOCKED_SEED_MISMATCH")
                digest = policy["receipt_image_sha256"]
                if (policy["owner_confirmed"] is not True or not isinstance(digest, str)
                        or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
                    raise ContractRunnerError("AMASI_TEST_RECEIPT_REVIEW_REQUIRED")
            if "pending_store_courier" in row:
                policy = row["pending_store_courier"]
                policy_fields = {"shipment_id", "courier_id", "not_dispatched_confirmed"}
                _strict_object(policy, allowed=policy_fields, required=policy_fields, error="BLOCKED_SEED_MISMATCH")
                for key in ("shipment_id", "courier_id"):
                    _id_text(policy[key], "BLOCKED_SEED_MISMATCH")
                if policy["not_dispatched_confirmed"] is not True:
                    raise ContractRunnerError("AMASI_TEST_COURIER_REVIEW_REQUIRED")
    products = seed.get("products", [])
    if (
        not isinstance(products, list)
        or (not 1 <= len(products) <= 6 if amasi else len(products) != len(REQUIRED_PRODUCT_KINDS))
        or any(type(x) is not dict for x in products)
        or (not {str(x.get("kind", "")) for x in products}.issubset(REQUIRED_PRODUCT_KINDS) if amasi else {str(x.get("kind", "")) for x in products} != REQUIRED_PRODUCT_KINDS)
        or any(not _nonempty(x.get("product_id")) or not _nonempty(x.get("sku")) for x in products)
    ):
        raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
    try:
        order_ids = [_id_text(x["order_id"], "BLOCKED_SEED_MISMATCH") for x in orders]
        item_ids = [_id_text(x["item_id"], "BLOCKED_SEED_MISMATCH") for x in orders]
        product_ids = [_id_text(x["product_id"], "BLOCKED_SEED_MISMATCH") for x in products]
        product_skus = [_id_text(x["sku"], "BLOCKED_SEED_MISMATCH") for x in products]
    except (KeyError, ContractRunnerError):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH") from None
    if len(set(order_ids)) != len(order_ids) or len(set(item_ids)) != len(item_ids) or len(set(product_ids)) != len(product_ids) or len(set(product_skus)) != len(product_skus):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    for row in products:
        if amasi:
            _strict_object(row, allowed={"kind", "product_id", "sku", "branch_id", "variant_ids", "option_ids", "value_ids", "option_value_tuples", "variant_tuples"},
                           required={"kind", "product_id", "sku", "branch_id"}, error="BLOCKED_SEED_MISMATCH")
        if row.get("kind") == "size_color_variant" and not all(_nonempty(row.get(k)) for k in ("variant_ids", "option_ids", "value_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        if row.get("kind") in {"text_option", "checkbox_yes_no"} and not _nonempty(row.get("option_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        if row.get("kind") == "checkbox_yes_no" and not _nonempty(row.get("value_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        for key in ("variant_ids", "option_ids", "value_ids"):
            if key in row:
                values = row[key]
                if not isinstance(values, list):
                    raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
                normalized = [_id_text(x, "BLOCKED_SEED_MISMATCH") for x in values]
                if len(normalized) != len(set(normalized)):
                    raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        _seed_product_relations(row)
    product_pairs = {(str(row["product_id"]), str(row["sku"])) for row in products}
    if any((str(row["product_id"]), str(row["sku"])) not in product_pairs for row in orders):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    if any(_nonempty(v) for k, v in _walk(seed) if k.casefold() in {"customer", "customer_name", "email", "mobile", "phone", "address"}):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")

def _status_slug(order: dict[str, Any]) -> str:
    value = order.get("status")
    if isinstance(value, dict):
        value = value.get("slug") or value.get("name")
    return str(value or "").strip().casefold().replace(" ", "_")

def _product_contract_relations(product_id: str, product: dict[str, Any]) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str, str, frozenset[tuple[str, str]]]]]:
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    options = product.get("options") if isinstance(product.get("options"), list) else []
    option_pairs: set[tuple[str, str]] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("id") or option.get("option_id")
        if not _nonempty(option_id):
            continue
        option_id = str(option_id)
        values = option.get("values") if isinstance(option.get("values"), list) else option.get("value")
        if not isinstance(values, list):
            values = []
        concrete = {
            str(row.get("id") or row.get("value_id"))
            for row in values
            if isinstance(row, dict) and _nonempty(row.get("id") or row.get("value_id"))
        }
        option_pairs.update((option_id, value_id) for value_id in concrete)
        option_type = str(option.get("type") or option.get("value_type") or "").casefold()
        if not concrete and option_type in {"text", "textarea", "string"}:
            option_pairs.add((option_id, "@text"))

    variant_relations: set[tuple[str, str, str, frozenset[tuple[str, str]]]] = set()
    for variant in variants:
        if not isinstance(variant, dict) or not _nonempty(variant.get("id")) or not _nonempty(variant.get("sku")):
            continue
        selections = _option_selections(variant.get("options"))
        relation_pairs = frozenset((option_id, value) for option_id, kind, value in selections if kind == "id")
        variant_relations.add((product_id, str(variant["id"]), str(variant["sku"]), relation_pairs))
    return frozenset(option_pairs), frozenset(variant_relations)

def _hash_id(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]

CASE_FIELDS = {
    "classification", "id", "order_id", "method", "path", "before_fetch_path",
    "after_fetch_path", "body", "assertions", "retry_once",
    "disposable_order_confirmed", "simulate_lost_response", "steps",
    "client_request_id",
}

BODY_FIELDS = {
    "order_id", "product_id", "identifier", "identifier_type", "sku",
    "variant_id", "branch_id", "quantity", "options", "price", "cost", "weight",
}

ASSERTION_PATH = re.compile(
    r"^(before|after)\.(?:fetch_ok|order_id|order_status|order_total|paid_amount|outstanding_amount|payment_status|payment_urls|transactions|items_count|items\.[0-9]+\.(?:item_id|product_id|variant_id|sku|quantity|options|price|cost|weight|branch_id|total|branches_quantity))$"
)

def _product_bindings(row: dict[str, Any]) -> list[dict[str, Any]]:
    option_pairs, variants = _seed_product_relations(row)
    bindings = [{
        "product": row,
        "product_id": str(row["product_id"]),
        "variant_id": None,
        "sku": str(row["sku"]),
        "option_pairs": option_pairs,
    }]
    bindings.extend({"product": row, **variant} for variant in variants)
    return bindings

def _matching_product_binding(seed: dict[str, Any], body: dict[str, Any], order_row: dict[str, Any], method: str) -> dict[str, Any]:
    filters: list[tuple[str, str]] = []
    if "product_id" in body:
        filters.append(("product_id", _id_text(body["product_id"])))
    if "sku" in body:
        filters.append(("sku", _id_text(body["sku"])))
    if "variant_id" in body:
        filters.append(("variant_id", _id_text(body["variant_id"])))
    if "identifier" in body or "identifier_type" in body:
        identifier_type = body.get("identifier_type")
        if identifier_type not in {"id", "sku", "variant_id"} or "identifier" not in body:
            raise ContractRunnerError(CASE_INVALID)
        filters.append((str(identifier_type), _id_text(body["identifier"])))
    if method == "PUT" and not filters:
        filters.extend((("product_id", str(order_row["product_id"])), ("sku", str(order_row["sku"]))))
        if _nonempty(order_row.get("variant_id")):
            filters.append(("variant_id", str(order_row["variant_id"])))
    if method == "POST" and not filters:
        raise ContractRunnerError("SALLA_P0_PRODUCT_ID_NOT_AUTHORIZED")

    def matches(binding: dict[str, Any]) -> bool:
        for kind, expected in filters:
            if kind in {"product_id", "id"} and binding["product_id"] != expected:
                return False
            if kind == "sku" and binding["sku"] != expected:
                return False
            if kind == "variant_id" and binding["variant_id"] != expected:
                return False
        return True

    bindings = [binding for row in seed["products"] for binding in _product_bindings(row) if matches(binding)]
    if len(bindings) != 1:
        raise ContractRunnerError("SALLA_P0_PRODUCT_ID_NOT_AUTHORIZED")
    selected = bindings[0]
    if method == "PUT" and (selected["product_id"] != str(order_row["product_id"]) or selected["sku"] != str(order_row["sku"])):
        raise ContractRunnerError("SALLA_P0_PRODUCT_ID_NOT_AUTHORIZED")
    return selected

def _validate_assertions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractRunnerError(CASE_INVALID)
    for assertion in value:
        _strict_object(assertion, allowed={"path", "equals"}, required={"path", "equals"})
        if not isinstance(assertion["path"], str) or not ASSERTION_PATH.fullmatch(assertion["path"]):
            raise ContractRunnerError(CASE_INVALID)
        try:
            json.dumps(assertion["equals"], allow_nan=False)
        except (TypeError, ValueError):
            raise ContractRunnerError(CASE_INVALID) from None
    return value

def _validate_options(options: Any, binding: dict[str, Any]) -> frozenset[tuple[str, str]]:
    if not isinstance(options, list):
        raise ContractRunnerError(CASE_INVALID)
    seen: set[str] = set()
    product = binding["product"]
    allowed_pairs: frozenset[tuple[str, str]] = binding["option_pairs"]
    allowed_options = {option_id for option_id, _ in allowed_pairs}
    selected_pairs: set[tuple[str, str]] = set()
    for option in options:
        _strict_object(option, allowed={"option_id", "value_id", "value"}, required={"option_id"})
        option_id = _id_text(option["option_id"])
        if option_id in seen or option_id not in allowed_options:
            raise ContractRunnerError("SALLA_P0_OPTION_ID_NOT_AUTHORIZED")
        seen.add(option_id)
        has_value_id = "value_id" in option
        has_text_value = "value" in option
        if has_value_id == has_text_value:
            raise ContractRunnerError(CASE_INVALID)
        value_key = _id_text(option["value_id"]) if has_value_id else "@text"
        if has_text_value and (product.get("kind") != "text_option" or not isinstance(option["value"], str) or not option["value"].strip()):
            raise ContractRunnerError(CASE_INVALID)
        pair = (option_id, value_key)
        if pair not in allowed_pairs:
            raise ContractRunnerError("SALLA_P0_VALUE_ID_NOT_AUTHORIZED")
        selected_pairs.add(pair)
    if binding["variant_id"] is not None and selected_pairs != set(allowed_pairs):
        raise ContractRunnerError("SALLA_P0_VARIANT_OPTION_TUPLE_MISMATCH")
    return frozenset(selected_pairs)

def _validate_delete_steps(steps: Any, *, order_id: str, item_id: str, seed: dict[str, Any]) -> None:
    if not isinstance(steps, list) or len(steps) != 3:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_REQUIRED")
    expected_actions = ["create_replacement", "fetch_confirm_replacement", "delete_old"]
    allowed_by_action = {
        "create_replacement": {"action", "order_id", "item_id", "replacement_item_id", "replacement_product_id", "replacement_sku", "response_status"},
        "fetch_confirm_replacement": {"action", "order_id", "item_id", "replacement_item_id", "replacement_product_id", "replacement_sku", "fetch_ok"},
        "delete_old": {"action", "order_id", "item_id", "replacement_item_id"},
    }
    for step, action in zip(steps, expected_actions):
        allowed = allowed_by_action[action]
        _strict_object(step, allowed=allowed, required=allowed)
        if step["action"] != action or _id_text(step["order_id"]) != order_id or _id_text(step["item_id"]) != item_id:
            raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_MISMATCH")
    replacement_ids = {_id_text(step["replacement_item_id"]) for step in steps}
    if len(replacement_ids) != 1 or item_id in replacement_ids:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_MISMATCH")
    create, verify, _delete = steps
    if isinstance(create["response_status"], bool) or not isinstance(create["response_status"], int) or not 200 <= create["response_status"] < 300:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_REQUIRED")
    if verify["fetch_ok"] is not True:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_REQUIRED")
    product_pairs = {
        (str(row["product_id"]), str(row["sku"]))
        for row in seed["products"]
        if row.get("kind") == "replacement"
    }
    create_pair = (_id_text(create["replacement_product_id"]), _id_text(create["replacement_sku"]))
    verify_pair = (_id_text(verify["replacement_product_id"]), _id_text(verify["replacement_sku"]))
    if create_pair != verify_pair or create_pair not in product_pairs:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_MISMATCH")

def validate_case(case: dict[str, Any], seed: dict[str, Any]) -> None:
    _strict_object(case, allowed=CASE_FIELDS, required={"id", "order_id", "method", "path", "body", "assertions"})
    amasi = seed.get("classification") == AMASI_MANIFEST_CLASSIFICATION
    expected_classification = AMASI_CASE_CLASSIFICATION if amasi else "SANDBOX_CASE_TEMPLATE"
    if case.get("classification", "SANDBOX_CASE_TEMPLATE") != expected_classification:
        raise ContractRunnerError(CASE_INVALID)
    _id_text(case["id"])
    order_id = _id_text(case["order_id"], "SALLA_P0_ORDER_ID_NOT_AUTHORIZED")
    order_rows = [row for row in seed["orders"] if str(row.get("order_id")) == order_id]
    if len(order_rows) != 1:
        raise ContractRunnerError("SALLA_P0_ORDER_ID_NOT_AUTHORIZED")
    order_row = order_rows[0]
    method = case["method"]
    path = case["path"]
    if not isinstance(method, str) or method not in {"POST", "PUT", "DELETE"} or not isinstance(path, str):
        raise ContractRunnerError(CASE_INVALID)
    expected_post_path = "/orders/items"
    item_match = re.fullmatch(r"/orders/items/([A-Za-z0-9._:-]+)", path)
    if (method == "POST" and path != expected_post_path) or (method in {"PUT", "DELETE"} and not item_match):
        raise ContractRunnerError(ENDPOINT_NOT_ALLOWED)
    validate_endpoint(method, path)
    body = _strict_object(case["body"], allowed=BODY_FIELDS)
    if method == "DELETE" and body:
        raise ContractRunnerError(CASE_INVALID)
    if method == "PUT" and not (set(body) & UPDATE_MUTABLE_FIELDS):
        raise ContractRunnerError(UPDATE_MUTATION_REQUIRED)
    if method in {"POST", "PUT"}:
        if "order_id" not in body or _id_text(body["order_id"], "SALLA_P0_ORDER_ID_NOT_AUTHORIZED") != order_id:
            raise ContractRunnerError("SALLA_P0_ORDER_ID_NOT_AUTHORIZED")
    item_id = str(item_match.group(1)) if item_match else ""
    if item_id and str(order_row["item_id"]) != item_id:
        raise ContractRunnerError("SALLA_P0_ITEM_ID_NOT_AUTHORIZED")
    if amasi and item_id == str(order_row["preserve_item_id"]):
        raise ContractRunnerError("AMASI_TEST_ORIGINAL_ITEM_PROTECTED")

    for name in ("retry_once", "disposable_order_confirmed", "simulate_lost_response"):
        if name in case and type(case[name]) is not bool:
            raise ContractRunnerError(CASE_INVALID)
    if case.get("simulate_lost_response", False):
        raise ContractRunnerError("SALLA_P0_SIMULATED_LOST_RESPONSE_UNSUPPORTED")
    _validate_assertions(case["assertions"])
    correlation_id = case.get("client_request_id")
    if correlation_id is not None:
        _id_text(correlation_id)
    expected_fetch = f"/orders/items?order_id={urllib.parse.quote(order_id, safe='')}"
    for name in ("before_fetch_path", "after_fetch_path"):
        if name in case and case[name] != expected_fetch:
            raise ContractRunnerError(CASE_INVALID)

    if "quantity" in body and (type(body["quantity"]) is not int or body["quantity"] <= 0):
        raise ContractRunnerError(CASE_INVALID)
    if method == "POST" and "quantity" not in body:
        raise ContractRunnerError(CASE_INVALID)
    for name in ("price", "cost", "weight"):
        if name in body and (isinstance(body[name], bool) or not isinstance(body[name], (int, float)) or not math.isfinite(body[name]) or body[name] < 0):
            raise ContractRunnerError(CASE_INVALID)

    steps = case.get("steps", [])
    if method == "DELETE" and amasi:
        if steps != [] or str(order_row["preserve_item_id"]) == item_id:
            raise ContractRunnerError("AMASI_TEST_ORIGINAL_ITEM_PROTECTED")
    elif method == "DELETE":
        _validate_delete_steps(steps, order_id=order_id, item_id=item_id, seed=seed)
    elif steps != []:
        raise ContractRunnerError(CASE_INVALID)

    if method != "DELETE":
        binding = _matching_product_binding(seed, body, order_row, method)
        product = binding["product"]
        if "branch_id" in body:
            branch_id = _id_text(body["branch_id"])
            allowed_branches = {str(order_row["branch_id"])}
            if _nonempty(product.get("branch_id")):
                allowed_branches &= {str(product["branch_id"])}
            if branch_id not in allowed_branches:
                raise ContractRunnerError("SALLA_P0_BRANCH_ID_NOT_AUTHORIZED")
        elif method == "POST":
            raise ContractRunnerError("SALLA_P0_BRANCH_ID_NOT_AUTHORIZED")
        if "options" in body:
            _validate_options(body["options"], binding)
        elif binding["variant_id"] is not None:
            raise ContractRunnerError("SALLA_P0_VARIANT_OPTION_TUPLE_MISMATCH")
    if amasi:
        if case.get("disposable_order_confirmed") is not True or case.get("retry_once", False):
            raise ContractRunnerError("AMASI_TEST_SINGLE_REVIEWED_ATTEMPT_REQUIRED")
        if set(body) & {"price", "cost", "weight"} or body.get("quantity", 1) > 2:
            raise ContractRunnerError("AMASI_TEST_MUTATION_LIMIT_EXCEEDED")
        for path in ("before.order_total", "after.order_total"):
            totals = [check["equals"] for check in case["assertions"] if check["path"] == path]
            if len(totals) != 1 or isinstance(totals[0], bool) or not isinstance(totals[0], (int, float)) or not math.isfinite(totals[0]) or totals[0] <= 0:
                raise ContractRunnerError("AMASI_TEST_REVIEWED_TOTALS_REQUIRED")
        if method == "POST" and binding["option_pairs"]:
            selected = _validate_options(body.get("options", []), binding)
            if {key for key, _ in selected} != {key for key, _ in binding["option_pairs"]}:
                raise ContractRunnerError("AMASI_TEST_OPTIONS_REQUIRED")

def _amount(value: Any) -> Any:
    return value.get("amount") if isinstance(value, dict) else value

def _item_amount(item: dict[str, Any], name: str) -> Any:
    direct = item.get(name)
    if direct is not None:
        return _amount(direct)
    amounts = item.get("amounts")
    return _amount(amounts.get(name)) if isinstance(amounts, dict) else None

def extract_snapshot(order_response: dict[str, Any], items_response: dict[str, Any]) -> dict[str, Any]:
    order = _data(order_response) if isinstance(_data(order_response), dict) else {}
    items = _data(items_response) if isinstance(_data(items_response), list) else []
    amounts = order.get("amounts") if isinstance(order.get("amounts"), dict) else {}
    payment = order.get("payment") if isinstance(order.get("payment"), dict) else {}
    return {
        "fetch_ok": order_response.get("status") == 200 and items_response.get("status") == 200,
        "order_id": order.get("id"), "order_status": _status_slug(order),
        "order_total": _amount(amounts.get("total") or order.get("total")),
        "paid_amount": _amount(amounts.get("paid") or payment.get("paid_amount") or order.get("paid_amount")),
        "outstanding_amount": _amount(amounts.get("remaining") or payment.get("remaining_amount") or order.get("remaining_amount")),
        "payment_status": payment.get("status") or order.get("payment_status"),
        "payment_urls": sanitize(payment.get("urls") or order.get("payment_urls") or []),
        "transactions": "UNAVAILABLE_SCOPE_NOT_GRANTED",
        "items_count": len(items),
        "items": [
            {
                "item_id": x.get("id"),
                "product_id": _item_product_id(x),
                "variant_id": x.get("variant_id") or ((x.get("variant") or {}).get("id") if isinstance(x.get("variant"), dict) else None),
                "sku": x.get("sku"),
                "quantity": x.get("quantity"),
                "options": x.get("options", []),
                "price": _item_amount(x, "price"),
                "cost": _item_amount(x, "cost"),
                "weight": x.get("weight"),
                "branch_id": x.get("branch_id"),
                "total": _amount((x.get("amounts") or {}).get("total")) if isinstance(x.get("amounts"), dict) else None,
                "branches_quantity": x.get("branches_quantity", []),
            }
            for x in items
            if isinstance(x, dict)
        ],
    }

def _amasi_money(value: Any, currency: str) -> Decimal:
    if isinstance(value, dict):
        if value.get("currency") != currency:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        value = value.get("amount")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN") from None
    if not amount.is_finite() or amount < 0:
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    return amount

def _amasi_response_success(response: dict[str, Any]) -> bool:
    body = response.get("body")
    status = response.get("status")
    if type(status) is not int or not 200 <= status < 300 or not isinstance(body, dict) or body.get("success") is not True:
        return False
    declared = body.get("status")
    return declared is None or (type(declared) is int and 200 <= declared < 300)

def _validate_amasi_order(order: dict[str, Any], row: dict[str, Any]) -> None:
    customer = order.get("customer")
    if (str(order.get("id", "")) != str(row["order_id"])
            or str(order.get("reference_id", "")) != str(row["order_number"])
            or not isinstance(customer, dict)
            or str(customer.get("id", "")) != str(row["test_customer_id"])
            or _status_slug(order) != row["state"]):
        raise ContractRunnerError("AMASI_TEST_ORDER_IDENTITY_OR_STATE_MISMATCH")
    if order.get("payment_method") != "bank":
        raise ContractRunnerError("AMASI_TEST_UNPAID_BANK_ORDER_REQUIRED")
    methods = order.get("payment_methods")
    if methods != []:
        if not isinstance(methods, list) or len(methods) != 1:
            raise ContractRunnerError("AMASI_TEST_UNPAID_BANK_ORDER_REQUIRED")
        fields = {"payment_method", "amount", "provider", "transaction_reference"}
        _strict_object(methods[0], allowed=fields, required=fields, error="AMASI_TEST_UNPAID_BANK_ORDER_REQUIRED")
        method = methods[0]
        if (method["payment_method"] != "bank" or _amasi_money(method["amount"], "SAR") != 0
                or method["provider"] is not None or method["transaction_reference"] is not None):
            raise ContractRunnerError("AMASI_TEST_UNPAID_BANK_ORDER_REQUIRED")
    allowed_shipping = ("shipping_ready",) if "pending_store_courier" in row else (None, "not_shippable")
    if order.get("shipping_status") not in allowed_shipping:
        raise ContractRunnerError("AMASI_TEST_SHIPMENT_ABSENCE_UNPROVEN")
    amounts = order.get("amounts")
    actions = order.get("payment_actions")
    remaining = actions.get("remaining_action") if isinstance(actions, dict) else None
    total_value = amounts.get("total") if isinstance(amounts, dict) else None
    if not isinstance(total_value, dict) or not isinstance(remaining, dict):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    currency = total_value.get("currency")
    if currency != "SAR" or remaining.get("has_remaining_amount") is not True:
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    total = _amasi_money(total_value, currency)
    if (total <= 0 or _amasi_money(remaining.get("paid_amount"), currency) != 0
            or _amasi_money(remaining.get("remaining_amount"), currency) != total):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    refund = actions.get("refund_action")
    if refund is not None:
        if not isinstance(refund, dict) or refund.get("can_refund") is True:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        for field_name in ("paid_amount", "refund_amount"):
            if _amasi_money(refund.get(field_name), currency) != 0:
                raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        if ("has_refund_amount" in refund and refund["has_refund_amount"] is not False
                or refund.get("pending_refund_amount") is not None and _amasi_money(refund["pending_refund_amount"], currency) != 0):
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    payment = order.get("payment")
    if payment is not None and not isinstance(payment, dict):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    if (payment or {}).get("reference") not in (None, ""):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    for container, fields in ((order, ("paid_amount", "refund_amount")),
                              (amounts, ("paid", "refunded")),
                              (payment or {}, ("paid_amount", "refund_amount"))):
        for field_name in fields:
            if field_name in container and _amasi_money(container[field_name], currency) != 0:
                raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    for container in (order, payment or {}):
        if "remaining_amount" in container and _amasi_money(container["remaining_amount"], currency) != total:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        if "has_remaining_amount" in container and container["has_remaining_amount"] is not True:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    for value in (order.get("payment_status"), order.get("payment_collection_status"),
                  (payment or {}).get("status"), (payment or {}).get("collection_status")):
        if value is not None and (not isinstance(value, str) or value not in {"pending", "unpaid"}):
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    bank = order.get("bank")
    if bank is not None and not isinstance(bank, dict):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    receipt_policy = row.get("synthetic_receipt")
    if receipt_policy is not None:
        receipt_image = order.get("receipt_image")
        if (receipt_policy.get("owner_confirmed") is not True
                or not isinstance(receipt_image, str) or not receipt_image.strip()
                or canonical_digest(receipt_image) != receipt_policy.get("receipt_image_sha256")):
            raise ContractRunnerError("AMASI_TEST_RECEIPT_REVIEW_REQUIRED")
    for container in (order, payment or {}, bank or {}):
        for key in ("receipt", "receipt_image", "bank_receipt", "transfer_receipt", "transaction_reference", "transactions",
                    "payment_receipt_url", "receipt_url", "attachment_url", "proof_url", "proof", "transfer_receipt_url",
                    "paid_at", "captured_at", "refunded_at", "transaction_id"):
            # Only the exact owner-confirmed synthetic top-level attachment is
            # accepted. Payment facts and other proof fields remain guarded.
            if container is order and key == "receipt_image" and receipt_policy is not None:
                continue
            if container.get(key) not in (None, "", [], {}):
                raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")

def _verify_amasi_shipments(transport: Any, row: dict[str, Any], correlation_id: str) -> dict[str, Any]:
    """Default to no shipments; optionally bind one reviewed pending courier.

    Missing tracking is not evidence of non-dispatch for a store courier.
    The explicit operator attestation and fresh pending-state checks are both
    required; this is only a disposable-fixture policy, never a shipping rule.
    """
    order_id = str(row["order_id"])
    policy = row.get("pending_store_courier")
    expected_count = 1 if policy is not None else 0
    error = "AMASI_TEST_COURIER_STATE_UNPROVEN" if policy is not None else "AMASI_TEST_SHIPMENT_ABSENCE_UNPROVEN"
    response = transport.request("GET", f"/shipments?order_id={urllib.parse.quote(order_id, safe='')}&per_page=1", None, correlation_id)
    body = response.get("body")
    page = body.get("pagination") if isinstance(body, dict) else None
    shipments = body.get("data") if isinstance(body, dict) else None
    if (response.get("status") != 200 or not _amasi_response_success(response)
            or not isinstance(shipments, list) or len(shipments) != expected_count or not isinstance(page, dict)):
        raise ContractRunnerError(error)
    for key, expected in (("total", expected_count), ("count", expected_count), ("currentPage", 1)):
        if type(page.get(key)) is not int or page[key] != expected:
            raise ContractRunnerError(error)
    if type(page.get("totalPages")) is not int or page["totalPages"] not in ((1,) if expected_count else (0, 1)):
        raise ContractRunnerError(error)
    for links in (page, page.get("links", {}), body.get("links", {})):
        if links == []:
            continue  # Salla also encodes an empty links collection as [].
        if not isinstance(links, dict) or any(links.get(key) not in (None, "") for key in ("next", "nextPage", "next_page_url")):
            raise ContractRunnerError(error)
    if policy is None:
        return {"mode": "no_shipments", "count": 0}
    shipment = shipments[0]
    expected = {"id": policy["shipment_id"], "courier_id": policy["courier_id"],
                "order_id": row["order_id"], "order_reference_id": row["order_number"]}
    if not isinstance(shipment, dict):
        raise ContractRunnerError(error)
    for key, value in expected.items():
        if _id_text(shipment.get(key), error) != str(value):
            raise ContractRunnerError(error)
    if (shipment.get("status") != "pending" or shipment.get("type") != "shipment"
            or shipment.get("source") != "dashboard" or shipment.get("payment_method") != "bank"
            or shipment.get("trackable") is not False):
        raise ContractRunnerError(error)
    for key in ("label", "shipping_number", "tracking_number", "tracking_link", "driver_info", "pickup_id", "shipping_route"):
        if key not in shipment or shipment[key] is not None:
            raise ContractRunnerError(error)
    # Other consumers recognize these aliases; a null canonical field cannot
    # hide a generated label, carrier handoff or delivery evidence elsewhere.
    for key in ("label_url", "pdf_label", "pdf_url", "documents", "waybill_number", "awb", "tracking_url",
                "shipped_at", "dispatched_at", "handed_over_at", "delivered_at", "received_at"):
        if shipment.get(key) not in (None, "", [], {}):
            raise ContractRunnerError(error)
    return {"mode": "pending_store_courier", "count": 1, "status": "pending",
            "shipment_id_hash": _hash_id(shipment["id"]), "courier_id_hash": _hash_id(shipment["courier_id"])}

def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current

def execute_assertions(assertions: list[dict[str, Any]], before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    context = {"before": before, "after": after}
    return [{"path": x.get("path"), "expected": x.get("equals"), "actual": _resolve_path(context, str(x.get("path", ""))), "passed": _resolve_path(context, str(x.get("path", ""))) == x.get("equals")} for x in assertions]

def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _postcondition(name: str, expected: Any, actual: Any, passed: bool | None = None) -> dict[str, Any]:
    return {"check": name, "expected": expected, "actual": actual, "passed": expected == actual if passed is None else passed}

def _snapshot_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    items = snapshot.get("items") if isinstance(snapshot, dict) else None
    return [item for item in items or [] if isinstance(item, dict)] if isinstance(items, list) else []

def _snapshot_item_by_id(snapshot: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    matches = [item for item in _snapshot_items(snapshot) if str(item.get("item_id")) == item_id]
    return matches[0] if len(matches) == 1 else None

def _option_selections(options: Any) -> frozenset[tuple[str, str, str]]:
    if not isinstance(options, list):
        return frozenset()
    selected: set[tuple[str, str, str]] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("option_id")
        if option_id is None:
            option_id = option.get("product_option_id")
        if option_id is None:
            option_id = option.get("id")
        raw_value = option.get("value_id")
        if raw_value is None:
            raw_value = option.get("option_value_id")
        nested_value = option.get("value")
        if raw_value is None and isinstance(nested_value, dict):
            raw_value = nested_value.get("id")
        if _nonempty(option_id) and _nonempty(raw_value):
            selected.add((str(option_id), "id", str(raw_value)))
        elif _nonempty(option_id) and isinstance(nested_value, str) and nested_value.strip():
            selected.add((str(option_id), "text", nested_value.strip()))
    return frozenset(selected)

def _expected_option_selections(body: dict[str, Any]) -> frozenset[tuple[str, str, str]]:
    selected: set[tuple[str, str, str]] = set()
    for option in body.get("options", []):
        if "value_id" in option:
            selected.add((str(option["option_id"]), "id", str(option["value_id"])))
        else:
            selected.add((str(option["option_id"]), "text", str(option["value"]).strip()))
    return frozenset(selected)

def _item_matches_binding(item: dict[str, Any], binding: dict[str, Any], body: dict[str, Any]) -> bool:
    if str(item.get("product_id")) != binding["product_id"] or str(item.get("sku")) != binding["sku"]:
        return False
    actual_variant = str(item.get("variant_id")) if _nonempty(item.get("variant_id")) else None
    if actual_variant != binding["variant_id"]:
        return False
    if "quantity" in body and item.get("quantity") != body["quantity"]:
        return False
    if "options" in body and _option_selections(item.get("options")) != _expected_option_selections(body):
        return False
    for field in ("price", "cost", "weight", "branch_id"):
        if field in body and item.get(field) != body[field]:
            return False
    return True

def _update_value(container: dict[str, Any], field_name: str, *, request_body: bool = False) -> Any:
    value = container.get(field_name)
    if field_name != "options":
        return value
    selections = _expected_option_selections(container) if request_body else _option_selections(value)
    return [list(selection) for selection in sorted(selections)]

def evaluate_fixed_postconditions(case: dict[str, Any], seed: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    order_id = str(case["order_id"])
    order_row = next(row for row in seed["orders"] if str(row["order_id"]) == order_id)
    before_items = _snapshot_items(before)
    after_items = _snapshot_items(after)
    before_id_values = [item.get("item_id") for item in before_items]
    after_id_values = [item.get("item_id") for item in after_items]
    before_ids = {str(item_id) for item_id in before_id_values}
    after_ids = {str(item_id) for item_id in after_id_values}
    before_by_id = {str(item.get("item_id")): item for item in before_items}
    after_by_id = {str(item.get("item_id")): item for item in after_items}
    conditions = [
        _postcondition("before_fetch_ok", True, before.get("fetch_ok")),
        _postcondition("after_fetch_ok", True, after.get("fetch_ok")),
        _postcondition("before_order_identity", order_id, str(before.get("order_id"))),
        _postcondition("after_order_identity", order_id, str(after.get("order_id"))),
        _postcondition("before_items_count_consistent", len(before_items), before.get("items_count")),
        _postcondition("after_items_count_consistent", len(after_items), after.get("items_count")),
        _postcondition("before_item_ids_present", True, all(_nonempty(item_id) for item_id in before_id_values)),
        _postcondition("after_item_ids_present", True, all(_nonempty(item_id) for item_id in after_id_values)),
        _postcondition("before_item_ids_unique", len(before_items), len(before_ids)),
        _postcondition("after_item_ids_unique", len(after_items), len(after_ids)),
    ]
    method = case["method"]
    body = case["body"]
    if method == "POST":
        binding = _matching_product_binding(seed, body, order_row, method)
        new_ids = after_ids - before_ids
        matching_new = [item for item in after_items if str(item.get("item_id")) in new_ids and _item_matches_binding(item, binding, body)]
        conditions.extend(
            [
                _postcondition("add_one_new_item", 1, len(new_ids)),
                _postcondition("add_target_binding", 1, len(matching_new)),
                _postcondition("add_item_count_delta", len(before_items) + 1, len(after_items)),
                _postcondition("add_preserves_existing_items", sorted(before_ids), sorted(before_ids & after_ids)),
                _postcondition(
                    "add_existing_items_unchanged",
                    {item_id: before_by_id[item_id] for item_id in sorted(before_ids)},
                    {item_id: after_by_id.get(item_id) for item_id in sorted(before_ids)},
                ),
            ]
        )
    elif method == "PUT":
        item_id = urllib.parse.urlsplit(case["path"]).path.rsplit("/", 1)[-1]
        before_target = _snapshot_item_by_id(before, item_id)
        after_target = _snapshot_item_by_id(after, item_id)
        binding = _matching_product_binding(seed, body, order_row, method)
        requested_fields = sorted(set(body) & UPDATE_MUTABLE_FIELDS)
        expected_values = {field_name: _update_value(body, field_name, request_body=True) for field_name in requested_fields}
        before_requested = {
            field_name: _update_value(before_target or {}, field_name)
            for field_name in requested_fields
        }
        after_requested = {
            field_name: _update_value(after_target or {}, field_name)
            for field_name in requested_fields
        }
        changed_requested_fields = [
            field_name
            for field_name in requested_fields
            if before_requested[field_name] != expected_values[field_name]
            and after_requested[field_name] == expected_values[field_name]
        ]
        unrequested_fields = sorted(UPDATE_MUTABLE_FIELDS - set(requested_fields))
        before_unrequested = {
            field_name: _update_value(before_target or {}, field_name)
            for field_name in unrequested_fields
        }
        after_unrequested = {
            field_name: _update_value(after_target or {}, field_name)
            for field_name in unrequested_fields
        }
        conditions.extend(
            [
                _postcondition("update_target_present_before", True, before_target is not None),
                _postcondition("update_target_stable_after", True, after_target is not None),
                _postcondition("update_item_count_stable", len(before_items), len(after_items)),
                _postcondition("update_unrelated_item_ids_stable", sorted(before_ids - {item_id}), sorted(after_ids - {item_id})),
                _postcondition(
                    "update_unrelated_items_unchanged",
                    {key: before_by_id[key] for key in sorted(before_ids - {item_id})},
                    {key: after_by_id.get(key) for key in sorted(before_ids - {item_id})},
                ),
                _postcondition("update_requested_binding", True, bool(after_target and _item_matches_binding(after_target, binding, body))),
                _postcondition("update_requested_values_applied", expected_values, after_requested),
                _postcondition("update_requested_field_delta", True, bool(changed_requested_fields)),
                _postcondition("update_unrequested_fields_stable", before_unrequested, after_unrequested),
            ]
        )
    else:
        item_id = urllib.parse.urlsplit(case["path"]).path.rsplit("/", 1)[-1]
        if seed.get("classification") == AMASI_MANIFEST_CLASSIFICATION:
            preserved_id = str(order_row["preserve_item_id"])
            conditions.extend([
                _postcondition("delete_target_present_before", True, _snapshot_item_by_id(before, item_id) is not None),
                _postcondition("delete_only_target_removed", sorted(before_ids - {item_id}), sorted(after_ids)),
                _postcondition("delete_item_count_delta", len(before_items) - 1, len(after_items)),
                _postcondition("delete_original_item_preserved", True, preserved_id != item_id and preserved_id in before_ids & after_ids),
                _postcondition("delete_remaining_items_unchanged",
                               {key: before_by_id[key] for key in sorted(before_ids - {item_id})},
                               {key: after_by_id.get(key) for key in sorted(after_ids)}),
            ])
            return conditions
        verify_step = case["steps"][1]
        replacement_id = str(verify_step["replacement_item_id"])
        replacement_before = _snapshot_item_by_id(before, replacement_id)
        replacement_after = _snapshot_item_by_id(after, replacement_id)
        replacement_matches = lambda item: bool(
            item
            and str(item.get("product_id")) == str(verify_step["replacement_product_id"])
            and str(item.get("sku")) == str(verify_step["replacement_sku"])
        )
        conditions.extend(
            [
                _postcondition("delete_target_present_before", True, _snapshot_item_by_id(before, item_id) is not None),
                _postcondition("delete_target_absent_after", True, _snapshot_item_by_id(after, item_id) is None),
                _postcondition("delete_replacement_proven_before", True, replacement_matches(replacement_before)),
                _postcondition("delete_replacement_preserved_after", True, replacement_matches(replacement_after)),
                _postcondition("delete_item_count_delta", len(before_items) - 1, len(after_items)),
                _postcondition("delete_only_target_removed", sorted(before_ids - {item_id}), sorted(after_ids)),
                _postcondition(
                    "delete_remaining_items_unchanged",
                    {key: before_by_id[key] for key in sorted(before_ids - {item_id})},
                    {key: after_by_id.get(key) for key in sorted(after_ids)},
                ),
            ]
        )
    return conditions
