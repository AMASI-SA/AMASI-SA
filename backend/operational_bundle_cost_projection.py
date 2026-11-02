"""Pure, non-posting bundle-cost exposure for the operational balances integration.

Consume the expanded, frozen per-selection leaves used by order_cost_authority;
never add the parent's catalog base/options again or re-expand today's recipe.
This module deliberately cannot turn stock consumption or supplier assignment
into a payable. Native receiving/invoice evidence still needs financial matching.
It is a projection core, NOT a source loader, importer or activation switch.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Any

VERSION = "operational-bundle-cost-projection-v1"
CENT = Decimal("0.01")
ZERO = Decimal(0)
from operational_product_cost_policy import UNRECEIVED_STATUSES



def _number(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("bundle_cost_number_invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("bundle_cost_number_invalid") from None
    if not number.is_finite() or number < 0 or number > Decimal("1e12") or (positive and not number):
        raise ValueError("bundle_cost_number_invalid")
    return number


def _money(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise ValueError("bundle_cost_identity_missing")
    return value.strip()


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _leaf_key(leaf: dict) -> str:
    # Frozen leaf_id wins. Otherwise keep every native physical discriminator;
    # identical products on separate paths are not duplicates of each other.
    if leaf.get("leaf_id"):
        return _identity(leaf["leaf_id"])
    path = leaf.get("path")
    if not isinstance(path, list) or not path or any(not isinstance(v, str) or not v for v in path):
        raise ValueError("bundle_leaf_identity_missing")
    return _hash({k: leaf.get(k) for k in (
        "path", "kind", "product_id", "resource_id", "variant_id", "sourcing_mode", "supplier_id")})


def _selections(values: Any) -> set[tuple[str, str]]:
    if not isinstance(values, list):
        raise ValueError("bundle_selection_evidence_missing")
    result = set()
    for row in values:
        if not isinstance(row, dict) or row.get("option_id") in (None, "") or row.get("value_id") in (None, ""):
            raise ValueError("bundle_selection_evidence_invalid")
        result.add((str(row["option_id"]), str(row["value_id"])))
    return result


def _unresolved(code: str) -> dict:
    return {"version": VERSION, "currency": "SAR", "complete": False,
            "posting_allowed": False, "rows": [], "issues": [{"code": code}],
            "recipe_cost_sar": None, "known_cost_sar": "0.00",
            "unreceived_expected_sar": None}


def project_bundle_costs(*, owner: str, order_number: str, order_item_id: str,
                         product_id: str, sale_quantity: Any, status: Any,
                         selected_options: list[dict], snapshot: dict,
                         native_units: list[dict] | None = None) -> dict:
    """Project a *resolved* frozen recipe, preserving evidence gaps explicitly.

    The source adapter must first match the exact owner/order/line and validate
    the shared authority's line fingerprint (including text/variant changes).
    selected_options are normalized native IDs, not arbitrary display labels.
    Native units must be scoped to this line and be the current, unreleased set.
    Received/consumed/stock-linked leaves are deliberately withheld from new
    supplier estimates until their native receipts/invoices are matched. This
    is a visible blocker, not an implicit zero or a historical financial import.
    """
    try:
        owner, order_number, order_item_id, product_id = map(
            _identity, (owner, order_number, order_item_id, product_id))
        q = _number(sale_quantity, positive=True)
        if q != q.to_integral_value() or q > 10000:
            raise ValueError("bundle_sale_quantity_invalid")
        if not isinstance(snapshot, dict) or str(snapshot.get("product_id") or "") != product_id:
            raise ValueError("bundle_snapshot_product_mismatch")
        if "reporting_cost" in snapshot:
            # The newer dashboard policy can add a financial base/service that
            # is deliberately absent from physical leaves. Counting those leaves
            # alone would silently understate remaining cost. Never fabricate a
            # physical unit or supplier debt to make its receipt coverage fit.
            raise ValueError("bundle_financial_policy_operational_mapping_required")
        if snapshot.get("historical_cost_verified") is False:
            raise ValueError("bundle_historical_cost_unproven")
        if snapshot.get("sale_quantity") is not None and _number(snapshot["sale_quantity"]) != q:
            raise ValueError("bundle_snapshot_quantity_mismatch")
        if (_selections(snapshot.get("selected_options")) != _selections(selected_options)
                or snapshot.get("needs_selection") or snapshot.get("unresolved_components")):
            raise ValueError("bundle_selected_options_unresolved")
        if snapshot.get("cost_complete") is False:
            raise ValueError("bundle_cost_incomplete")
        if str(snapshot.get("cost_currency", "SAR")).upper() != "SAR":
            raise ValueError("bundle_cost_currency_requires_review")
        leaves = snapshot.get("leaves")
        if not isinstance(leaves, list) or not leaves or len(leaves) * q > 10000:
            raise ValueError("bundle_leaf_coverage_incomplete")
        recipe = []
        seen = set()
        for leaf in leaves:
            if not isinstance(leaf, dict) or leaf.get("kind") not in {"product", "resource"}:
                raise ValueError("bundle_leaf_shape_invalid")
            key = _leaf_key(leaf)
            if key in seen:
                raise ValueError("bundle_leaf_identity_duplicate")
            seen.add(key)
            if str(leaf.get("cost_currency", "SAR")).upper() != "SAR":
                raise ValueError("bundle_cost_currency_requires_review")
            amount = _number(leaf.get("unit_cost")) * _number(leaf.get("quantity"), positive=True)
            recipe.append((key, leaf, amount))
        per_selection = sum((value for _, _, value in recipe), ZERO)
        if _number(snapshot.get("estimated_cost")) != per_selection:
            raise ValueError("bundle_leaf_total_mismatch")
        unit_map, unit_states = {}, {}
        if native_units is not None:
            if not isinstance(native_units, list) or len(native_units) != int(q):
                raise ValueError("bundle_unit_coverage_incomplete")
            for unit in native_units:
                if not isinstance(unit, dict) or unit.get("user_id") != owner:
                    raise ValueError("bundle_unit_owner_mismatch")
                if (str(unit.get("order_id")) != order_number
                        or str(unit.get("order_line_id")) != order_item_id):
                    raise ValueError("bundle_unit_scope_mismatch")
                index = unit.get("unit_index")
                if type(index) is not int or index < 1 or index > int(q) or index in unit_map:
                    raise ValueError("bundle_unit_identity_ambiguous")
                if unit.get("state") not in {"reserved", "consumed"}:
                    raise ValueError("bundle_unit_state_unresolved")
                actual = unit.get("bundle_leaves")
                if not isinstance(actual, list) or not all(isinstance(v, dict) for v in actual):
                    raise ValueError("bundle_unit_leaf_coverage_incomplete")
                keys = [_leaf_key(v) for v in actual]
                if len(keys) != len(set(keys)) or set(keys) != seen:
                    raise ValueError("bundle_unit_leaf_coverage_incomplete")
                actual_by_key = dict(zip(keys, actual))
                for key, expected, _ in recipe:
                    observed = actual_by_key[key]
                    for field in ("kind", "product_id", "salla_product_id", "mezan_product_id", "resource_id", "variant_id"):
                        if str(observed.get(field) or "") != str(expected.get(field) or ""):
                            raise ValueError("bundle_unit_leaf_identity_mismatch")
                    if _number(observed.get("quantity"), positive=True) != _number(expected["quantity"], positive=True):
                        raise ValueError("bundle_unit_leaf_quantity_mismatch")
                unit_map[index] = actual_by_key
                unit_states[index] = unit["state"]
    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
        code = str(exc) if str(exc).startswith("bundle_") else "bundle_cost_evidence_invalid"
        return _unresolved(code)

    raw_status = status.get("slug") or status.get("name") if isinstance(status, dict) else status
    eligible = str(raw_status or "").strip().lower() in UNRECEIVED_STATUSES
    result, issues, subtotal, expected_total = [], [], ZERO, ZERO
    prior_rounded = ZERO
    for index in range(1, int(q) + 1):
        for key, leaf, value in sorted(recipe, key=lambda v: v[0]):
            observed = unit_map.get(index, {}).get(key)
            stage = "unreceived" if eligible else "outside_unreceived_status"
            if observed is not None:
                try:
                    received = _number(observed.get("received_quantity", "0"))
                    consumed = _number(observed.get("consumed_quantity", "0"))
                    reserved = _number(observed.get("reserved_quantity", "0"))
                    if any(v > _number(leaf["quantity"]) for v in (received, consumed, reserved)):
                        raise ValueError("bundle_unit_quantity_exceeds_recipe")
                    segments = observed.get("sourcing_segments") or []
                    if not isinstance(segments, list) or any(not isinstance(v, dict) for v in segments):
                        raise ValueError("bundle_source_segments_invalid")
                    # Segment costs are already rolled up by their parent leaf.
                    # Do not add them to the recipe or multiply the sale again.
                    if (received or consumed or reserved or observed.get("receipt_ids") or segments
                            or unit_states.get(index) == "consumed"
                            or observed.get("state") in {"received", "consumed", "reserved"}):
                        stage = "financial_link_review_required"
                        issues.append({"code": "bundle_native_financial_link_required",
                                       "unit_index": index, "leaf_id": key})
                except (ValueError, TypeError, InvalidOperation) as exc:
                    stage = "financial_link_review_required"
                    issues.append({"code": str(exc), "unit_index": index, "leaf_id": key})
            # Deterministic cumulative allocation preserves the rounded line sum,
            # even when a frozen resource unit cost has sub-halalah precision.
            subtotal += value
            rounded = subtotal.quantize(CENT, rounding=ROUND_HALF_UP)
            allocated, prior_rounded = rounded - prior_rounded, rounded
            if stage == "unreceived":
                expected_total += allocated
            result.append({
                "id": "bundle-cost:" + _hash([owner, order_number, order_item_id, index, key]),
                "order_number": order_number, "order_item_id": order_item_id,
                "unit_index": index, "leaf_id": key, "kind": leaf["kind"],
                "product_id": leaf.get("product_id"), "resource_id": leaf.get("resource_id"),
                "variant_id": leaf.get("variant_id"), "quantity": str(_number(leaf["quantity"])),
                "cost_currency": "SAR", "cost_sar": _money(allocated), "stage": stage,
                "supplier_id": None, "confirmed_payable_sar": None,
                "source_revision": snapshot.get("revision"),
            })
    return {"version": VERSION, "currency": "SAR", "complete": not issues,
            "posting_allowed": False, "rows": result, "issues": issues,
            "recipe_cost_sar": _money(per_selection * q), "known_cost_sar": _money(subtotal),
            "unreceived_expected_sar": None if issues else _money(expected_total),
            "known_unreceived_subtotal_sar": _money(expected_total),
            "unreceived_status_eligible": eligible}
