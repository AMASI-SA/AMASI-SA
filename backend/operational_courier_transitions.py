"""Narrow bridge from Salla's order status events to one current delivery."""
from collections import defaultdict
from datetime import datetime

from operational_delivery_evidence import DELIVERED, _fail, _instant, _status, _text

COLLECTION = "order_activity_events_v2"
WEBHOOK_PROOF = "verified_order_delivery_transition"
CARRIER_HISTORY = "verified_salla_carrier_history"
ORDER_HISTORY = "verified_salla_order_history"
IN_TRANSIT = {"delivering", "out_for_delivery", "جاري التوصيل", "قيد التوصيل"}
COMPLETED = {"completed", "تم التنفيذ"}
COMPLETED_PROOF = 'verified_order_completed_transition'


def _state(value):
    if isinstance(value, dict):
        value = value.get('customized') or value
    values = [value.get(k) for k in ("slug", "code", "name", "label", "title")] if isinstance(value, dict) else [value]
    states = {_status(v) in DELIVERED for v in values if _text(v)}
    return states.pop() if len(states) == 1 else None


def completed_state(value):
    if isinstance(value, dict):
        value = value.get('customized') or value
    values = [value.get(k) for k in ('slug', 'code', 'name', 'label', 'title')] if isinstance(value, dict) else [value]
    states = {_status(v) in COMPLETED for v in values if _text(v)}
    return states.pop() if len(states) == 1 else None


def transition_observation(proofs):
    """Allow only the same typed target added to an otherwise identical old row."""
    if not isinstance(proofs, list) or not proofs or any(not isinstance(row, dict) for row in proofs):
        _fail('timestamp_ambiguous')
    base = {k: v for k, v in proofs[0].items() if k != 'target_status'}
    targets = {row.get('target_status') for row in proofs if row.get('target_status') is not None}
    if (any({k: v for k, v in row.items() if k != 'target_status'} != base for row in proofs)
            or targets - {'completed'} or targets and base.get('delivered') is not False):
        _fail('timestamp_ambiguous')
    return {**base, **({'target_status': 'completed'} if targets else {})}


def native_status_transition(row, provider_id):
    """Preserve native event dates before the display normalizer flattens them."""
    if not _text(row.get("id") or row.get("history_id")):
        return None
    # The histories API permits a free-form note with the *current* status.
    # Its creation time never dates a transition, even when the note says so.
    states = [_state(row[k]) for k in ("new_status", "to_status") if row.get(k) is not None]
    if not states and any(row.get(k) in ("status_changed", "order.status.updated", "status_updated")
                          for k in ("event", "type")):
        states.append(_state(row.get("customized") or row.get("status")))
    if not states:
        # Carrier integrations publish their status timeline as activity rows.
        # User comments have a current-status label too, but are not transitions.
        status, note = _status(row.get("status")), _status(row.get("note"))
        actor = row.get("created_by")
        if row.get("type") != "activity" or not status:
            return None
        try:
            at = _instant(row.get("created_at")).isoformat()
        except ValueError:
            at = None
        if (status != note or row.get("customized") or not isinstance(actor, dict)
                or not _text(actor.get("id")) or not isinstance(actor.get("name"), str) or not _text(actor.get("name"))):
            # A later native non-delivered observation still conflicts with a
            # proposed delivery. It cannot establish a delivery or dispatch.
            observed = _state(row.get("customized") or row.get("status"))
            if observed is True:
                return None
            if row.get("order_id") is not None and _text(row["order_id"]) != provider_id:
                observed = None
            return {"delivered": observed, "at": at, "source": "salla_activity_status_observation"}
        return {"delivered": status in DELIVERED, "at": at, "source": "salla_carrier_activity",
                "history_id": _text(row.get("id") or row.get("history_id")), "native_type": "activity",
                "status": status, "created_at": row.get("created_at"),
                "actor": {"id": _text(actor["id"]), "name": actor["name"]},
                "order_id": _text(row["order_id"]) if row.get("order_id") is not None else provider_id}
    delivered = states[0] if None not in states and len(set(states)) == 1 else None
    if row.get("order_id") is not None and _text(row["order_id"]) != provider_id:
        delivered = None
    try:
        dates = {_instant(row[k]).isoformat() for k in ("created_at", "date") if row.get(k) is not None}
    except ValueError:
        dates = set()
    targets = [row[k] for k in ('new_status', 'to_status') if row.get(k) is not None]
    if not targets:
        targets = [row.get('customized') or row.get('status')]
    completed = (targets and all(completed_state(target) is True for target in targets)
                 and (row.get('order_id') is None or _text(row['order_id']) == provider_id))
    return {"delivered": delivered, "at": dates.pop() if len(dates) == 1 else None,
            **({'target_status': 'completed'} if completed else {})}


def carrier_activity_transition(observations, current, provider_id):
    """Bind an explicit native carrier timeline to the current carrier identity."""
    from shipping_companies import SHIPPING_ALIASES, normalize_shipping_company

    if not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations):
        _fail("courier_history_ambiguous")
    key = normalize_shipping_company(current.get("company_name"))[0]
    if key in {"unknown", "mandoob", "mandoob_riyadh", "pickup"} or key.startswith("other:"):
        _fail("courier_history_actor_unproven")
    # Match complete native publisher labels, never an arbitrary substring in
    # a user's name. The same publisher ID must supply dispatch and delivery.
    labels = {" ".join(label.casefold().split()) for carrier, display, alias in SHIPPING_ALIASES if carrier == key
              for label in (display, alias, alias + " delivery", alias + " express", alias + " للتوصيل", alias + " للشحن")}
    selected = {}
    for observation in observations:
        actor = observation.get("actor") or {}
        identity = _text(observation.get("history_id"))
        if (not identity or observation.get("native_type") != "activity"
                or observation.get("source") != "salla_carrier_activity"
                or observation.get("order_id") != provider_id or not isinstance(actor, dict)
                or not _text(actor.get("id")) or not isinstance(actor.get("name"), str) or not _text(actor.get("name"))
                or type(observation.get("delivered")) is not bool
                or observation["delivered"] != (_status(observation.get("status")) in DELIVERED)):
            _fail("courier_history_ambiguous")
        if _instant(observation.get("at")) != _instant(observation.get("created_at")):
            _fail("courier_history_ambiguous")
        if " ".join(actor["name"].casefold().split()) not in labels:
            continue
        if identity in selected and selected[identity] != observation:
            _fail("courier_history_ambiguous")
        selected[identity] = observation
    delivered = [row for row in selected.values() if row["delivered"]]
    if len(delivered) != 1:
        _fail("courier_history_ambiguous")
    event = delivered[0]
    at = _instant(event["at"])
    dispatch = [row for row in selected.values() if _status(row.get("status")) in IN_TRANSIT
                and row["actor"] == event["actor"] and _instant(row["at"]) < at]
    if not dispatch or any(_instant(row["at"]) >= at for row in observations if row.get("delivered") is not True):
        _fail("courier_history_ambiguous")
    previous = max(dispatch, key=lambda row: _instant(row["at"]))
    return {"at": event["at"], "event": event, "dispatch": previous}


def verified_webhook_transition(body, raw, current, owner):
    """Build evidence only inside the verified, merchant-resolved order webhook."""
    from operational_delivery_evidence import _order_waybill

    if body.get("event") != "order.status.updated" or not _text(owner):
        return None
    data = body.get("data") or {}
    if not isinstance(data, dict) or not isinstance(current, dict):
        return None
    status = raw.get("status")
    effective = status.get("customized") or status if isinstance(status, dict) else status
    target = data.get("to_status") or data.get("new_status") or data.get("status")
    target = target.get("customized") or target if isinstance(target, dict) else target
    oid, number = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
    shipment, waybill = _text(current.get("shipment_id")), _text(current.get("tracking_number"))
    if (not oid or not number or not shipment or not waybill or _state(target) is not True
            or _state(effective) is not True
            or any(data.get(k) is not None and _text(data[k]) != v for k, v in (
                ("order_id", oid), ("order_number", number), ("reference_id", number)))):
        return None
    try:
        stamp = body.get("created_at")
        if isinstance(stamp, str) and " GMT" in stamp:
            stamp = datetime.strptime(stamp, "%a %b %d %Y %H:%M:%S GMT%z")
        at = _instant(stamp)
        _order_waybill(raw, current, shipment, waybill, at)
    except (TypeError, ValueError):
        return None
    return {"source": "verified_order_status_update", "owner": owner, "order_id": oid, "order_number": number,
            "shipment_id": shipment, "waybill": waybill, "at": at.isoformat()}


def order_delivery_time(raw, source, history, end, *, provenance=None):
    """Require agreement of authoritative order status, explicit dates and history."""
    order_id, number = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
    if any(source.get(k) is not None and _text(source[k]) != value
           for k, value in (("salla_order_id", order_id), ("order_number", number))):
        _fail("order_identity_conflict")
    date = raw.get("date") if isinstance(raw.get("date"), dict) else {}
    version = raw.get("updated_at") or date.get("updated") or raw.get("date_updated")
    watermark = source.get("g47_salla_snapshot") or {}
    if watermark.get("cancelled") or watermark.get("component_pending") or watermark.get("requires_authoritative_refresh") or (
            watermark.get("source_updated_at") is not None and
            _instant(watermark["source_updated_at"]) != _instant(version)):
        _fail("canonical_source_stale")
    status = raw.get("status")
    obj = status if isinstance(status, dict) else {}
    effective = obj.get("customized") or status
    if _state(effective) is not True or any(_state(value) is not True for value in (
            raw.get("status_slug"), source.get("order_status"), source.get("order_status_slug")) if value is not None):
        _fail("order_shipment_status_conflict")
    # A parent status's date cannot establish when a customized child changed.
    status_time = effective.get("changed_at") if isinstance(effective, dict) else None
    times = {_instant(value) for value in (status_time, raw.get("status_changed_at")) if value is not None}
    current = source.get("salla_shipping_current") or {}
    observation = current.get(WEBHOOK_PROOF)
    if observation is not None:
        if (not isinstance(observation, dict) or observation.get("ambiguous")
                or observation.get("source") not in {"verified_order_status_update", CARRIER_HISTORY, ORDER_HISTORY}
                or observation.get("owner") != source.get("user_id") or observation.get("order_id") != order_id
                or observation.get("order_number") != number or observation.get("shipment_id") != current.get("shipment_id")
                or observation.get("waybill") != current.get("tracking_number")):
            _fail("order_waybill_unproven")
        if observation["source"] == CARRIER_HISTORY:
            transition = carrier_activity_transition(observation.get("events") or [], current, order_id)
            if (observation.get("history_complete") is not True
                    or _instant(transition["at"]) != _instant(observation.get("at"))
                    or _instant(observation.get("source_updated_at")) > _instant(version)):
                _fail("courier_history_ambiguous")
            if provenance is not None:
                provenance.update(transition)
        elif observation["source"] == ORDER_HISTORY:
            events = observation.get("events")
            if (observation.get("history_complete") is not True or not isinstance(events, list) or not events
                    or _instant(observation.get("source_updated_at")) > _instant(version)):
                _fail("timestamp_ambiguous")
            unbound = {**source, "salla_shipping_current": {k: v for k, v in current.items() if k != WEBHOOK_PROOF}}
            recorded = order_delivery_time(raw, unbound, events, end)
            if recorded != _instant(observation.get("at")):
                _fail("timestamp_ambiguous")
        times.add(_instant(observation.get("at")))
    other_times = set()
    activities = []
    for row in history:
        if row.get("provider_order_id") != order_id or row.get("order_number") != number:
            _fail("order_identity_conflict")
        proofs = row.get("native_status_transition")
        if not _text(row.get("source_event_id")):
            _fail("timestamp_ambiguous")
        obs = transition_observation(proofs)
        if not isinstance(obs, dict) or type(obs.get("delivered")) is not bool:
            _fail("timestamp_ambiguous")
        if obs.get("source") == "salla_carrier_activity":
            if obs.get("history_id") != row["source_event_id"]:
                _fail("courier_history_ambiguous")
            activities.append(obs)
            continue
        (times if obs["delivered"] else other_times).add(_instant(obs.get("at")))
    if activities and times:
        # An explicit order transition stands independently of a carrier's
        # unfinished activity timeline. A later non-delivered observation is
        # still contradictory; an earlier dispatch cannot demand delivery proof.
        other_times.update(_instant(row.get('at')) for row in activities if row.get('delivered') is not True)
    elif activities:
        transition = carrier_activity_transition(activities, current, order_id)
        times.add(_instant(transition["at"]))
        if provenance is not None:
            provenance.update(transition)
    if not times:
        _fail("timestamp_missing")
    if len(times) != 1 or other_times and max(other_times) >= min(times):
        _fail("timestamp_ambiguous")
    at = times.pop()
    if at > end:
        _fail('event_in_future')
    if version is not None and not at <= _instant(version) <= end:
        _fail("canonical_source_stale")
    return at


def carrier_history_snapshot(raw, current):
    """Read the separately acquired shipment only for its bound native order."""
    observation = current.get(WEBHOOK_PROOF) or {}
    shipment = observation.get("shipment")
    order_id, number = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
    if (observation.get("source") != CARRIER_HISTORY or not isinstance(shipment, dict)
            or _text(shipment.get("order_id")) != order_id
            or _text(shipment.get("id")) != current.get("shipment_id")
            or shipment.get("order_reference_id") is not None and _text(shipment["order_reference_id"]) != number):
        _fail("order_waybill_unproven")
    return {**raw, "shipping": {}, "shipment": None, "shipments": [shipment]}


async def load_delivery_history(db, owner, sources, *, max_rows, completion_keys=()):
    """Read only typed transition observations for delivered, identified orders."""
    numbers = set()
    for source in sources:
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        if not isinstance(raw, dict):
            continue
        current = source.get("salla_shipping_current") or raw.get("salla_shipping_current")
        status = raw.get("status")
        effective = status.get("customized") or status if isinstance(status, dict) else status
        completion = isinstance(current, dict) and any(
            str(current.get(key) or '').casefold() in completion_keys for key in ('company_code', 'company_name'))
        if (not isinstance(current, dict)
                or not completion and _state(effective) is not True and _status(current.get("status")) not in DELIVERED):
            continue
        number = _text(raw.get("reference_id") or raw.get("order_number"))
        if number and _text(raw.get("id")) and (completion or (
                _text(current.get("shipment_id")) and _text(current.get("tracking_number")))):
            numbers.add(number)
    projection = {"_id": 0, **{k: 1 for k in ("user_id", "order_number", "provider_order_id", "source", "event_type",
                                                "source_event_id", "native_status_transition")}}
    groups, count = defaultdict(list), 0
    numbers = sorted(numbers)
    for offset in range(0, len(numbers), 250):
        batch = numbers[offset:offset + 250]
        query = {"user_id": owner, "source": "salla_history",
                 "order_number": {"$in": batch}, "native_status_transition": {"$exists": True}}
        rows = await db[COLLECTION].find(query, projection).to_list(max_rows - count + 1)
        count += len(rows)
        if count > max_rows:
            raise ValueError("operational_source_scope_too_large")
        for row in rows:
            if (row.get("user_id") != owner or row.get("order_number") not in batch
                    or row.get("source") != "salla_history"):
                raise ValueError("operational_source_owner_mismatch")
            groups[row["order_number"]].append(row)
    return groups
