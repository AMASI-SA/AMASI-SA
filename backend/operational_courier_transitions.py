"""Narrow bridge from Salla's order status events to one current delivery."""
from collections import defaultdict
import re

from operational_delivery_evidence import DELIVERED, _fail, _instant, _status, _text

COLLECTION = "order_activity_events_v2"
# Salla's native history descriptions used on the order timeline. Arbitrary
# notes mentioning delivery, including hypothetical changes, are not events.
TARGET = re.compile(r"(?:تم تغيير الحالة|تغيرت الحالة|تم تغيير حالة الطلب|تغيرت حالة الطلب) (?:إلى|الى) (.+)|"
                    r"(?:order )?status (?:changed|updated) to (.+)", re.IGNORECASE)


def _state(value):
    values = [value.get(k) for k in ("slug", "code", "name", "label", "title")] if isinstance(value, dict) else [value]
    states = {_status(v) in DELIVERED for v in values if _text(v)}
    return states.pop() if len(states) == 1 else None


def native_status_transition(row, title, event_type, provider_id):
    """Preserve native event dates before the display normalizer flattens them."""
    if event_type != "status_changed" or not _text(row.get("id") or row.get("history_id")):
        return None
    target = TARGET.fullmatch(title.strip())
    states = [_state(row[k]) for k in ("new_status", "to_status") if row.get(k) is not None]
    if target:
        states.append(_state((target.group(1) or target.group(2)).strip(' «»".')))
    if not states:
        return None
    delivered = states[0] if None not in states and len(set(states)) == 1 else None
    if row.get("order_id") is not None and _text(row["order_id"]) != provider_id:
        delivered = None
    try:
        dates = {_instant(row[k]).isoformat() for k in ("created_at", "date") if row.get(k) is not None}
    except ValueError:
        dates = set()
    return {"delivered": delivered, "at": dates.pop() if len(dates) == 1 else None}


def order_delivery_time(raw, source, history, end):
    """Require agreement of authoritative order status, explicit dates and history."""
    order_id, number = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
    if any(source.get(k) is not None and _text(source[k]) != value
           for k, value in (("salla_order_id", order_id), ("order_number", number))):
        _fail("order_identity_conflict")
    date = raw.get("date") if isinstance(raw.get("date"), dict) else {}
    version = raw.get("updated_at") or date.get("updated") or raw.get("date_updated")
    watermark = source.get("g47_salla_snapshot") or {}
    if watermark.get("cancelled") or watermark.get("component_pending") or (
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
    other_times = set()
    for row in history:
        if row.get("provider_order_id") != order_id or row.get("order_number") != number:
            _fail("order_identity_conflict")
        proofs = row.get("native_status_transition")
        if not _text(row.get("source_event_id")) or not isinstance(proofs, list) or len(proofs) != 1:
            _fail("timestamp_ambiguous")
        obs = proofs[0]
        if not isinstance(obs, dict) or type(obs.get("delivered")) is not bool:
            _fail("timestamp_ambiguous")
        (times if obs["delivered"] else other_times).add(_instant(obs.get("at")))
    if not times:
        _fail("timestamp_missing")
    if len(times) != 1 or other_times and max(other_times) >= min(times):
        _fail("timestamp_ambiguous")
    at = times.pop()
    if version is not None and not at <= _instant(version) <= end:
        _fail("canonical_source_stale")
    return at


async def load_delivery_history(db, owner, sources, *, max_rows):
    """Read only typed transition observations for delivered, identified orders."""
    numbers = set()
    for source in sources:
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        if not isinstance(raw, dict):
            continue
        current = source.get("salla_shipping_current") or raw.get("salla_shipping_current")
        if not isinstance(current, dict) or _status(current.get("status")) not in DELIVERED:
            continue
        number = _text(raw.get("reference_id") or raw.get("order_number"))
        if number and _text(raw.get("id")) and _text(current.get("shipment_id")) and _text(current.get("tracking_number")):
            numbers.add(number)
    projection = {"_id": 0, **{k: 1 for k in ("user_id", "order_number", "provider_order_id", "source", "event_type",
                                                "source_event_id", "native_status_transition")}}
    groups, count = defaultdict(list), 0
    numbers = sorted(numbers)
    for offset in range(0, len(numbers), 250):
        batch = numbers[offset:offset + 250]
        query = {"user_id": owner, "source": "salla_history", "event_type": "status_changed",
                 "order_number": {"$in": batch}, "native_status_transition": {"$exists": True}}
        rows = await db[COLLECTION].find(query, projection).to_list(max_rows - count + 1)
        count += len(rows)
        if count > max_rows:
            raise ValueError("operational_source_scope_too_large")
        for row in rows:
            if (row.get("user_id") != owner or row.get("order_number") not in batch
                    or row.get("source") != "salla_history" or row.get("event_type") != "status_changed"):
                raise ValueError("operational_source_owner_mismatch")
            groups[row["order_number"]].append(row)
    return groups
