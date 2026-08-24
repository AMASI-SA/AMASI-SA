"""Normalize Salla product fields and keep Mezan product costs consistent.

Rules:
- Variants expose a human-readable combination label, not only a Salla id.
- Product custom fields are normalized with their Salla field type.
- Text-like custom fields and text-like Salla options can carry a conditional
  cost through the synthetic value ``filled``.
- Open supplier-invoice sessions are live views of current Mezan product cost,
  option surcharges, components and services until the invoice is approved.
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


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed else 0.0


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


def _supplier_cost_item(piece: dict[str, Any]) -> dict[str, Any]:
    """Expose preparation-piece customer choices to the canonical option matcher."""
    normalized = (
        piece.get("options_normalized")
        or piece.get("product_options")
        or piece.get("selected_options")
        or {}
    )
    return {
        "variant_id": piece.get("variant_id") or piece.get("salla_variant_id"),
        "sku": piece.get("sku"),
        "options_raw": piece.get("options_raw") or piece.get("options") or [],
        "options_normalized": normalized if isinstance(normalized, dict) else {},
        "custom_fields": piece.get("custom_fields") or [],
    }


def _live_service_row(resource: dict[str, Any], binding: dict[str, Any], *, option_selected: bool) -> dict[str, Any]:
    amount = _number(resource.get("unit_cost"))
    quantity = _number(binding.get("quantity")) or 1.0
    return {
        "service_id": _text(resource.get("id")),
        "service_name": _text(resource.get("name")) or _text(resource.get("id")),
        "service_code": _text(resource.get("code")) or None,
        "unit": _text(resource.get("unit")) or "job",
        "required_quantity": quantity,
        "reference_unit_price_halalas": round(amount * 100),
        "reference_price_complete": resource.get("unit_cost") not in (None, ""),
        "linked_to_product": not option_selected,
        "eligibility_source": "option" if option_selected else "product_live",
        "eligibility_condition": ({
            "option_id": binding.get("option_id"),
            "option_name": binding.get("option_name"),
            "value_id": binding.get("value_id"),
            "value_name": binding.get("value_name"),
        } if option_selected else None),
        "customer_selected": option_selected,
        "supplier_invoice_required": True,
        "add_to_product": False,
    }


def install_product_field_cost_support() -> None:
    import product_v2_details_routes as details_module
    import product_option_cost_routes as cost_module
    import order_option_cost_snapshot_routes as snapshot_module
    import product_v2_routes as product_v2_module
    import product_cost_setup_routes as cost_setup_module
    import product_fulfillment_rules as fulfillment_rules
    import supplier_receiving_routes as supplier_module

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
            rows = item.get("options_raw", []) if isinstance(item, dict) else getattr(item, "options_raw", None) or []
            for row in rows:
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

            fields = item.get("custom_fields", []) if isinstance(item, dict) else getattr(item, "custom_fields", None) or []
            for row in fields:
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

    original_supplier_price = supplier_module._supplier_product_reference_price
    if not getattr(original_supplier_price, "_mezan_live_invoice_costs", False):
        async def live_supplier_product_price(
            db: Any,
            *,
            user_id: str,
            piece: dict[str, Any],
            mongo_session: Any = None,
        ) -> dict[str, Any]:
            kwargs = {"session": mongo_session} if mongo_session is not None else {}
            base = await original_supplier_price(
                db,
                user_id=user_id,
                piece=piece,
                mongo_session=mongo_session,
            )
            product_id = _text(piece.get("product_id"))
            product = await db[product_v2_module.PRODUCTS].find_one(
                {
                    "user_id": user_id,
                    "$or": [
                        {"id": product_id},
                        {"mezan_product_id": product_id},
                        {"salla_product_id": product_id},
                    ],
                },
                {"_id": 0, "id": 1, "mezan_product_id": 1, "salla_product_id": 1},
                **kwargs,
            )
            if not product:
                return base
            salla_id = _text(product.get("salla_product_id")) or _text(product.get("mezan_product_id") or product.get("id"))
            product_links = await db[fulfillment_rules.PRODUCT_RESOURCE_BINDINGS].find(
                {"user_id": user_id, "salla_product_id": salla_id},
                {"_id": 0},
                **kwargs,
            ).to_list(5000)
            option_links = await db[cost_module.BINDINGS].find(
                {"user_id": user_id, "salla_product_id": salla_id},
                {"_id": 0},
                **kwargs,
            ).to_list(10000)
            resource_ids = {
                _text(row.get("resource_id"))
                for row in [*product_links, *option_links]
                if _text(row.get("resource_id"))
            }
            resources = (
                await db[cost_module.RESOURCES].find(
                    {"user_id": user_id, "id": {"$in": sorted(resource_ids)}},
                    {"_id": 0},
                    **kwargs,
                ).to_list(max(1, len(resource_ids)))
                if resource_ids else []
            )
            resource_map = {_text(row.get("id")): row for row in resources}
            component_halalas = 0
            live_services: list[dict[str, Any]] = []
            seen_service_ids: set[str] = set()
            for binding in product_links:
                resource = resource_map.get(_text(binding.get("resource_id"))) or {}
                if _text(resource.get("kind")).casefold() == "service":
                    service_id = _text(resource.get("id"))
                    if service_id and service_id not in seen_service_ids:
                        live_services.append(_live_service_row(resource, binding, option_selected=False))
                        seen_service_ids.add(service_id)
                    continue
                amount = _number(resource.get("unit_cost")) * (_number(binding.get("quantity")) or 1.0)
                component_halalas += round(amount * 100)

            tokens = snapshot_module.selected_option_tokens(_supplier_cost_item(piece))
            option_halalas = 0
            for binding in option_links:
                if not snapshot_module.binding_matches(binding, tokens):
                    continue
                if _text(binding.get("mode")).casefold() == "resource":
                    resource = resource_map.get(_text(binding.get("resource_id"))) or {}
                    if _text(resource.get("kind")).casefold() == "service":
                        service_id = _text(resource.get("id"))
                        if service_id and service_id not in seen_service_ids:
                            live_services.append(_live_service_row(resource, binding, option_selected=True))
                            seen_service_ids.add(service_id)
                        continue
                    amount = _number(resource.get("unit_cost")) * (_number(binding.get("quantity")) or 1.0)
                else:
                    amount = _number(binding.get("direct_amount"))
                option_halalas += round(amount * 100)

            total = int(base.get("reference_product_unit_price_halalas") or 0) + component_halalas + option_halalas
            return {
                **base,
                "reference_product_unit_price_halalas": total,
                "reference_product_component_cost_halalas": component_halalas,
                "reference_product_option_cost_halalas": option_halalas,
                "reference_product_price_live": True,
                "live_invoice_services": live_services,
            }
        live_supplier_product_price._mezan_live_invoice_costs = True  # type: ignore[attr-defined]
        supplier_module._supplier_product_reference_price = live_supplier_product_price

    original_recent_events = supplier_module._recent_session_events
    if not getattr(original_recent_events, "_mezan_live_invoice_costs", False):
        async def recent_events_with_live_product_state(
            db: Any,
            *,
            user_id: str,
            session_id: str,
            limit: int = 100,
            mongo_session: Any = None,
        ) -> list[dict[str, Any]]:
            rows = await original_recent_events(
                db,
                user_id=user_id,
                session_id=session_id,
                limit=limit,
                mongo_session=mongo_session,
            )
            if not rows:
                return rows
            kwargs = {"session": mongo_session} if mongo_session is not None else {}
            piece_ids = [_text(row.get("piece_id")) for row in rows if _text(row.get("piece_id"))]
            pieces = await db[supplier_module.PIECES].find(
                {"user_id": user_id, "piece_id": {"$in": piece_ids}},
                {"_id": 0},
                **kwargs,
            ).to_list(max(1, len(piece_ids)))
            piece_map = {_text(row.get("piece_id")): row for row in pieces}
            session = await db[supplier_module.SESSIONS].find_one(
                {"user_id": user_id, "id": session_id},
                {"_id": 0},
                **kwargs,
            )
            if not session:
                return rows
            service_catalog = await supplier_module._supplier_service_catalog(
                db,
                user_id=user_id,
                session=session,
                mongo_session=mongo_session,
            )
            for row in rows:
                current_piece = piece_map.get(_text(row.get("piece_id")))
                if current_piece:
                    for field in (
                        "services", "product_options", "options_raw", "options_normalized",
                        "custom_fields", "variant_id", "salla_variant_id", "sku",
                        "product_id", "product_name", "selected_image_url",
                    ):
                        if field in current_piece:
                            row[field] = current_piece.get(field)

                existing_services = supplier_module.supplier_piece_invoice_services(
                    row,
                    session,
                    service_catalog,
                )
                live_price = await supplier_module._supplier_product_reference_price(
                    db,
                    user_id=user_id,
                    piece=row,
                    mongo_session=mongo_session,
                )
                live_services = list(live_price.pop("live_invoice_services", []) or [])
                merged_services: list[dict[str, Any]] = []
                seen: set[str] = set()
                for service in [*live_services, *existing_services]:
                    service_id = _text(service.get("service_id"))
                    if not service_id or service_id in seen:
                        continue
                    seen.add(service_id)
                    merged_services.append(service)
                row["invoice_services"] = merged_services
                if row.get("product_charge_eligible") is not False:
                    row.update(live_price)
                row["supplier_invoice_live_draft"] = True
            return rows
        recent_events_with_live_product_state._mezan_live_invoice_costs = True  # type: ignore[attr-defined]
        supplier_module._recent_session_events = recent_events_with_live_product_state
