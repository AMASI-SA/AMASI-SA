"""PR3.1 hold projection only. No application or release capability."""
from copy import deepcopy

import fulfillment_lifecycle as controls

AUTHORITY = "order_change_pr3_1"
ADD_KIND = "ADD_CHANGE_HOLD"
IDENTITY_FIELDS = ("change_id", "order_item_id", "unit_index", "generation", "revision")


def add_identity_matches(hold, identity):
    """Exact reference check for a future PR4; never authorizes or performs release.

    PR4 must separately enforce actor/tenant, current fences, other holds and a
    complete atomic application. This pure predicate is not that application.
    """
    return (hold.get("authority") == AUTHORITY and hold.get("contract_version") == 5
            and hold.get("hold_kind") == ADD_KIND and hold.get("scope") == "item"
            and hold.get("status") == "active" and set(identity) == set(IDENTITY_FIELDS)
            and all(type(identity[k]) is type(hold.get(k)) and identity[k] == hold.get(k)
                    for k in IDENTITY_FIELDS)
            and hold.get("target_id") == identity["order_item_id"])


def event_holds(event, *, existing_item_ids):
    """Use PR1-understood scopes; ambiguous source evidence stays order-wide."""
    owner, number = event["user_id"], event["order_number"]
    base = {"user_id": owner, "order_number": number, "status": "active",
            "change_id": event["change_id"], "event_id": event["event_id"],
            "revision": event["revision"], "source_generation": event["generation"],
            "source_version": event["source_version"], "created_at": event["recorded_at"],
            "mezan_only": True, "salla_updated": False}
    kind = event["change_type"]
    old, new = event.get("old_data") or {}, event.get("new_data") or {}
    safe = event["intake_state"] == "pending_application"
    item = new.get("order_item_id")
    if (safe and kind == "add_product" and not old and isinstance(item, str) and item
            and item not in existing_item_ids and type(new.get("quantity")) is int
            and 1 <= new["quantity"] <= 1000 and isinstance(new.get("options"), dict)):
        return [{**base, "id": "add-change-" + controls._identity(owner,
                    [number, event["change_id"], item, index, 0, event["revision"]]),
                 "scope": "item", "target_id": item, "order_item_id": item,
                 "unit_index": index, "generation": 0, "contract_version": 5,
                 "authority": AUTHORITY, "hold_kind": ADD_KIND,
                 "release_contract": "pr4_atomic_add_exact_identity",
                 "reason": "New source item awaits atomic ADD application"}
                for index in range(1, new["quantity"] + 1)]
    # The complete old source identity is sufficient for an item-wide stop,
    # even when its physical pieces have not been materialized yet.
    targets = []
    if safe and kind in {"cancel_product", "edit_options", "replace_product"}:
        old_id = old.get("order_item_id")
        if isinstance(old_id, str) and old_id and old.get("product_id"):
            targets = [old_id]
            if kind == "replace_product":
                new_id = new.get("order_item_id")
                if not isinstance(new_id, str) or not new_id:
                    targets = []
                elif new_id not in targets:
                    targets.append(new_id)
    if targets:
        return [{**base, "id": "source-item-" + controls._identity(owner, [event["event_id"], target]),
                 "scope": "item", "target_id": target, "contract_version": 4,
                 "authority": "salla_change_pr3", "hold_kind": "SOURCE_ITEM_CHANGE_HOLD",
                 "affected_units": deepcopy(event["affected_units"]),
                 "reason": "Source item change awaits its separate application contract"}
                for target in targets]
    # Preserve the established singleton for uncertain/order-wide operations.
    return [{**base, "id": "salla-reconciliation-" + controls._identity(owner, number),
             "scope": "order", "target_id": number, "contract_version": 4,
             "authority": "salla_change_pr3", "hold_kind": "SOURCE_ORDER_CHANGE_HOLD",
             "reason": "Source change requires conservative order reconciliation"}]
