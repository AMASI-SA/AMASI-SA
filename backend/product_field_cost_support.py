"""Normalize Salla product fields and make text fields cost-aware.

Rules:
- Variants expose a human-readable combination label, not only a Salla id.
- Product custom fields are normalized with their Salla field type.
- Text-like custom fields and text-like Salla options can carry a conditional
  cost through the synthetic value ``filled``. The cost is applied only when
  the customer submitted a non-empty value on the order item.
"""
from __future__ import annotations

from typing import Any, Callable


FILL_BASED_TYPES = {"text", "textarea", "long_text", "number", "date", "time", "file"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "label", "title", "value", "text"):
            result = _text(value.get(key))
            if result:
                return result
        return ""
    return str(value).strip()


def normalize_custom_fields(raw: Any) -> list[dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        field_id = _text(row.get("id") or row.get("field_id") or row.get("key")) or str(index)
        name = _text(row.get("name") or row.get("label") or row.get("title")) or f"حقل {index + 1}"
        field_type = _text(
            row.get("type")
            or row.get("input_type")
            or row.get("field_type")
            or row.get("format")
        ).lower() or "text"
        values = row.get("values") or row.get("options") or []
        normalized_values = []
        if isinstance(values, list):
            for value_index, value in enumerate(values):
                if isinstance(value, dict):
                    value_id = _text(value.get("id") or value.get("value") or value.get("key")) or str(value_index)
                    value_name = _text(value.get("name") or value.get("label") or value.get("value")) or value_id
                else:
                    value_id = _text(value) or str(value_index)
                    value_name = _text(value) or value_id
                normalized_values.append({"id": value_id, "name": value_name})
        result.append({
            "id": field_id,
            "name": name,
            "type": field_type,
            "required": bool(row.get("required") or row.get("is_required")),
            "placeholder": _text(row.get("placeholder")) or None,
            "values": normalized_values,
            "cost_subject_id": f"field:{field_id}",
            "cost_value_id": "filled",
            "cost_condition": "filled",
        })
    return result


def _selection_pairs(raw: Any) -> list[tuple[str, str]]:
    if isinstance(raw, dict):
        return [(_text(key), _text(value)) for key, value in raw.items()]
    if not isinstance(raw, list):
        return []
    pairs: list[tuple[str, str]] = []
    for row in raw:
        if isinstance(row, dict):
            option = _text(
                row.get("option_name") or row.get("name") or row.get("option")
                or row.get("option_id") or row.get("id")
            )
            value = _text(
                row.get("value_name") or row.get("value") or row.get("label")
                or row.get("value_id")
            )
            if option or value:
                pairs.append((option, value))
        elif row not in (None, ""):
            pairs.append(("", _text(row)))
    return pairs


def readable_variant_label(variant: dict[str, Any], options: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    option_by_id = {str(row.get("id")): row for row in options if isinstance(row, dict)}
    value_names: dict[str, str] = {}
    for option in options:
        if not isinstance(option, dict):
            continue
        for value in option.get("values") or []:
            if isinstance(value, dict) and value.get("id") is not None:
                value_names[str(value.get("id"))] = _text(value.get("name"))

    source = variant.get("selections") or variant.get("options") or variant.get("values") or variant.get("attributes")
    normalized: list[dict[str, str]] = []
    for option_token, value_token in _selection_pairs(source):
        option = option_by_id.get(option_token)
        option_name = _text(option.get("name")) if option else option_token
        value_name = value_names.get(value_token) or value_token
        if option_name or value_name:
            normalized.append({"option_name": option_name, "value_name": value_name})

    explicit = _text(variant.get("name") or variant.get("title"))
    if explicit and not explicit.isdigit() and explicit != _text(variant.get("id")):
        label = explicit
    else:
        values = [row["value_name"] for row in normalized if row.get("value_name")]
        label = " - ".join(values)
    return label or f"متغير #{_text(variant.get('id'))}", normalized


def _field_value_is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def _option_is_fill_based(option: dict[str, Any]) -> bool:
    return _text(option.get("type")).lower() in FILL_BASED_TYPES and not (option.get("values") or [])


def install_product_field_cost_support() -> None:
    import product_v2_details_routes as details_module
    import product_option_cost_routes as cost_module
    import order_option_cost_snapshot_routes as snapshot_module
    import product_v2_routes as product_v2_module
    import product_cost_setup_routes as cost_setup_module

    original_details: Callable[..., dict[str, Any]] = details_module._details_patch
    if not getattr(original_details, "_mezan_field_cost_support", False):
        def details_with_fields(raw: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            patch = original_details(raw, *args, **kwargs)
            fields = normalize_custom_fields(raw.get("custom_fields"))
            patch["custom_fields"] = fields
            options = patch.get("options") or []
            variants = patch.get("variants") or []
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                label, selections = readable_variant_label(variant, options)
                variant["display_name"] = label
                existing_name = _text(variant.get("name"))
                if not existing_name or existing_name.isdigit() or existing_name == _text(variant.get("id")):
                    variant["name"] = label
                if selections:
                    variant["selections"] = selections
            return patch
        details_with_fields._mezan_field_cost_support = True  # type: ignore[attr-defined]
        details_module._details_patch = details_with_fields

    original_option_value = cost_module._option_value
    if not getattr(original_option_value, "_mezan_field_cost_support", False):
        def option_or_custom_field(product: dict[str, Any], option_id: str, value_id: str):
            if str(option_id).startswith("field:") and str(value_id) == "filled":
                field_id = str(option_id).split(":", 1)[1]
                for field in product.get("custom_fields") or []:
                    if isinstance(field, dict) and str(field.get("id")) == field_id:
                        return (
                            {"id": str(option_id), "name": field.get("name"), "type": field.get("type")},
                            {"id": "filled", "name": "عند تعبئة الحقل"},
                        )
            if str(value_id) == "filled":
                for option in product.get("options") or []:
                    if isinstance(option, dict) and str(option.get("id")) == str(option_id) and _option_is_fill_based(option):
                        return (
                            {"id": str(option_id), "name": option.get("name"), "type": option.get("type")},
                            {"id": "filled", "name": "عند تعبئة الحقل"},
                        )
            return original_option_value(product, option_id, value_id)
        option_or_custom_field._mezan_field_cost_support = True  # type: ignore[attr-defined]
        cost_module._option_value = option_or_custom_field

    original_tokens = snapshot_module.selected_option_tokens
    if not getattr(original_tokens, "_mezan_field_cost_support", False):
        def tokens_with_custom_fields(item: Any):
            tokens = set(original_tokens(item))
            for row in getattr(item, "options_raw", None) or []:
                if not isinstance(row, dict):
                    continue
                option = row.get("option") if isinstance(row.get("option"), dict) else {}
                option_id = row.get("option_id") or row.get("id") or option.get("id")
                option_name = row.get("option_name") or row.get("name") or option.get("name")
                value = row.get("value")
                if isinstance(value, dict):
                    value = value.get("name") or value.get("value") or value.get("text")
                if value is None:
                    value = row.get("text") or row.get("answer")
                if not _field_value_is_filled(value):
                    continue
                if option_id not in (None, ""):
                    tokens.add((f"id:{option_id}", "id:filled"))
                if option_name not in (None, ""):
                    tokens.add((f"name:{snapshot_module._norm(option_name)}", f"name:{snapshot_module._norm('عند تعبئة الحقل')}"))

            for row in getattr(item, "custom_fields", None) or []:
                if not isinstance(row, dict):
                    continue
                field = row.get("field") if isinstance(row.get("field"), dict) else {}
                field_id = row.get("field_id") or row.get("id") or field.get("id")
                field_name = row.get("field_name") or row.get("name") or field.get("name")
                value = row.get("value")
                if value is None:
                    value = row.get("text") or row.get("answer")
                if not _field_value_is_filled(value):
                    continue
                if field_id not in (None, ""):
                    tokens.add((f"id:field:{field_id}", "id:filled"))
                if field_name not in (None, ""):
                    tokens.add((f"name:{snapshot_module._norm(field_name)}", f"name:{snapshot_module._norm('عند تعبئة الحقل')}"))
            return tokens
        tokens_with_custom_fields._mezan_field_cost_support = True  # type: ignore[attr-defined]
        snapshot_module.selected_option_tokens = tokens_with_custom_fields

    original_product_router = product_v2_module.make_product_v2_router
    if not getattr(original_product_router, "_mezan_cost_review_order_fix", False):
        def product_router_with_cost_review_first(db: Any, current_user: Callable[..., Any]):
            product_router = original_product_router(db, current_user)
            cost_setup_router = cost_setup_module.make_product_cost_setup_router(db, current_user)
            product_router.routes = [*cost_setup_router.routes, *product_router.routes]
            return product_router
        product_router_with_cost_review_first._mezan_cost_review_order_fix = True  # type: ignore[attr-defined]
        product_v2_module.make_product_v2_router = product_router_with_cost_review_first
