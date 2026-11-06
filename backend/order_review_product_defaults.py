"""Owner/product-scoped review defaults. Templates never retain customer text."""
from copy import deepcopy
import hashlib
import json

from pymongo.errors import DuplicateKeyError

DEFAULTS = "order_review_product_option_defaults"


def _base():
    import order_review_routes
    return order_review_routes


def product_key(item):
    from order_review_export_controls import preparation_assignment_product_key
    key = preparation_assignment_product_key(item)
    return key if key.startswith(("product:", "sku:")) else None


def specifications(item):
    base = _base()
    result = {}
    for option in getattr(item, "options", None) or []:
        name, value = base._text(getattr(option, "name", None)), base._text(getattr(option, "value", None))
        if name and value:
            result[base._normalized(name)] = {"key": base._normalized(name), "name": name, "value": value}
    for name in ("color", "size", "material"):
        value = base._text(getattr(item, name, None))
        if value:
            result.setdefault(name, {"key": name, "name": name, "value": value})
    for row in getattr(item, "custom_fields", None) or []:
        if not isinstance(row, dict):
            continue
        name = next((base._text(row.get(k)) for k in ("name", "label", "title", "question", "key") if base._text(row.get(k))), "")
        raw = next((row[k] for k in ("value", "answer", "selected", "choice", "text", "response") if row.get(k)), "")
        value = base._text(raw.get("name") if isinstance(raw, dict) else raw)
        if name and value:
            result.setdefault(base._normalized(name), {"key": base._normalized(name), "name": name, "value": value})
    return result


async def _storage(db):
    await db[DEFAULTS].create_index([("user_id", 1), ("product_key", 1)], unique=True)


async def remember_hidden(db, *, user_id, actor_id, item, keys):
    key = product_key(item)
    if not key:
        return
    base = _base()
    await _storage(db)
    await db[DEFAULTS].update_one({"user_id": user_id, "product_key": key}, {
        "$set": {"manual_hidden_spec_keys": sorted(set(keys)), "updated_at": base._now(), "updated_by": actor_id},
        "$inc": {"revision": 1},
    }, upsert=True)


def template_for(item, card):
    base = _base()
    key = product_key(item)
    specs = card.get("linked_specs") or []
    keys = sorted({base._normalized(s.get("key") or s.get("name")) for s in specs if isinstance(s, dict)} - {""})
    if not key or not keys:
        return None
    conditions = {base._normalized(s.get("key") or s.get("name")): base._normalized(s.get("value"))
        for s in specs if isinstance(s, dict) and not base._is_personal_option(base._text(s.get("name") or s.get("key")))}
    identity = json.dumps([key, base._normalized(card.get("name")), keys, conditions], ensure_ascii=False, sort_keys=True)
    template_id = card.get("default_template_id") or hashlib.sha256(identity.encode()).hexdigest()[:32]
    return {"id": template_id, "product_key": key, "name": base._text(card.get("name")),
        "linked_spec_keys": keys, "conditions": conditions,
        "image_url": base._text(card.get("image_url")) or None}


async def remember_operational(db, *, user_id, actor_id, item, card):
    template = template_for(item, card)
    if not template:
        return
    await _storage(db)
    selector = {"user_id": user_id, "product_key": template["product_key"]}
    # Atomic field paths avoid lost updates when reviewers save different rules.
    await db[DEFAULTS].update_one(selector, {"$set": {
        f"operational_templates.{template['id']}": template,
        "updated_at": _base()._now(), "updated_by": actor_id,
    }, "$inc": {"revision": 1}}, upsert=True)


async def forget_operational(db, *, user_id, card):
    key, identity = card.get("default_product_key"), card.get("default_template_id")
    if key and identity:
        await db[DEFAULTS].update_one({"user_id": user_id, "product_key": key}, {
            "$unset": {f"operational_templates.{identity}": ""}, "$inc": {"revision": 1},
        })


async def apply_product_defaults(db, *, user_id, order, identities):
    """Materialize pending-review metadata once, before the approval read snapshot.

    No stock, source/provider, financial or approved workflow mutation occurs.
    Normal workflow revision CAS also protects parallel page openings.
    """
    base = _base()
    selector = {"user_id": user_id, "order_number": order.order_number}
    workflow = await db[base.WORKFLOWS].find_one(selector, {"_id": 0})
    if (workflow or {}).get("stage") in base.REVIEW_COMPLETED_STAGES:
        return
    keys = sorted({product_key(item) for item in identities} - {None})
    if not keys:
        return
    defaults = {row["product_key"]: row for row in await db[DEFAULTS].find(
        {"user_id": user_id, "product_key": {"$in": keys}}, {"_id": 0}).to_list(len(keys))}
    if not defaults:
        return
    states, cards = base._state_map(workflow), deepcopy((workflow or {}).get("operational_items") or [])
    changed = False
    now = base._now()
    for item in identities:
        item_changed = False
        key = product_key(item)
        setting = defaults.get(key) or {}
        state = states.get(item.order_item_id, {"order_item_id": item.order_item_id, "review_status": "pending_review"})
        if "manual_hidden_spec_keys" in setting and "manual_supplier_export_excluded_spec_keys" not in state:
            state["manual_supplier_export_excluded_spec_keys"] = list(setting["manual_hidden_spec_keys"])
            item_changed = True
        current = specifications(item)
        for template in (setting.get("operational_templates") or {}).values():
            linked_keys = template["linked_spec_keys"]
            if not all(k in current for k in linked_keys) or any(
                base._normalized(current[k]["value"]) != v for k, v in template["conditions"].items()):
                continue
            if any(c.get("source_order_item_id") == item.order_item_id and c.get("default_template_id") == template["id"] for c in cards):
                continue
            identity = "op:" + hashlib.sha256(f"{order.order_number}|{item.order_item_id}|{template['id']}".encode()).hexdigest()[:32]
            cards.append({"operational_item_id": identity, "default_template_id": template["id"], "default_product_key": key,
                "name": template["name"], "image_url": template.get("image_url"), "image_source": "product_option_default",
                "item_type": "internal_operational", "quantity": 1, "source_order_item_id": item.order_item_id,
                "source_product_name": base._text(getattr(item, "name", None)),
                "linked_specs": [deepcopy(current[k]) for k in linked_keys], "preparation_status": "pending",
                "blocks_order_completion": True, "supplier_export": False, "salla_product": False,
                "financial_item": False, "created_at": now, "created_by": "product_option_default"})
            item_changed = True
        if item_changed:
            changed = True
            from order_review_export_controls import operational_excluded_keys
            operational = operational_excluded_keys(cards, item.order_item_id)
            state["supplier_export_excluded_spec_keys"] = sorted(set(state.get("manual_supplier_export_excluded_spec_keys") or []) | set(operational))
            state["moved_to_operational_item_ids"] = [c["operational_item_id"] for c in cards if c.get("source_order_item_id") == item.order_item_id]
            states[item.order_item_id] = state
    if not changed:
        return
    revision = int((workflow or {}).get("revision") or 0)
    document = {**(workflow or {}), **selector, "order_id": order.order_id, "stage": "pending_review",
        "revision": revision + 1, "items": list(states.values()), "operational_items": cards, "updated_at": now}
    if workflow:
        await db[base.WORKFLOWS].replace_one({**selector, "revision": revision}, document)
    else:
        document["created_at"] = now
        # Production already maintains this unique key; ensure it for bare fixtures.
        await db[base.WORKFLOWS].create_index([("user_id", 1), ("order_number", 1)], unique=True)
        try:
            await db[base.WORKFLOWS].insert_one(document)
        except DuplicateKeyError:
            pass  # Snapshot below reads the actual winning workflow/revision.
