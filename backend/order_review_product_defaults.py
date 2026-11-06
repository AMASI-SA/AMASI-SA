"""Owner/product-scoped review defaults. Templates never retain customer text."""
from copy import deepcopy
import hashlib
import json
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern

DEFAULTS = "order_review_product_option_defaults"
ACTION_CLOCKS = "order_review_product_default_action_clocks"
WORKFLOWS = "order_review_workflows"
_READY = set()


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
    identity = (id(db.client), db.name)
    if identity in _READY:
        return
    await db[DEFAULTS].create_index([("user_id", 1), ("product_key", 1)], unique=True)
    await db[WORKFLOWS].create_index([("user_id", 1), ("default_actions.state", 1),
        ("default_actions.product_key", 1)], name="ix_product_default_outbox")
    _READY.add(identity)


def _pending():
    return HTTPException(503, detail={"code": "review_default_settings_pending", "retry_post": False,
        "reconcile": "get_only", "message": "لم يتأكد حفظ إعداد المنتج بعد. أعد تحميل الطلب لإكماله."})


async def _action(db, *, user_id, actor_id, key, kind, **values):
    """Reserve only an ordering number, before the business workflow CAS.

    Gaps after a rejected workflow CAS are harmless. A majority-journaled clock
    avoids replica-clock ordering and lets later cancellations fence old puts.
    No setting, approval fact, provider or financial data changes here.
    """
    identity = hashlib.sha256(json.dumps([user_id, key], ensure_ascii=False).encode()).hexdigest()
    try:
        await _storage(db)
        clock = await db[ACTION_CLOCKS].with_options(write_concern=WriteConcern("majority", j=True)).find_one_and_update(
            {"_id": identity, "user_id": user_id, "product_key": key}, {"$inc": {"sequence": 1}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except PyMongoError as exc:
        raise _pending() from exc
    return {"id": uuid.uuid4().hex, "kind": kind, "product_key": key, "actor_id": actor_id,
        "sequence": clock["sequence"], "state": "pending", **deepcopy(values)}


async def hidden_action(db, *, user_id, actor_id, item, previous_keys, keys):
    key = product_key(item)
    before, after = set(previous_keys), set(keys)
    if not key or before == after:
        return None
    return await _action(db, user_id=user_id, actor_id=actor_id, key=key, kind="hidden_delta",
        add_keys=sorted(after - before), remove_keys=sorted(before - after))


def _conditions(specs):
    base = _base()
    conditions = {}
    yes_no = {"نعم", "لا", "yes", "no", "true", "false", "1", "0"}
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        name = base._text(spec.get("name") or spec.get("key"))
        value = base._normalized(spec.get("value"))
        question = base._normalized(name).startswith(("هل ", "do ", "would ", "want "))
        if not base._is_personal_option(name) or (question and value in yes_no):
            conditions[base._normalized(spec.get("key") or name)] = value
    return conditions


def template_for(item, card):
    base = _base()
    key = product_key(item)
    specs = card.get("linked_specs") or []
    keys = sorted({base._normalized(s.get("key") or s.get("name")) for s in specs if isinstance(s, dict)} - {""})
    if not key or not keys:
        return None
    conditions = _conditions(specs)
    identity = json.dumps([key, base._normalized(card.get("name")), keys, conditions], ensure_ascii=False, sort_keys=True)
    template_id = card.get("default_template_id") or hashlib.sha256(identity.encode()).hexdigest()[:32]
    return {"id": template_id, "product_key": key, "name": base._text(card.get("name")),
        "linked_spec_keys": keys, "conditions": conditions,
        "image_url": base._text(card.get("image_url")) or None}


async def operational_action(db, *, user_id, actor_id, item, card, allow_create):
    template = template_for(item, card)
    if not template:
        return None
    return await _action(db, user_id=user_id, actor_id=actor_id, key=template["product_key"],
        kind="template_put", template=template, allow_create=allow_create)


async def forget_action(db, *, user_id, actor_id, card):
    key, identity = card.get("default_product_key"), card.get("default_template_id")
    if key and identity:
        return await _action(db, user_id=user_id, actor_id=actor_id, key=key,
            kind="template_forget", template_id=identity)
    return None


def append_action(workflow, action):
    return [*deepcopy((workflow or {}).get("default_actions") or []), *([action] if action else [])]


async def _apply_action(db, *, user_id, action):
    selector = {"user_id": user_id, "product_key": action["product_key"]}
    for _ in range(5):
        current = await db[DEFAULTS].find_one(selector) or {}
        if action["id"] in (current.get("applied_actions") or {}):
            return
        sequence = action["sequence"]
        patch = {"updated_at": _base()._now(), "updated_by": action["actor_id"]}
        outcome = "superseded"
        if action["kind"] == "hidden_delta":
            values = set(current.get("manual_hidden_spec_keys") or [])
            stamps = dict(current.get("hidden_action_stamps") or {})
            for keys, hidden in ((action["add_keys"], True), (action["remove_keys"], False)):
                for key in keys:
                    digest = hashlib.sha256(key.encode()).hexdigest()
                    if sequence > stamps.get(digest, 0):
                        values.add(key) if hidden else values.discard(key)
                        stamps[digest] = sequence
                        outcome = "applied"
            patch.update(manual_hidden_spec_keys=sorted(values), hidden_action_stamps=stamps)
        elif action["kind"] in {"template_put", "template_forget"}:
            template = action.get("template") or {}
            identity = template.get("id") or action["template_id"]
            templates = deepcopy(current.get("operational_templates") or {})
            stamps = dict(current.get("operational_action_stamps") or {})
            if sequence > stamps.get(identity, 0):
                if action["kind"] == "template_forget":
                    templates.pop(identity, None)
                    outcome = "applied"
                elif action.get("allow_create") is True or identity in templates:
                    templates[identity] = template
                    outcome = "applied"
                else:
                    outcome = "inactive"
                stamps[identity] = sequence
            patch.update(operational_templates=templates, operational_action_stamps=stamps)
        else:
            raise _pending()
        # The receipt and rule/tombstone change share the same atomic CAS. An
        # ACK lost before workflow receipt marking can only read this result.
        patch[f"applied_actions.{action['id']}"] = {"sequence": sequence, "outcome": outcome}
        revision = current.get("revision")
        match = {**selector, "revision": revision if revision is not None else {"$exists": False}}
        try:
            changed = await db[DEFAULTS].update_one(match, {"$set": patch, "$inc": {"revision": 1}}, upsert=not current)
            if changed.matched_count or changed.upserted_id is not None:
                return
        except DuplicateKeyError:
            continue
    raise _pending()


async def flush_default_actions(db, *, user_id, product_keys):
    """Recover all applicable durable actions before issuing another approval.

    Actions contain field keys/configuration only. Per-key sequence stamps make
    cross-order reordering safe and preserve independently changed fields.
    """
    if not product_keys:
        return
    await _storage(db)
    try:
        rows = await db[WORKFLOWS].find({"user_id": user_id, "default_actions": {"$elemMatch": {
            "state": "pending", "product_key": {"$in": list(product_keys)}}}},
            {"_id": 0, "order_number": 1, "default_actions": 1}).to_list(1001)
        if len(rows) > 1000:
            raise _pending()
        pending = [(row["order_number"], action) for row in rows for action in row.get("default_actions") or []
            if action.get("state") == "pending" and action.get("product_key") in product_keys]
        for number, action in sorted(pending, key=lambda pair: pair[1]["sequence"]):
            await _apply_action(db, user_id=user_id, action=action)
            await db[WORKFLOWS].update_one({"user_id": user_id, "order_number": number,
                "default_actions.id": action["id"]}, {"$set": {"default_actions.$[action].state": "applied"}},
                array_filters=[{"action.id": action["id"]}])
    except PyMongoError as exc:
        raise _pending() from exc


def _automatic(card):
    return card.get("default_auto_generated") is True or card.get("created_by") == "product_option_default"


def _matches(template, current):
    keys = template.get("linked_spec_keys") or []
    return bool(keys) and all(key in current for key in keys) and all(
        _base()._normalized(current[key]["value"]) == value for key, value in (template.get("conditions") or {}).items())


def assert_pending_auto_cards_current(workflow, identities):
    """The signed display snapshot must not approve a stale automatic value."""
    if not workflow or workflow.get("stage") in _base().REVIEW_COMPLETED_STAGES:
        return
    current = {item.order_item_id: specifications(item) for item in identities}
    for card in workflow.get("operational_items") or []:
        if not _automatic(card):
            continue
        specs = current.get(card.get("source_order_item_id"), {})
        keys = [row.get("key") for row in card.get("linked_specs") or []]
        template = {"linked_spec_keys": keys, "conditions": card.get("default_conditions", _conditions(card.get("linked_specs") or []))}
        if not _matches(template, specs) or card.get("linked_specs") != [specs[key] for key in keys]:
            raise HTTPException(409, detail={"code": "review_defaults_source_changed", "refresh_required": True})


async def apply_product_defaults(db, *, user_id, order, identities, _attempt=0):
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
    await flush_default_actions(db, user_id=user_id, product_keys=keys)
    # Receipt marking can have changed workflow metadata without its review
    # revision; preserve the actual durable action states in the replacement.
    workflow = await db[base.WORKFLOWS].find_one(selector, {"_id": 0})
    if (workflow or {}).get("stage") in base.REVIEW_COMPLETED_STAGES:
        return
    defaults = {row["product_key"]: row for row in await db[DEFAULTS].find(
        {"user_id": user_id, "product_key": {"$in": keys}}, {"_id": 0}).to_list(len(keys))}
    states, cards = base._state_map(workflow), deepcopy((workflow or {}).get("operational_items") or [])
    if not defaults and not any(_automatic(card) for card in cards):
        return
    changed = False
    now = base._now()
    for item in identities:
        item_changed = False
        key = product_key(item)
        setting = defaults.get(key) or {}
        state = states.get(item.order_item_id, {"order_item_id": item.order_item_id, "review_status": "pending_review"})
        from order_review_export_controls import operational_excluded_keys
        old_operational = set(operational_excluded_keys(cards, item.order_item_id))
        manual = list(state["manual_supplier_export_excluded_spec_keys"] or []) if "manual_supplier_export_excluded_spec_keys" in state else list(
            set(state.get("supplier_export_excluded_spec_keys") or []) - old_operational)
        if "manual_hidden_spec_keys" in setting and "manual_supplier_export_excluded_spec_keys" not in state:
            state["manual_supplier_export_excluded_spec_keys"] = list(setting["manual_hidden_spec_keys"])
            manual = list(setting["manual_hidden_spec_keys"])
            item_changed = True
        current = specifications(item)
        kept = []
        for card in cards:
            if card.get("source_order_item_id") != item.order_item_id or not _automatic(card):
                kept.append(card); continue
            linked_keys = [row["key"] for row in card.get("linked_specs") or []]
            condition = card.get("default_conditions", _conditions(card.get("linked_specs") or []))
            template = {"linked_spec_keys": linked_keys, "conditions": condition}
            if not _matches(template, current):
                item_changed = True
                continue
            fresh = [deepcopy(current[k]) for k in linked_keys]
            if card.get("linked_specs") != fresh or "default_conditions" not in card:
                card.update(linked_specs=fresh, default_conditions=deepcopy(condition))
                item_changed = True
            kept.append(card)
        cards = kept
        for template in (setting.get("operational_templates") or {}).values():
            linked_keys = template["linked_spec_keys"]
            if not _matches(template, current):
                continue
            if any(c.get("source_order_item_id") == item.order_item_id and c.get("default_template_id") == template["id"] for c in cards):
                continue
            identity = "op:" + hashlib.sha256(f"{order.order_number}|{item.order_item_id}|{template['id']}".encode()).hexdigest()[:32]
            cards.append({"operational_item_id": identity, "default_template_id": template["id"], "default_product_key": key,
                "default_auto_generated": True, "default_conditions": deepcopy(template["conditions"]),
                "name": template["name"], "image_url": template.get("image_url"), "image_source": "product_option_default",
                "item_type": "internal_operational", "quantity": 1, "source_order_item_id": item.order_item_id,
                "source_product_name": base._text(getattr(item, "name", None)),
                "linked_specs": [deepcopy(current[k]) for k in linked_keys], "preparation_status": "pending",
                "blocks_order_completion": True, "supplier_export": False, "salla_product": False,
                "financial_item": False, "created_at": now, "created_by": "product_option_default"})
            item_changed = True
        if item_changed:
            changed = True
            operational = operational_excluded_keys(cards, item.order_item_id)
            state["manual_supplier_export_excluded_spec_keys"] = sorted(manual)
            state["supplier_export_excluded_spec_keys"] = sorted(set(manual) | set(operational))
            state["moved_to_operational_item_ids"] = [c["operational_item_id"] for c in cards if c.get("source_order_item_id") == item.order_item_id]
            states[item.order_item_id] = state
    if not changed:
        return
    revision = int((workflow or {}).get("revision") or 0)
    document = {**(workflow or {}), **selector, "order_id": order.order_id, "stage": "pending_review",
        "revision": revision + 1, "items": list(states.values()), "operational_items": cards, "updated_at": now}
    if workflow:
        changed = await db[base.WORKFLOWS].replace_one({**selector, "revision": revision}, document)
        if not changed.matched_count:
            if _attempt >= 3:
                raise HTTPException(409, detail={"code": "review_revision_conflict"})
            return await apply_product_defaults(db, user_id=user_id, order=order, identities=identities, _attempt=_attempt + 1)
    else:
        document["created_at"] = now
        # Production already maintains this unique key; ensure it for bare fixtures.
        await db[base.WORKFLOWS].create_index([("user_id", 1), ("order_number", 1)], unique=True)
        try:
            await db[base.WORKFLOWS].insert_one(document)
        except DuplicateKeyError:
            pass  # Snapshot below reads the actual winning workflow/revision.
