"""Presentation evidence only; never reprice the selected cost authority."""
import hashlib
import json
from decimal import Decimal

from order_cost_authority import decimal, _economic_tokens
from report_option_recovery import report_cost_item


def detail_group(cost, item, quantity):
    complete = bool(cost.get("calculation_cost_available"))
    known = decimal(cost.get("line_total")) or Decimal(0)
    cells = [dict(cell) for cell in cost.get("cost_detail_cells") or []]
    if complete and not cells and cost.get("applied_product_resources") is not None and cost.get("applied_option_costs") is not None:
        # Legacy ordinary snapshots already retain exact per-unit amounts.
        # Recover those amounts, not costs read from today's catalog.
        base = decimal(cost.get("base_total"))
        components = decimal(cost.get("components_total"))
        saved = []
        invalid_saved = False
        for kind, records in (("component", cost["applied_product_resources"]), ("service", cost["applied_option_costs"])):
            for record in records:
                amount = decimal(record.get("resolved_amount"))
                if amount is None:
                    invalid_saved = True
                    break
                identity = str(record.get("binding_id") or record.get("resource_id") or len(saved))
                saved.append({"key": "saved:" + kind + ":" + identity, "label": identity,
                              "kind": kind, "status": "costed", "unit_cost": float(amount),
                              "resource_id": record.get("resource_id")})
        if not invalid_saved and base is not None and components is not None and sum((decimal(c["unit_cost"]) for c in saved), Decimal(0)) * quantity == components:
            cells = [{"key": "base", "label": "المنتج (تكلفة محفوظة)", "kind": "product", "status": "costed", "unit_cost": float(base / quantity)}, *saved]
    if not cells:
        # Old frozen records may contain only totals. Display their actual
        # evidence explicitly, never invent a split using today's catalog.
        base = decimal(cost.get("base_total"))
        components = decimal(cost.get("components_total"))
        if complete and base is not None and components is not None and base + components == known:
            cells = [{"key": "base", "label": "المنتج (تكلفة محفوظة)", "kind": "product",
                      "status": "costed", "unit_cost": float(base / quantity)}]
            if components:
                cells.append({"key": "historical-components", "label": "خدمات ومكونات محفوظة — تفصيل غير متاح",
                              "kind": "component", "status": "costed", "unit_cost": float(components / quantity)})
        else:
            cells = [{"key": "unresolved-cost", "label": "بيانات التكلفة",
                      "kind": "product", "status": "unresolved", "unit_cost": None}]
    signature = {"cells": sorted(cells, key=lambda c: c["key"]),
                 "choices": _economic_tokens(report_cost_item(item)),
                 "authority": cost.get("cost_authority"), "stage": cost.get("cost_stage"),
                 "complete": complete, "unit": str(known / quantity),
                 "reasons": sorted(cost.get("missing_cost_reasons") or [])}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"key": key, "cells": cells, "quantity": float(quantity),
            "unit_cost": float(known / quantity) if complete else None,
            "total_cost": float(known) if complete else None,
            "known_unit_cost": float(known / quantity), "known_total_cost": float(known),
            "complete": complete, "cost_stage": cost.get("cost_stage"),
            "missing_cost_reasons": cost.get("missing_cost_reasons") or []}


def detail_groups(cost, item, quantity):
    """Split proven actual units only when their coverage matches authority."""
    entries = cost.get("cost_detail_units") or []
    if entries and cost.get("calculation_cost_available"):
        counts = [decimal(entry.get("quantity")) for entry in entries]
        amounts = [decimal(entry.get("line_total")) for entry in entries]
        if (all(count is not None and count > 0 for count in counts)
                and all(amount is not None for amount in amounts)
                and sum(counts, Decimal(0)) == quantity
                and sum(amounts, Decimal(0)) == decimal(cost.get("line_total"))
                and all(entry.get("cost_detail_cells") for entry in entries)):
            return [detail_group({**cost, **entry}, item, count)
                    for entry, count in zip(entries, counts)]
    return [detail_group(cost, item, quantity)]


def add_group(row, group):
    previous = row["_groups"].get(group["key"])
    if previous is None:
        row["_groups"][group["key"]] = group
    else:
        previous["quantity"] += group["quantity"]
        previous["known_total_cost"] = float(decimal(previous["known_total_cost"]) + decimal(group["known_total_cost"]))
        if previous["complete"]:
            previous["total_cost"] = previous["known_total_cost"]
    row["_reasons"].update(group["missing_cost_reasons"])


def finish_groups(row, resources=None):
    groups = list(row.pop("_groups").values())
    columns = {}
    for group in groups:
        for cell in group["cells"]:
            resource = (resources or {}).get(str(cell.get("resource_id") or "")) or {}
            if resource.get("name"):
                cell["label"] = resource["name"]
                if resource.get("kind") == "service":
                    cell["kind"] = "service"
            columns.setdefault(cell["key"], {k: cell[k] for k in ("key", "label", "kind")})
    row["cost_columns"] = list(columns.values())
    row["cost_groups"] = groups
    row["missing_cost_reasons"] = sorted(row.pop("_reasons"))
    row["unit_cost_varies"] = len({g["unit_cost"] for g in groups}) > 1
