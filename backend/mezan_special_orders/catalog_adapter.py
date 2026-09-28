"""Tenant-scoped adapters to the existing Product V2 and Order Engine readers.

Only reads canonical existing sources. No Salla requests, refresh, sync, stock
mutation, price import, or creation of a fake commerce order is performed here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Callable

from .contracts import (Address, OptionRule, OptionValue, OriginalLine,
                        OriginalOrder, Product, Recipient)
from .domain import DomainError

PRODUCTS = "mezan_products_v2"
MAX_CATALOG_OPTIONS = 100
MAX_OPTION_CHOICES = 300


def text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def option_key(option_id: Any, label: str) -> str:
    identity = text(option_id)
    if identity and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,110}", identity):
        return "option:" + identity
    return "label:" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


def _label(row: dict) -> str:
    return text(row.get("name") or row.get("label") or row.get("title"))


def _truth(value: Any, *, default: bool) -> bool:
    if value in (True, 1, "true", "1"):
        return True
    if value in (False, 0, "false", "0"):
        return False
    return default


def product_from_catalog(row: dict, tenant_id: str, product_id: str,
                         variant_id: str | None = None) -> Product:
    if text(row.get("user_id")) != tenant_id or text(row.get("salla_product_id")) != product_id:
        raise DomainError("catalog_scope_or_identity_mismatch", 403)
    if row.get("archived") is True:
        raise DomainError("catalog_product_archived", 409)
    raw = row.get("raw_salla") if isinstance(row.get("raw_salla"), dict) else {}
    raw_options = raw.get("options")
    if raw_options is None:
        raw_options = row.get("options")
    if raw_options is None:
        raw_options = []
    if not isinstance(raw_options, list) or len(raw_options) > MAX_CATALOG_OPTIONS:
        raise DomainError("catalog_option_schema_invalid", 422)
    count = row.get("options_count", 0)
    if type(count) is not int or count < 0:
        raise DomainError("catalog_option_count_invalid", 422)
    if not raw_options and count > 0:
        raise DomainError("catalog_option_schema_unavailable", 422)
    rules = []
    for option in raw_options:
        if not isinstance(option, dict) or not _label(option):
            raise DomainError("catalog_option_schema_invalid", 422)
        label = _label(option)
        values = option.get("values") or []
        if not isinstance(values, list) or len(values) > MAX_OPTION_CHOICES:
            raise DomainError("catalog_option_choices_invalid", 422)
        choices = []
        ids = []
        for value in values:
            if isinstance(value, str):
                value = {"name": value}
            if not isinstance(value, dict) or not _label(value):
                raise DomainError("catalog_option_choice_label_missing", 422)
            name = _label(value)
            if name in choices:
                # Two different choices with the same label cannot be selected safely by text.
                raise DomainError("catalog_ambiguous_option_choice", 422)
            choices.append(name)
            identity = text(value.get("id"))
            if identity:
                ids.append((name, identity))
        display = text(option.get("display_type") or option.get("type")).casefold()
        if display in {"file", "upload", "image_upload", "file_upload"}:
            # Do not silently turn a required uploaded specification into empty text.
            raise DomainError("catalog_file_option_requires_secure_attachment_binding", 422)
        selection_mode = "multi" if display in {"checkbox", "checkboxes", "multiple", "multi_select", "multiselect"} else "single"
        maximum = option.get("max_length") or option.get("maximum_characters") or 300
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 3000:
            raise DomainError("catalog_option_length_invalid", 422)
        rules.append(OptionRule(key=option_key(option.get("id"), label), label=label,
            required=_truth(option.get("required", option.get("is_required")), default=True),
            choices=tuple(choices), choice_ids=tuple(ids), selection_mode=selection_mode,
            source_option_id=text(option.get("id")) or None, max_length=maximum))
    variants = row.get("variants") or raw.get("variants") or raw.get("skus") or []
    if not isinstance(variants, list):
        raise DomainError("catalog_variant_schema_invalid", 422)
    variant = None
    if variant_id:
        variant = next((v for v in variants if isinstance(v, dict) and text(v.get("id") or v.get("variant_id")) == variant_id), None)
        if variant is None:
            raise DomainError("catalog_variant_not_found", 404)
    variant = variant or {}
    fixed = _variant_selections(variant, rules) if variant_id else ()
    image_urls = []
    images = raw.get("images") or []
    if not isinstance(images, list):
        raise DomainError("catalog_images_invalid", 422)
    for candidate in [variant.get("image"), row.get("main_image"), *images]:
        if isinstance(candidate, dict):
            candidate = candidate.get("url") or candidate.get("original") or candidate.get("image")
        url = text(candidate)
        if url.startswith("https://") and len(url) <= 2048 and url not in image_urls:
            image_urls.append(url)
    revision = text(row.get("source_revision")) or hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return Product(tenant_id=tenant_id, product_id=product_id, variant_id=variant_id,
        sku=text(variant.get("sku") or row.get("sku")) or None,
        barcode=text(variant.get("barcode") or row.get("barcode")) or None,
        name=text(row.get("name")) or text(raw.get("name")),
        image_url=image_urls[0] if image_urls else None, image_urls=tuple(image_urls[:30]),
        option_rules=tuple(rules), requires_options=bool(rules),
        variant_selections=fixed, catalog_revision=revision)



def _variant_selections(variant: dict, rules: list[OptionRule]) -> tuple[OptionValue, ...]:
    """Validate a SKU against its actual option values; do not infer from its name."""
    raw = variant.get("values") or variant.get("options") or variant.get("selections") or variant.get("attributes")
    selectable = [r for r in rules if r.choices]
    if not raw:
        if selectable:
            raise DomainError("catalog_variant_selection_schema_unavailable", 422)
        return ()
    if isinstance(raw, dict):
        raw = [{"name": name, "value": value} for name, value in raw.items()]
    if not isinstance(raw, list):
        raise DomainError("catalog_variant_selections_invalid", 422)
    out = {}
    for row in raw:
        value_id = text(row.get("id") or row.get("value_id")) if isinstance(row, dict) else text(row)
        matches = [(r, name) for r in rules for name, ident in r.choice_ids if ident == value_id]
        if isinstance(row, dict) and not matches:
            label = text(row.get("name") or row.get("option_name"))
            chosen = row.get("value")
            if isinstance(chosen, dict):
                chosen = chosen.get("name") or chosen.get("value")
            matches = [(r, text(chosen)) for r in rules if r.label.casefold() == label.casefold() and text(chosen) in r.choices]
        if len(matches) != 1:
            raise DomainError("catalog_variant_selection_ambiguous", 422)
        rule, name = matches[0]
        if rule.selection_mode != "single" or rule.key in out:
            raise DomainError("catalog_variant_selection_ambiguous", 422)
        out[rule.key] = name
    return tuple(OptionValue(key=r.key, value=out[r.key]) for r in rules if r.key in out)

def selected_values_from_item(item: Any, product: Product) -> tuple[OptionValue, ...]:
    """Preserve provider selection IDs/names and free-text personalizations."""
    data = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
    by_name = {r.label.casefold(): r for r in product.option_rules}
    by_id = {r.source_option_id: r for r in product.option_rules if r.source_option_id}
    result: dict[str, list[str]] = {}
    rows = data.get("options_raw") or data.get("options") or []
    if not isinstance(rows, list):
        raise DomainError("original_options_invalid", 422)
    for row in rows:
        if not isinstance(row, dict):
            raise DomainError("original_options_invalid", 422)
        nested = row.get("option") if isinstance(row.get("option"), dict) else {}
        identity = text(row.get("option_id") or row.get("id") or nested.get("id"))
        name = text(row.get("option_name") or row.get("name") or nested.get("name"))
        rule = by_id.get(identity) or by_name.get(name.casefold())
        value = row.get("value") if row.get("value") not in (None, "", [], {}) else row.get("values")
        if rule is None and value not in (None, "", [], {}):
            raise DomainError("original_option_not_in_current_schema", 422)
        if rule is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for selected in values:
            if isinstance(selected, dict):
                label = text(selected.get("name") or selected.get("label") or selected.get("value") or selected.get("text"))
                if not label and text(selected.get("id")):
                    label = next((n for n, i in rule.choice_ids if i == text(selected["id"])), "")
            else:
                label = text(selected)
            if label:
                result.setdefault(rule.key, [])
                if label not in result[rule.key]:
                    result[rule.key].append(label)
    normalized = data.get("options_normalized") or {}
    if not isinstance(normalized, dict):
        raise DomainError("original_options_invalid", 422)
    for name, value in normalized.items():
        rule = by_name.get(text(name).casefold())
        if rule is None or rule.key in result:
            continue
        values = value if isinstance(value, list) else [value]
        result[rule.key] = [text(v) for v in values if text(v)]
    out = []
    for rule in product.option_rules:
        values = result.get(rule.key, [])
        if not values:
            continue
        if rule.selection_mode != "multi" and len(values) != 1:
            raise DomainError("original_single_option_has_multiple_values", 422)
        out.append(OptionValue(key=rule.key, value=tuple(values) if rule.selection_mode == "multi" else values[0]))
    return tuple(out)


def recipient_from_order(order: Any) -> Recipient:
    shipping = order.shipping
    customer = order.customer
    recipient = shipping.recipient if isinstance(shipping.recipient, dict) else {}
    address = shipping.address or customer.shipping_address
    name = text(recipient.get("name") or recipient.get("full_name")) or text(customer.name)
    mobile = text(recipient.get("mobile") or recipient.get("phone")) or text(customer.mobile)
    mobile = re.sub(r"[\s()\-]", "", mobile)
    address_model = None
    if address is not None:
        fields = address.model_dump(mode="json") if hasattr(address, "model_dump") else dict(address)
        city, district = text(fields.get("city")), text(fields.get("district"))
        formatted = text(fields.get("formatted")) or "، ".join(text(fields.get(k)) for k in ("city", "district", "street", "building_number") if text(fields.get(k)))
        if city and district and len(formatted) >= 5:
            address_model = Address(city=city, district=district, formatted=formatted,
                country_code=text(fields.get("country_code")) or "SA", postal_code=fields.get("postal_code"),
                building_number=fields.get("building_number"), short_address=fields.get("short_address"),
                latitude=fields.get("latitude"), longitude=fields.get("longitude"))
    try:
        return Recipient(name=name, mobile=mobile, address=address_model)
    except ValueError as exc:
        raise DomainError("original_recipient_incomplete", 422) from exc


class ExistingCatalogAdapter:
    def __init__(self, db: Any, order_reader: Callable | None = None):
        self.db = db
        self.order_reader = order_reader

    async def product(self, tenant_id: str, product_id: str, variant_id: str | None = None) -> Product:
        row = await self.db[PRODUCTS].find_one(
            {"user_id": tenant_id, "salla_product_id": product_id, "archived": {"$ne": True}}, {"_id": 0})
        if row is None:
            raise DomainError("product_not_found", 404)
        return product_from_catalog(row, tenant_id, product_id, variant_id)

    async def original(self, tenant_id: str, number: str) -> OriginalOrder:
        if self.order_reader is not None:
            order = await self.order_reader(tenant_id, number)
        else:
            from order_engine.repository import MongoOrderRepository
            from order_engine.service import get_order, OrderNotFoundError
            try:
                order = await get_order(MongoOrderRepository(self.db), user_id=tenant_id, order_number=number)
            except OrderNotFoundError as exc:
                raise DomainError("original_order_not_found", 404) from exc
        if order is None or text(order.order_number) != number:
            raise DomainError("original_order_not_found", 404)
        items = []
        for item in order.items:
            product_id = text(item.parent_product_id) or text(item.product_id)
            if not product_id:
                raise DomainError("original_product_identity_missing", 422)
            product = await self.product(tenant_id, product_id, text(item.variant_id) or None)
            try:
                quantity = Decimal(str(item.quantity))
            except InvalidOperation as exc:
                raise DomainError("original_quantity_invalid", 422) from exc
            if not quantity.is_finite() or quantity != quantity.to_integral_value() or not 1 <= quantity <= 999:
                raise DomainError("original_quantity_not_whole_units", 422)
            items.append(OriginalLine(item_id=item.order_item_id, product=product,
                quantity=int(quantity), options=selected_values_from_item(item, product)))
        snapshot = order.model_dump(mode="json")
        revision = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        return OriginalOrder(tenant_id=tenant_id, order_id=order.order_id, order_number=order.order_number,
            recipient=recipient_from_order(order), items=tuple(items), source=order.source.provider,
            tracking_number=order.shipping.tracking_number, source_revision=revision)
