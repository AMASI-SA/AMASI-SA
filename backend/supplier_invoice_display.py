"""Read-only invoice presentation. Never use these cards for posting or close.

The grouping key is original piece identity plus the exact effective unit cost.
Existing financial line rounding is retained, not repeated per piece. Integer
rounding residue is attributed deterministically for display only and disclosed.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json

MAX_PIECES = 5000
MAX_SAFE_INTEGER = 2**53 - 1


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SAFE_INTEGER:
        raise ValueError("supplier_display_invalid_amount")
    return value


def _quantity(value):
    try:
        text = str(value)
        if len(text) > 32:
            raise ValueError("supplier_display_invalid_quantity")
        number = Decimal(text)
        if not number.is_finite() or number <= 0 or number > 1000000 or number.as_tuple().exponent < -12:
            raise ValueError("supplier_display_invalid_quantity")
        return Fraction(number)
    except (InvalidOperation, TypeError):
        raise ValueError("supplier_display_invalid_quantity") from None


def _ratio(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def _round(value):
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _allocate(total, ids):
    """Attribute existing rounded minor units; never alter a financial amount."""
    quotient, residue = divmod(total, len(ids))
    extras = set(sorted(ids)[:residue])
    return {key: quotient + int(key in extras) for key in ids}


def project_supplier_invoice_display(lines, pieces, *, expected_total_halalas=None):
    if not isinstance(lines, list) or not isinstance(pieces, list) or len(lines) > MAX_PIECES or len(pieces) > MAX_PIECES:
        raise ValueError("supplier_display_size_invalid")
    by_piece = {}
    for piece in pieces:
        key = piece.get("piece_id")
        if not isinstance(key, str) or not key.strip() or key in by_piece:
            raise ValueError("supplier_display_piece_identity_invalid")
        if not piece.get("product_id"):
            raise ValueError("supplier_display_product_identity_missing")
        by_piece[key] = piece
    seen = set(); cards = {}; total = 0
    for line_index, line in enumerate(lines):
        ids = line.get("piece_ids")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_PIECES:
            raise ValueError("supplier_display_piece_identity_invalid")
        if any(not isinstance(key, str) or key not in by_piece or key in seen for key in ids) or len(set(ids)) != len(ids):
            raise ValueError("supplier_display_piece_identity_mismatch")
        seen.update(ids)
        base = _integer(line.get("product_unit_price_halalas"))
        cost = Fraction(base)
        services = line.get("services") or []
        if not isinstance(services, list) or len(services) > 200:
            raise ValueError("supplier_display_services_invalid")
        service_ids = set(); allocations = []; line_total = base * len(ids)
        for service in services:
            identity = service.get("service_id")
            if not identity or identity in service_ids:
                raise ValueError("supplier_display_service_identity_invalid")
            service_ids.add(identity)
            unit = _integer(service.get("unit_price_halalas")) * _quantity(service.get("quantity_per_piece", 1))
            cost += unit
            # Persisted totals include rounding at the ORIGINAL financial line
            # boundary, which may predate financial line consolidation.
            service_total = (_integer(service["total_halalas"]) if "total_halalas" in service else _round(unit * len(ids)))
            allocations.append((unit, service_total, _allocate(service_total, ids)))
            line_total += service_total
        _integer(line_total)
        if "total_halalas" in line and _integer(line["total_halalas"]) != line_total:
            raise ValueError("supplier_display_source_total_mismatch")
        total += line_total
        for index, key in enumerate(ids):
            original = by_piece[key]
            identity = (str(original["product_id"]), str(original.get("sku") or ""),
                        str(original.get("variant_id") or original.get("salla_variant_id") or ""))
            group_key = json.dumps([*identity, cost.numerator, cost.denominator], ensure_ascii=False, separators=(",", ":"))
            rendered_services = []
            amount = base
            for service, (unit, source_total, allocated) in zip(services, allocations):
                amount += allocated[key]
                rendered_services.append({**deepcopy(service), "source_total_halalas":source_total,
                    "display_total_halalas":allocated[key], "effective_cost":_ratio(unit)})
            detail = {"piece_id":key, "source_key":line.get("source_key", str(line_index)),
                "source_line_index":line_index, "source_piece_index":index,
                "source":deepcopy(original), "services":rendered_services,
                "product_cost_halalas":base, "effective_cost":_ratio(cost),
                "display_total_halalas":amount, "rounding_adjustment":_ratio(Fraction(amount)-cost)}
            if group_key not in cards:
                cards[group_key] = {"key":group_key, "product_id":identity[0], "sku":identity[1],
                    "variant_id":identity[2] or None, "product_name":original.get("product_name") or line.get("product_name") or "منتج",
                    "selected_image_url":original.get("selected_image_url") or line.get("selected_image_url"),
                    "effective_cost":_ratio(cost), "quantity":0,"piece_ids":[],"pieces":[],"total_halalas":0}
            card = cards[group_key]
            card["quantity"] += 1; card["piece_ids"].append(key); card["pieces"].append(detail); card["total_halalas"] += amount
    if seen != set(by_piece):
        raise ValueError("supplier_display_piece_identity_mismatch")
    _integer(total)
    if expected_total_halalas is not None and _integer(expected_total_halalas) != total:
        raise ValueError("supplier_display_total_mismatch")
    return {"contract":"supplier-display-v1", "cards":list(cards.values()),
            "source_lines":deepcopy(lines), "piece_ids":list(by_piece), "total_halalas":total}
