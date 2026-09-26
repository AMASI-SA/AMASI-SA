"""Transactional component reservations and physical consumption.

On-hand stock belongs only to typed warehouse occupancy items. Active unit
allocations below are the reservation authority, never a second stock balance.
All public mutations join the existing owner transaction; callers can include
their workflow transition in that same transaction. No journal is written here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from bson.decimal128 import Decimal128
from fastapi import HTTPException

from accounting_atomic import atomic_owner

PLANS = "mezan_component_consumption_plans_v1"
UNITS = "mezan_component_consumption_units_v1"
CLAIMS = "mezan_component_prebuilt_claims_v1"
LOCATIONS = "warehouse_locations"
PRODUCTS = "mezan_products_v2"
RESOURCES = "mezan_cost_resources_v2"
PRODUCT_BINDINGS = "mezan_product_resource_bindings_v2"
OPTION_BINDINGS = "mezan_product_option_cost_bindings_v2"
RECEIPTS = "mezan_inventory_receipts_v2"
AUTHORITY = "stock_component_consumption_v1"
MAX_UNITS = 10000
MAX_ROWS = 20000


def _fail(code: str, **details: Any) -> None:
    raise HTTPException(409, detail={"code": "component_" + code, **details})


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: Any) -> str:
    return " ".join(_text(value).replace("_", " ").casefold().split())


def _filled(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _id(kind: str, *parts: Any) -> str:
    return kind + "_" + _digest(parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quantity_decimal(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        _fail("quantity_invalid")
    try:
        result = value.to_decimal() if isinstance(value, Decimal128) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _fail("quantity_invalid")
    if (not result.is_finite() or result < 0 or (positive and result == 0)
            or result > Decimal("1000000000") or result.as_tuple().exponent < -6):
        _fail("quantity_invalid")
    return result


def _stored(value: Decimal) -> int | float:
    # Existing occupancy readers and Mongo $inc use ordinary BSON numbers.
    # Decimal is the arithmetic boundary; reject an unrepresentable round trip.
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if Decimal(str(number)) != value:
        _fail("quantity_precision_loss")
    return number


def _whole(value: Any) -> int:
    amount = quantity_decimal(value, positive=True)
    if amount != amount.to_integral_value() or amount > MAX_UNITS:
        _fail("unit_quantity_invalid")
    return int(amount)


def _version(value: Any) -> dict[str, Any]:
    if type(value) is int and value >= 0:
        return {"kind": "revision", "value": value}
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is not None and stamp.utcoffset() is not None:
                return {"kind": "timestamp", "value": stamp.astimezone(timezone.utc).isoformat()}
        except ValueError:
            pass
    _fail("source_version_required")


def _compare_version(incoming: dict, current: dict) -> int:
    if incoming["kind"] != current["kind"]:
        _fail("source_version_kind_conflict")
    return (incoming["value"] > current["value"]) - (incoming["value"] < current["value"])


def _timestamp(value: Any) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _fail("source_created_at_required")


async def _new_cohort(db: Any, owner: str, source_created_at: Any) -> str:
    settings = await db["settings"].find_one({"user_id": owner}) or {}
    configuration = settings.get("g47_inventory")
    cutoff = configuration.get("component_lifecycle_starts_at") if isinstance(configuration, dict) else None
    try:
        starts = _timestamp(cutoff)
    except HTTPException:
        _fail("configuration_required", setting="g47_inventory.component_lifecycle_starts_at")
    created = _timestamp(source_created_at)
    if created < starts:
        _fail("historical_backfill_forbidden")
    return created.isoformat()


def selection_tokens(line: dict[str, Any]) -> list[list[str]]:
    """Original canonical options; never PDF display replacements or current recipe."""
    tokens: set[tuple[str, str]] = set()
    rows = line.get("options_raw") or line.get("selected_options") or line.get("options") or []
    if not isinstance(rows, list):
        _fail("selected_context_invalid")
    for row in rows:
        if not isinstance(row, dict):
            _fail("selected_context_invalid")
        option = row.get("option") if isinstance(row.get("option"), dict) else {}
        oid = row.get("option_id") or row.get("id") or option.get("id")
        name = row.get("option_name") or row.get("name") or option.get("name")
        values = row.get("values") if "values" in row else row.get("value", row.get("value_name"))
        values = values if isinstance(values, list) else [values]
        for value in values:
            data = value if isinstance(value, dict) else {}
            vid = row.get("value_id") or data.get("id")
            label = data.get("name", data.get("value", data.get("label", data.get("text")))) if data else value
            if oid not in (None, ""):
                tokens.add(("option", _text(oid)))
                if name:
                    tokens.add(("option-name:" + _norm(name), "id:" + _text(oid)))
            if oid not in (None, "") and vid not in (None, ""):
                tokens.add(("id:" + _text(oid), "id:" + _text(vid)))
            if name and _filled(label):
                tokens.add(("name:" + _norm(name), "name:" + _norm(label)))
            if _filled(label) or _filled(row.get("text", row.get("answer"))):
                if oid not in (None, ""):
                    tokens.add(("id:" + _text(oid), "id:filled"))
                if name:
                    tokens.add(("name:" + _norm(name), "filled"))
    normalized = line.get("options_normalized") or {}
    if not isinstance(normalized, dict):
        _fail("selected_context_invalid")
    for key, value in normalized.items():
        for part in value if isinstance(value, list) else [value]:
            if _filled(part):
                tokens.add(("name:" + _norm(key), "name:" + _norm(part)))
                tokens.add(("name:" + _norm(key), "filled"))
    for row in line.get("custom_fields") or []:
        if not isinstance(row, dict):
            _fail("selected_context_invalid")
        field = row.get("field") if isinstance(row.get("field"), dict) else {}
        fid = row.get("field_id") or row.get("id") or field.get("id")
        name = row.get("field_name") or row.get("name") or field.get("name")
        value = row.get("value", row.get("text", row.get("answer")))
        if _filled(value):
            if fid not in (None, ""):
                tokens.add(("id:field:" + _text(fid), "id:filled"))
            if name:
                tokens.add(("name:" + _norm(name), "filled"))
    return [list(pair) for pair in sorted(tokens)]


def binding_selected(binding: dict, selected: list[list[str]]) -> bool:
    tokens = {tuple(pair) for pair in selected}
    oid, vid = _text(binding.get("option_id")), _text(binding.get("value_id"))
    named_ids = {pair[1] for pair in tokens if pair[0] == "option-name:" + _norm(binding.get("option_name"))}
    if named_ids and "id:" + oid not in named_ids:
        return False
    if vid == "__option__":
        if named_ids or ("option", oid) in tokens:
            return ("option", oid) in tokens
        return any(pair[0] == "name:" + _norm(binding.get("option_name")) for pair in tokens)
    id_tokens = {pair for pair in tokens if pair[0] == "id:" + oid and (vid == "filled" or pair[1] != "id:filled")}
    if id_tokens:
        return ("id:" + oid, "id:" + vid) in tokens
    name = "name:" + _norm(binding.get("option_name"))
    if vid == "filled":
        return (name, "filled") in tokens
    return (name, "name:" + _norm(binding.get("value_name"))) in tokens


def _lines(lines: list[dict]) -> list[dict]:
    if not isinstance(lines, list):
        _fail("lines_invalid")
    result, seen = [], set()
    for line in lines:
        if not isinstance(line, dict):
            _fail("line_identity_invalid")
        key = _text(line.get("order_line_id") or line.get("order_item_id"))
        product = _text(line.get("product_id") or line.get("salla_product_id"))
        if not key or not product or key in seen:
            _fail("line_identity_invalid")
        seen.add(key)
        entries = line.get("prebuilt_receipts") or []
        if not isinstance(entries, list) or any(not isinstance(r, dict) for r in entries):
            _fail("provenance_invalid")
        prebuilt = [{"receipt_id": _text(r.get("receipt_id")), "quantity": _whole(r.get("quantity"))} for r in entries]
        qty = _whole(line.get("quantity"))
        if (any(not r["receipt_id"] for r in prebuilt) or sum(r["quantity"] for r in prebuilt) > qty
                or len({r["receipt_id"] for r in prebuilt}) != len(prebuilt)):
            _fail("provenance_invalid")
        result.append({"order_line_id": key, "product_id": product, "quantity": qty,
                       "selected_context": selection_tokens(line),
                       "prebuilt_receipts": sorted(prebuilt, key=lambda r: r["receipt_id"])})
    if sum(row["quantity"] for row in result) > MAX_UNITS:
        _fail("unit_limit_exceeded")
    return sorted(result, key=lambda row: row["order_line_id"])


async def _rows(collection: Any, query: dict, limit: int = MAX_ROWS) -> list[dict]:
    rows = await collection.find(query).to_list(limit + 1)
    if len(rows) > limit:
        _fail("safe_scan_limit_exceeded")
    return rows


async def _recipe(db: Any, owner: str, line: dict, *, include_demands: bool = True) -> tuple[str, list[dict]]:
    product = await db[PRODUCTS].find_one({"user_id": owner, "$or": [
        {field: line["product_id"]} for field in ("id", "mezan_product_id", "salla_product_id")
    ]})
    if not product:
        _fail("product_missing", order_line_id=line["order_line_id"])
    product_id = _text(product.get("salla_product_id") or product.get("mezan_product_id") or product.get("id"))
    if not include_demands:
        return product_id, []
    base = await _rows(db[PRODUCT_BINDINGS], {"user_id": owner, "salla_product_id": product_id})
    options = await _rows(db[OPTION_BINDINGS], {"user_id": owner, "salla_product_id": product_id, "mode": "resource"})
    bindings = [("product", row) for row in base] + [
        ("option", row) for row in options if binding_selected(row, line["selected_context"])]
    demands = []
    for source, binding in bindings:
        resource_id = _text(binding.get("resource_id"))
        resource = await db[RESOURCES].find_one({"user_id": owner, "id": resource_id})
        if not resource:
            _fail("resource_missing", resource_id=resource_id)
        if resource.get("kind") == "service" or resource.get("track_inventory") is not True:
            continue
        qty = quantity_decimal(binding.get("quantity", 1), positive=True)
        binding_id = _text(binding.get("id")) or _id("binding", source, product_id, resource_id,
                                                        binding.get("option_id"), binding.get("value_id"))
        demands.append({"binding_id": source + ":" + binding_id, "resource_id": resource_id,
                        "quantity": str(qty), "selected_context_hash": _digest(line["selected_context"])})
    return product_id, sorted(demands, key=lambda row: (row["binding_id"], row["resource_id"]))


def _lot(item: dict) -> str:
    identity = _text(item.get("receipt_id") or item.get("lot_id") or item.get("id"))
    if not identity:
        _fail("stock_identity_missing")
    return identity


async def _available(db: Any, owner: str, warehouse_ids: list[str]) -> list[dict]:
    query: dict[str, Any] = {"user_id": owner, "state": {"$ne": "disabled"}}
    if warehouse_ids:
        query["warehouse_id"] = {"$in": warehouse_ids}
    locations = await _rows(db[LOCATIONS], query)
    reserved: dict[tuple[str, str, str], Decimal] = {}
    for unit in await _rows(db[UNITS], {"user_id": owner, "state": "reserved"}):
        for allocation in unit.get("allocations", []):
            key = (allocation["location_id"], allocation["lot_id"], allocation["resource_id"])
            reserved[key] = reserved.get(key, Decimal(0)) + quantity_decimal(allocation["quantity"])
    result, identities = [], set()
    for location in sorted(locations, key=lambda row: _text(row.get("id"))):
        for item in (location.get("occupancy") or {}).get("items", []):
            if item.get("item_type") != "stock_component" or not _text(item.get("resource_id")):
                continue
            key = (_text(location.get("id")), _lot(item), _text(item["resource_id"]))
            if key in identities:
                _fail("stock_identity_ambiguous")
            identities.add(key)
            available = quantity_decimal(item.get("quantity")) - reserved.get(key, Decimal(0))
            if available < 0:
                _fail("reservation_exceeds_stock", resource_id=key[2])
            result.append({"location_id": key[0], "lot_id": key[1], "resource_id": key[2], "available": available})
    included_locations = {_text(row.get("id")) for row in locations}
    if any(amount > 0 and key not in identities and (not warehouse_ids or key[0] in included_locations)
           for key, amount in reserved.items()):
        _fail("reserved_lot_missing")
    return result


async def _prebuilt(db: Any, owner: str, plan_id: str, line: dict, product_id: str) -> list[dict]:
    result = []
    for entry in line["prebuilt_receipts"]:
        receipt = await db[RECEIPTS].find_one({"user_id": owner, "id": entry["receipt_id"], "status": "posted"})
        provenance = (receipt or {}).get("component_provenance") or {}
        source_order = _text(provenance.get("source_order_id"))
        if (provenance.get("authority") != AUTHORITY or not source_order.startswith("stock-preparation:")
                or receipt.get("source_type") != "stock_preparation_order"
                or source_order != "stock-preparation:" + _text(receipt.get("source_id"))
                or provenance.get("plan_id") != _id("component_plan", owner, source_order)):
            _fail("provenance_invalid")
        references = provenance.get("units")
        if (not isinstance(references, list) or len(references) != _whole(receipt.get("quantity"))
                or entry["quantity"] > len(references)):
            _fail("provenance_invalid")
        chosen = 0
        seen = set()
        for reference in references:
            if not isinstance(reference, dict) or _text(reference.get("order_line_id")) != _text(receipt.get("source_line_id")):
                _fail("provenance_invalid")
            source_unit_id = _id("component_unit", owner, provenance["source_order_id"],
                                 reference.get("order_line_id"), reference.get("unit_index"))
            if source_unit_id in seen:
                _fail("provenance_invalid")
            seen.add(source_unit_id)
            source = await db[UNITS].find_one({"_id": source_unit_id, "user_id": owner, "state": "consumed"})
            if (not source or source.get("plan_id") != provenance["plan_id"]
                    or source.get("consumption_id") != reference.get("consumption_id")
                    or source.get("product_id") != product_id
                    or source.get("selected_context") != line["selected_context"]
                    or source.get("prebuilt")):
                _fail("provenance_invalid")
            if await db[CLAIMS].find_one({"_id": source_unit_id}):
                continue
            unit_index = len(result) + 1
            await db[CLAIMS].insert_one({"_id": source_unit_id, "user_id": owner, "plan_id": plan_id,
                                       "order_line_id": line["order_line_id"], "unit_index": unit_index})
            result.append({"source_unit_id": source_unit_id, "receipt_id": entry["receipt_id"],
                           "source_consumption_id": source["consumption_id"], "resource_demands": source["resource_demands"]})
            chosen += 1
            if chosen == entry["quantity"]:
                break
        if chosen != entry["quantity"]:
            _fail("provenance_already_claimed")
    return result


async def _public(db: Any, owner: str, plan: dict, *, duplicate: bool = False, proof_unit_ids: set | None = None) -> dict:
    rows = await _rows(db[UNITS], {"user_id": owner, "plan_id": plan["_id"]})
    rows.sort(key=lambda row: (row["order_line_id"], row["unit_index"]))
    units = [{key: row.get(key) for key in ("order_line_id", "unit_index", "state", "consumption_id", "resource_demands", "prebuilt")} for row in rows]
    consumed = [row for row in rows if row["state"] == "consumed" and (proof_unit_ids is None or row["_id"] in proof_unit_ids)]
    return {"plan_id": plan["_id"], "order_id": plan["order_id"], "state": plan["state"],
            "source_version": plan["source_version"]["value"], "duplicate": duplicate,
            "reconciliation_required": bool(plan.get("reconciliation_required")), "units": units,
            "component_provenance": {"authority": AUTHORITY, "source_order_id": plan["order_id"], "plan_id": plan["_id"],
                                     "units": [{key: row[key] for key in ("order_line_id", "unit_index", "consumption_id")} for row in consumed]}}


async def reserve_component_stock(db: Any, *, merchant_id: str, order_id: str,
                                  lines: list[dict], source_version: Any,
                                  source_created_at: Any = None,
                                  warehouse_ids: list[str] | None = None, actor_id: str = "system") -> dict:
    owner, order = _text(merchant_id), _text(order_id)
    if not owner or not order:
        _fail("owner_order_required")
    normalized = _lines(lines)
    warehouses = sorted(set(warehouse_ids or []))
    version, plan_id = _version(source_version), _id("component_plan", owner, order)
    fingerprint = _digest({"lines": normalized, "warehouse_ids": warehouses})

    async def reserve(scoped):
        current = await scoped[PLANS].find_one({"_id": plan_id, "user_id": owner})
        if current:
            comparison = _compare_version(version, current["source_version"])
            if current["state"] == "cancelled":
                _fail("reaccept_review_required", plan_id=plan_id)
            if comparison < 0:
                _fail("stale_source_version", plan_id=plan_id)
            if current.get("input_hash") != fingerprint:
                _fail("plan_conflict", plan_id=plan_id)
            if comparison > 0:
                await scoped[PLANS].update_one({"_id": plan_id}, {"$set": {"source_version": version}})
                current["source_version"] = version
            return await _public(scoped, owner, current, duplicate=True)
        created = await _new_cohort(scoped, owner, source_created_at)
        plan = {"_id": plan_id, "user_id": owner, "order_id": order, "state": "accepted", "source_created_at": created,
                "input_hash": fingerprint, "source_version": version, "lines": normalized, "warehouse_ids": warehouses,
                "created_at": _now(), "actor_id": actor_id}
        available = await _available(scoped, owner, warehouses)
        await scoped[PLANS].insert_one(plan)
        for line in normalized:
            needs_recipe = line["quantity"] > sum(row["quantity"] for row in line["prebuilt_receipts"])
            product_id, demands = await _recipe(scoped, owner, line, include_demands=needs_recipe)
            prebuilt = await _prebuilt(scoped, owner, plan_id, line, product_id)
            for index in range(1, line["quantity"] + 1):
                unit_id = _id("component_unit", owner, order, line["order_line_id"], index)
                proof = prebuilt[index - 1] if index <= len(prebuilt) else None
                unit_demands = proof["resource_demands"] if proof else demands
                allocations = []
                for demand in [] if proof else demands:
                    remaining = quantity_decimal(demand["quantity"], positive=True)
                    for lot in available:
                        if lot["resource_id"] != demand["resource_id"] or lot["available"] <= 0:
                            continue
                        taken = min(remaining, lot["available"])
                        allocations.append({"id": _id("component_allocation", owner, order, line["order_line_id"], index,
                                                         demand["binding_id"], demand["resource_id"], demand["selected_context_hash"], lot["location_id"], lot["lot_id"]),
                                            **{key: lot[key] for key in ("location_id", "lot_id", "resource_id")},
                                            "binding_id": demand["binding_id"], "quantity": str(taken)})
                        lot["available"] -= taken
                        remaining -= taken
                        if not remaining:
                            break
                    if remaining:
                        _fail("insufficient_stock", resource_id=demand["resource_id"], missing_quantity=str(remaining))
                await scoped[UNITS].insert_one({"_id": unit_id, "user_id": owner, "plan_id": plan_id, "order_id": order,
                                               "order_line_id": line["order_line_id"], "unit_index": index,
                                               "product_id": product_id, "selected_context": line["selected_context"],
                                               "resource_demands": unit_demands, "allocations": allocations,
                                               "prebuilt": proof, "state": "reserved",
                                               "consumption_id": _id("component_consumption", unit_id, unit_demands), "created_at": _now()})
        return await _public(scoped, owner, plan)
    return await atomic_owner(db, owner, reserve)


async def _selected_units(db: Any, owner: str, plan_id: str, units: dict | None) -> list[dict]:
    rows = await _rows(db[UNITS], {"user_id": owner, "plan_id": plan_id})
    if units is None:
        return rows
    if not isinstance(units, dict):
        _fail("unit_selection_invalid")
    if any(not _text(line) or not isinstance(indices, (list, tuple)) for line, indices in units.items()):
        _fail("unit_selection_invalid")
    keys = {(_text(line), _whole(index)) for line, indices in units.items() for index in indices}
    found = {(row["order_line_id"], row["unit_index"]): row for row in rows}
    if keys - found.keys():
        _fail("unit_selection_unknown")
    return [found[key] for key in sorted(keys)]


async def _deduct(db: Any, owner: str, allocations: list[dict]) -> None:
    grouped: dict[str, list[dict]] = {}
    for allocation in allocations:
        grouped.setdefault(allocation["location_id"], []).append(allocation)
    for location_id, demands in grouped.items():
        location = await db[LOCATIONS].find_one({"user_id": owner, "id": location_id, "state": {"$ne": "disabled"}})
        if not location:
            _fail("reserved_location_missing")
        before = location.get("occupancy") or {}
        after = deepcopy(before)
        total_taken = Decimal(0)
        for demand in demands:
            matches = [item for item in after.get("items", []) if item.get("item_type") == "stock_component"
                       and _text(item.get("resource_id")) == demand["resource_id"] and _lot(item) == demand["lot_id"]]
            if len(matches) != 1:
                _fail("reserved_lot_missing")
            item = matches[0]
            taken = quantity_decimal(demand["quantity"], positive=True)
            remainder = quantity_decimal(item.get("quantity")) - taken
            if remainder < 0:
                _fail("insufficient_stock", resource_id=demand["resource_id"])
            item["quantity"] = _stored(remainder)
            total_taken += taken
        total = quantity_decimal(before.get("total_quantity")) - total_taken
        if total < 0:
            _fail("stock_total_invalid")
        after["total_quantity"] = _stored(total)
        result = await db[LOCATIONS].update_one({"user_id": owner, "id": location_id, "occupancy": before},
                                              {"$set": {"occupancy": after, "updated_at": _now()}})
        if result.matched_count != 1:
            _fail("stock_conflict")


async def consume_component_stock(db: Any, *, merchant_id: str, order_id: str,
                                  units: dict | None = None, actor_id: str = "system") -> dict:
    owner, order = _text(merchant_id), _text(order_id)
    if not owner or not order:
        _fail("owner_order_required")
    plan_id = _id("component_plan", owner, order)
    async def consume(scoped):
        plan = await scoped[PLANS].find_one({"_id": plan_id, "user_id": owner})
        if not plan:
            _fail("reservation_missing")
        if plan["state"] == "cancelled":
            _fail("order_cancelled")
        rows = await _selected_units(scoped, owner, plan_id, units)
        # Detect a raw stock edit that invalidated another active reservation,
        # before committing any of this order's physical consumption.
        await _available(scoped, owner, [])
        duplicate = all(row["state"] == "consumed" for row in rows)
        for row in rows:
            if row["state"] == "consumed":
                continue
            if row["state"] != "reserved":
                _fail("unit_released")
            await _deduct(scoped, owner, row["allocations"])
            await scoped[UNITS].update_one({"_id": row["_id"], "state": "reserved"},
                                         {"$set": {"state": "consumed", "consumed_at": _now(), "actor_id": actor_id}})
        return await _public(scoped, owner, plan, duplicate=duplicate, proof_unit_ids={row["_id"] for row in rows})
    return await atomic_owner(db, owner, consume)


async def release_component_stock(db: Any, *, merchant_id: str, order_id: str,
                                  source_version: Any, units: dict | None = None,
                                  actor_id: str = "system") -> dict:
    owner, order = _text(merchant_id), _text(order_id)
    if not owner or not order:
        _fail("owner_order_required")
    version, plan_id = _version(source_version), _id("component_plan", owner, order)
    async def release(scoped):
        plan = await scoped[PLANS].find_one({"_id": plan_id, "user_id": owner})
        if plan and _compare_version(version, plan["source_version"]) < 0:
            return {**await _public(scoped, owner, plan, duplicate=True), "stale_event": True}
        if not plan:
            plan = {"_id": plan_id, "user_id": owner, "order_id": order, "state": "cancelled", "source_version": version,
                    "created_at": _now(), "actor_id": actor_id}
            await scoped[PLANS].insert_one(plan)
        rows = await _selected_units(scoped, owner, plan_id, units)
        for row in rows:
            if row["state"] != "reserved":
                continue
            if row.get("prebuilt"):
                await scoped[CLAIMS].delete_one({"_id": row["prebuilt"]["source_unit_id"], "plan_id": plan_id,
                                                "order_line_id": row["order_line_id"], "unit_index": row["unit_index"]})
            await scoped[UNITS].update_one({"_id": row["_id"], "state": "reserved"},
                                         {"$set": {"state": "released", "released_at": _now(), "actor_id": actor_id}})
        all_units = await _rows(scoped[UNITS], {"user_id": owner, "plan_id": plan_id})
        patch = {"source_version": version, "reconciliation_required": any(row["state"] == "consumed" for row in all_units),
                 "updated_at": _now()}
        if units is None:
            patch["state"] = "cancelled"
        await scoped[PLANS].update_one({"_id": plan_id}, {"$set": patch})
        return await _public(scoped, owner, {**plan, **patch})
    return await atomic_owner(db, owner, release)


async def ensure_component_consumption_indexes(db: Any) -> None:
    """Startup only; never called inside an owner transaction."""
    await db[PLANS].create_index([("user_id", 1), ("order_id", 1)], unique=True, name="uq_component_order_plan")
    await db[UNITS].create_index([("user_id", 1), ("plan_id", 1), ("order_line_id", 1), ("unit_index", 1)],
                                unique=True, name="uq_component_order_unit")
    await db[UNITS].create_index([("user_id", 1), ("state", 1)], name="ix_component_active_reservations")
