"""Pure proof of a delivery after the operational opening instant.

The event is independent of the customer's order age and later payment state.
It carries no price, sale, customer payment, bank effect, or opening allocation.
Only an explicit delivery timestamp can cross the opening boundary: a source
update clock or delivered label cannot supply a missing event time.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shipping_companies import normalize_shipping_company


DELIVERED = {"delivered", "تم التوصيل"}
INACTIVE = {"cancelled", "canceled", "void", "deleted", "archived", "superseded", "returned",
            "ملغي", "ملغى", "تم الإلغاء", "تم الالغاء", "مسترجع", "مرتجع", "تم الاسترجاع", "محذوف", "مؤرشف"}
RETURNS = {"return", "return_shipment", "reverse"}


def _fail(reason):
    raise ValueError("operational_delivery_" + reason)


def _text(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or isinstance(value, int) and value <= 0:
        return None
    value = str(value)
    if not value or value != value.strip() or len(value) > 300 or any(ord(char) < 32 for char in value):
        return None
    return value


def _status(value):
    if isinstance(value, dict):
        value = value.get("slug") or value.get("code") or value.get("name")
    return str(value or "").strip().casefold()


def _instant(value):
    if value is None or value == "":
        _fail("timestamp_missing")
    zone = None
    if isinstance(value, dict):
        zone, value = value.get("timezone"), value.get("date")
        if value is None or value == "":
            _fail("timestamp_missing")
    try:
        if isinstance(value, datetime):
            # Naive native BSON datetimes are UTC; naive text is not.
            parsed = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        else:
            if not isinstance(value, str) or not ("T" in value or " " in value):
                _fail("timestamp_invalid")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None and zone:
                parsed = parsed.replace(tzinfo=ZoneInfo(zone))
        if parsed.utcoffset() is None:
            _fail("timestamp_timezone_missing")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ZoneInfoNotFoundError):
        _fail("timestamp_invalid")
    except ValueError as error:
        if str(error).startswith("operational_delivery_"):
            raise
        _fail("timestamp_invalid")


def delivery_time_matches(frozen, delivered_at, *, order_id, native_driver_earning=None):
    """Match a validated native earning to an unpriced legacy second timestamp."""
    saved_at = frozen.get("delivered_at")
    if isinstance(saved_at, str):
        saved_at = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
    if not isinstance(saved_at, datetime) or saved_at.utcoffset() is None:
        return False
    saved, delivered = _instant(saved_at), _instant(delivered_at)
    if saved == delivered:
        return True
    earning = native_driver_earning or {}
    # Keep both timestamps intact. Compatibility only completes an unpriced
    # legacy driver fact after the source has proved its assignment and earning.
    return (frozen.get("party_type") == "store_driver"
            and "cost" in frozen and frozen["cost"] is None
            and frozen.get("component_mode") is False
            and saved.microsecond == 0 and delivered.microsecond != 0
            and saved.date() == delivered.date()
            and saved_at.date() == delivered.astimezone(saved_at.tzinfo).date()
            and abs(saved - delivered) < timedelta(seconds=1)
            and isinstance(earning, dict)
            and _text(earning.get("order_id")) == str(order_id)
            and _text(earning.get("assignment_id")) is not None
            and _text(earning.get("earning_id")) is not None
            and earning.get("currency") == "SAR"
            and earning.get("party_id") == frozen.get("id")
            and earning.get("candidate_amount") is not None
            and _instant(earning.get("delivered_at")) == delivered)


def _inactive(row):
    return (any(row.get(key) for key in ("archived", "superseded", "cancelled", "returned", "deleted", "is_deleted"))
            or _status(row.get("status") or row.get("shipment_status")) in INACTIVE
            or _status(row.get("type")) in RETURNS)


def _currency(raw, assignment=None):
    amounts = raw.get("amounts") if isinstance(raw.get("amounts"), dict) else {}
    payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}
    candidates = [raw, amounts, payment]
    candidates.extend(value for value in (amounts.get("total"), raw.get("total"), assignment) if isinstance(value, dict))
    currencies = [row["currency"] for row in candidates if row.get("currency") is not None]
    if not currencies:
        _fail("currency_missing")
    # Courier delivery evidence carries the customer's currency; its service
    # price is quoted independently. The native driver producer remains SAR.
    if (any(not isinstance(value, str) or len(value) != 3 or not value.isascii()
            or not value.isalpha() or value != value.upper() for value in currencies)
            or len(set(currencies)) != 1
            or (assignment is not None and currencies[0] != "SAR")):
        _fail("currency_unsupported")
    return currencies[0]


def _window(value, created, start, end):
    delivered = _instant(value)
    if delivered < created:
        _fail("timestamp_before_order")
    if delivered > end:
        _fail("event_in_future")
    return delivered if delivered >= start else None


def _driver_assignment(assignments, owner, order_id, number):
    if not isinstance(assignments, (list, tuple)):
        _fail("assignment_scope_invalid")
    if any(not isinstance(row, dict) or row.get("user_id") != owner for row in assignments):
        _fail("owner_mismatch")
    relevant = [row for row in assignments if _text(row.get("order_id")) == order_id
                or _text(row.get("order_number")) == number]
    if any(_text(row.get("order_id")) != order_id or _text(row.get("order_number")) != number for row in relevant):
        _fail("assignment_order_conflict")
    active = [row for row in relevant if row.get("active") is True and not _inactive(row)
              and _status(row.get("status")) in {"assigned", "out_for_delivery", *DELIVERED}]
    if len(active) > 1:
        _fail("assignment_ambiguous")
    if not active:
        if any(_status(row.get("status")) in DELIVERED for row in relevant):
            _fail("assignment_inactive")
        return None
    assignment = active[0]
    identity = _text(assignment.get("id"))
    if not identity or not _text(assignment.get("driver_id")):
        _fail("assignment_identity_missing")
    if sum(_text(row.get("id")) == identity for row in assignments) != 1:
        _fail("assignment_ambiguous")
    return assignment


def order_shipping_identity(raw, source):
    """Use the canonical order's carrier when no normalized label exists."""
    current = source.get("salla_shipping_current") or raw.get("salla_shipping_current")
    if current is not None:
        return current
    shipping = raw.get("shipping")
    if not isinstance(shipping, dict):
        return {}
    company = shipping.get("company")
    company = company if isinstance(company, dict) else {"name": company}
    return {**shipping, "source_kind": "order",
            "company_code": shipping.get("company_code") or company.get("code"),
            "company_name": shipping.get("company_name") or company.get("name")}


def _current_shipping(raw, source, *, require_delivered=True, require_label=True):
    current = source.get("salla_shipping_current")
    nested = raw.get("salla_shipping_current")
    if current is None:
        current = nested if require_label else order_shipping_identity(raw, source)
    if not isinstance(current, dict) or current.get("source_kind") not in {"order", "shipment"}:
        _fail("current_shipment_required")
    if nested is not None and nested is not current:
        if not isinstance(nested, dict) or any(current.get(key) != nested.get(key) for key in (
                "shipment_id", "tracking_number", "company_code", "company_name", "status")):
            _fail("current_shipment_conflict")
    shipment, waybill = _text(current.get("shipment_id")), _text(current.get("tracking_number"))
    if require_label and (not shipment or not waybill):
        _fail("shipment_identity_missing")
    superseded = current.get("superseded_shipment_ids") or []
    if not isinstance(superseded, list):
        _fail("shipment_identity_conflict")
    if not require_label:
        documented = raw.get("shipments", [])
        if (not isinstance(documented, list) or any(not isinstance(row, dict) for row in documented)
                or len({_text(row.get("id") or row.get("shipment_id")) for row in documented}) > 1
                or superseded and shipment not in superseded):
            _fail("shipment_identity_conflict")
    if _inactive(current) or shipment in {_text(value) for value in superseded}:
        _fail("shipment_inactive")
    if require_delivered and _status(current.get("status")) not in DELIVERED:
        _fail("shipment_not_delivered")
    for root, field in (("salla_shipment_id", "shipment_id"), ("tracking_number", "tracking_number"),
                        ("shipping_number", "tracking_number"), ("shipping_company_code", "company_code"),
                        ("shipping_company", "company_name"), ("shipping_status", "status"), ("shipment_status", "status")):
        if root in source and _text(source[root]) != _text(current.get(field)):
            _fail("current_shipment_conflict")
    return current, shipment, waybill


def _courier(setup, owner, current):
    from operational_courier_tariffs import matches as matches_key
    if not isinstance(setup, dict) or setup.get("user_id") != owner:
        _fail("shipping_setup_owner_mismatch")
    code = _text(current.get("company_code"))
    key = code or _text(current.get("company_name"))
    if not key:
        _fail("courier_identity_missing")
    if any(normalize_shipping_company(str(current.get(field) or ""))[0] in {"mandoob", "mandoob_riyadh", "pickup"}
           or _status(current.get(field)) in {"store_driver", "store-driver", "store driver", "store_delivery", "local_delivery", "pickup"}
           for field in ("company_code", "company_name", "method")):
        _fail("driver_assignment_required")
    couriers = setup.get("couriers") or []
    if not isinstance(couriers, list) or any(not isinstance(row, dict) for row in couriers):
        _fail("courier_identity_ambiguous")
    approved = [row for row in couriers if row.get("status") == "active" and not _inactive(row)
                and row.get("active") is not False and row.get("is_active") is not False
                and row.get("confirmed_by") and row.get("confirmed_at")]
    if any(not isinstance(row.get("salla_carrier_keys"), list)
           or any(not _text(value) for value in row["salla_carrier_keys"]) for row in approved):
        _fail("courier_identity_ambiguous")
    matches = [row for row in approved if (code or row.get("identity_match") != "exact")
               and matches_key(row, key)]
    if len(matches) != 1 or not _text(matches[0].get("courier_key")):
        _fail("courier_identity_ambiguous")
    name = _text(current.get("company_name"))
    named = [row for row in approved if row.get("identity_match") != "exact" and name and matches_key(row, name)]
    if named and {row.get("courier_key") for row in named} != {matches[0]["courier_key"]}:
        _fail("courier_identity_conflict")
    return _text(matches[0]["courier_key"]), key


def _shipment_rows(raw, shipment, *, include_nested=True):
    shipping = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
    candidates = raw.get("shipments") or []
    candidates = candidates if isinstance(candidates, list) else [candidates] if isinstance(candidates, dict) else []
    if isinstance(raw.get("shipment"), dict):
        candidates = [*candidates, raw["shipment"]]
    nested = shipping.get("shipment") if isinstance(shipping.get("shipment"), dict) else {}
    shipping_id = shipping.get("shipment_id") or nested.get("id")
    if shipping_id is not None:
        candidates = [*candidates, {**shipping, "id": shipping_id}]
    # Apply the same identity, state, carrier and waybill checks to a nested
    # shipment as to each documented top-level source.
    if include_nested and nested and _text(nested.get("id") or nested.get("shipment_id")) == shipment:
        if shipping.get("shipment_id") is not None and _text(shipping["shipment_id"]) != shipment:
            _fail("shipment_identity_conflict")
        candidates = [*candidates, nested]
    selected = [row for row in candidates if isinstance(row, dict) and _text(row.get("id") or row.get("shipment_id")) == shipment]
    if not selected:
        _fail("shipment_source_missing")
    return candidates, selected, nested


def _order_waybill(raw, current, shipment, waybill, created_through, *, optional=False):
    """Bind one original waybill whose label exists by the supplied boundary."""
    from salla_shipping import extract_shipping, same_carrier

    if optional:
        shipping = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
        candidates = list(raw.get("shipments") or [])
        candidates += [raw["shipment"]] if isinstance(raw.get("shipment"), dict) else []
        nested = shipping.get("shipment") if isinstance(shipping.get("shipment"), dict) else {}
        candidates += [nested] if nested else []
        if any(not isinstance(row, dict) for row in candidates):
            _fail("shipment_identity_conflict")
        if shipment and any(_text(row.get("id") or row.get("shipment_id")) not in {None, shipment} for row in candidates):
            _fail("order_waybill_unproven")
        selected = candidates
    else:
        candidates, selected, nested = _shipment_rows(raw, shipment)
    observed = extract_shipping(raw) or {}
    if (current.get("superseded_shipment_ids") or len(selected) != len(candidates)
            or nested and _text(nested.get("id") or nested.get("shipment_id")) != shipment):
        _fail("order_waybill_unproven")
    for row in selected:
        if _inactive(row):
            _fail("shipment_inactive")
        company = row.get("courier") or row.get("company")
        code = _text(company.get("code")) if isinstance(company, dict) else _text(row.get("company_code"))
        name = _text(company.get("name")) if isinstance(company, dict) else _text(company)
        if (code and current.get("company_code") and code != current["company_code"]
                or name and current.get("company_name") and name != current["company_name"]):
            _fail("shipment_carrier_conflict")
        tracking = row.get("tracking") if isinstance(row.get("tracking"), dict) else {}
        references = [_text(row.get(key)) for key in ("tracking_number", "tracking_id", "shipping_number", "awb")]
        references.append(_text(tracking.get("number")))
        if (waybill or not optional) and any(value != waybill for value in references if value is not None):
            _fail("shipment_waybill_conflict")
        if not optional and row.get("created_at") is not None and _instant(row["created_at"]) > created_through:
            _fail("timestamp_before_shipment")
    if optional:
        return
    if (observed.get('shipment_id') != shipment or observed.get('tracking_number') != waybill
            or not same_carrier(current, observed)):
        _fail('order_waybill_unproven')


def _shipment_time(raw, current, shipment, waybill, *, include_nested=True, order_transition=None):
    candidates, selected, nested = _shipment_rows(raw, shipment, include_nested=include_nested)
    timestamps = []
    dated_waybill = False
    delivered_waybill = False
    for row in selected:
        if _inactive(row):
            _fail("shipment_inactive")
        status = _status(row.get("status") or row.get("shipment_status"))
        if status and status not in DELIVERED:
            _fail("shipment_status_conflict")
        company = row.get("courier") or row.get("company")
        code = _text(company.get("code")) if isinstance(company, dict) else _text(row.get("company_code"))
        name = _text(company.get("name")) if isinstance(company, dict) else _text(company)
        if (code and current.get("company_code") and code != current["company_code"]
                or name and current.get("company_name") and name != current["company_name"]):
            _fail("shipment_carrier_conflict")
        tracking = row.get("tracking") if isinstance(row.get("tracking"), dict) else {}
        references = [_text(row.get(key)) for key in ("tracking_number", "tracking_id", "shipping_number", "awb")]
        references.append(_text(tracking.get("number")))
        if any(value != waybill for value in references if value is not None):
            _fail("shipment_waybill_conflict")
        delivered_waybill |= waybill in references and status in DELIVERED
        for key in ("delivered_at", "actual_delivered_at"):
            if row.get(key) is not None:
                timestamps.append(_instant(row[key]))
                dated_waybill = dated_waybill or waybill in references
    if not timestamps and order_transition is not None:
        # Order status has no shipment ID. Only the sole original, delivered
        # waybill can inherit that transition. Never pick one of several legs.
        at = order_transition()
        from salla_shipping import extract_shipping, same_carrier
        observed = extract_shipping(raw) or {}
        if (not delivered_waybill or current.get("superseded_shipment_ids")
                or observed.get("shipment_id") != shipment or observed.get("tracking_number") != waybill
                or not same_carrier(current, observed)
                or len(selected) != len(candidates)
                or nested and _text(nested.get("id") or nested.get("shipment_id")) != shipment):
            _fail("order_waybill_unproven")
        if any(_instant(row["created_at"]) > at for row in selected if row.get("created_at") is not None):
            _fail("timestamp_before_shipment")
        return at, True
    if not dated_waybill:
        _fail("timestamp_missing")
    if len(set(timestamps)) != 1:
        _fail("timestamp_ambiguous")
    if any(_instant(row["created_at"]) > timestamps[0] for row in selected if row.get("created_at") is not None):
        _fail("timestamp_before_shipment")
    return timestamps[0], False


def delivery_requires_cod_proof(raw, event):
    """Nested and order-level delivery proofs alone do not establish collected cash."""
    if event.get("timestamp_source") == "order_status_transition":
        return True
    try:
        _shipment_time(raw, {}, event["shipment_id"], event["waybill"], include_nested=False)
    except ValueError:
        return True
    return False


def conflicting_shipments(sources, state):
    """A current AWB or frozen shipment already used by another order needs review."""
    seen = defaultdict(set)

    def bind(oid, shipment, waybill):
        if oid and shipment and waybill:
            seen[("shipment", shipment)].add(oid)
            seen[("waybill", waybill)].add(oid)

    for source in sources:
        raw = (source.get("raw_by_source") or {}).get("salla_direct")
        if not isinstance(raw, dict):
            continue
        current = source.get("salla_shipping_current") or raw.get("salla_shipping_current")
        if isinstance(current, dict):
            bind(_text(raw.get("id")), _text(current.get("shipment_id")), _text(current.get("tracking_number")))
    for k, event in (state or {}).get("engine", {}).get("facts", {}).items():
        if k.startswith("delivery_event:") and event.get("party_type") == "courier":
            bind(_text(event.get("order_id")), _text(event.get("shipment_id")), _text(event.get("waybill")))
    for saved in (state or {}).get("historical_shipping_fee_approvals", {}).values():
        p = saved["candidate"]
        if p.get("party_type") == "courier":
            bind(_text(p.get("order_id")), _text(p.get("shipment_id")), _text(p.get("waybill")))
    return {key for key, orders in seen.items() if len(orders) > 1}


def recorded_courier_fee(state, order_id):
    """An unpriced delivery observation cannot grandfather a new financial charge."""
    engine = (state or {}).get('engine', {})
    facts = engine.get('facts', {})
    root = 'shipping:' + order_id
    if (facts.get(root, {}).get('cost') is not None or 'shipping_fee:' + order_id in facts
            or any(key.startswith(root + ':') for key in facts)):
        return True
    keys = {key for key, row in engine.get('obligations', {}).items()
            if (key == root or key.startswith(root + ':')) and row.get('kind') == 'shipping'}
    return (any(Decimal(engine['obligations'][key].get('confirmed', '0')) > 0 for key in keys)
            or any(a.get('obligation_id') in keys and Decimal(a.get('amount', '0')) > 0
                   for movement in (state or {}).get('movements', []) for a in movement.get('allocations', [])))


def resolve_delivery_event(raw, *, source, owner, assignments, setup, started_at, as_of, history=(), shipment_conflicts=(),
                           frozen_event=None, frozen_shipping=None, recorded_fee=False):
    """Return a proved post-start event, None outside scope, or an exact issue.

    The caller retains normal order eligibility independently and quotes the
    existing shipping contract at ``delivered_at``. This resolver never decides
    a fee, collection approval, refund, or how a pooled opening should settle.
    """
    if not _text(owner) or not isinstance(source, dict) or source.get("user_id") != owner:
        _fail("owner_mismatch")
    if not isinstance(raw, dict):
        _fail("canonical_source_missing")
    raw_sources = source.get("raw_by_source")
    canonical = raw_sources.get("salla_direct") if isinstance(raw_sources, dict) else None
    if not isinstance(canonical, dict):
        _fail("canonical_source_missing")
    order_id = _text(raw.get("id"))
    number = _text(raw.get("reference_id") or raw.get("order_number"))
    if not order_id or not number:
        _fail("order_identity_missing")
    if (_text(canonical.get("id")) != order_id
            or _text(canonical.get("reference_id") or canonical.get("order_number")) != number):
        _fail("order_identity_conflict")
    watermark = source.get("g47_salla_snapshot") or {}
    if not isinstance(watermark, dict) or watermark.get("requires_authoritative_refresh"):
        _fail("canonical_source_stale")
    start, end = _instant(started_at), _instant(as_of)
    if end < start:
        _fail("time_range_invalid")
    created = _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
    if created > end:
        _fail("order_in_future")
    assignment = _driver_assignment(assignments, owner, order_id, number)
    base = {"schema": "operational_delivery_event_v1", "order_id": order_id, "order_number": number,
            "order_created_at": created.isoformat()}
    if assignment is not None:
        if _status(assignment.get("status")) not in DELIVERED:
            return None
        delivered = _window(assignment.get("delivered_at"), created, start, end)
        if delivered is None:
            return None
        assignment_id = _text(assignment["id"])
        return {**base, "id": "store-delivery:" + assignment_id, "delivered_at": delivered.isoformat(),
                "party_type": "store_driver", "party_id": _text(assignment["driver_id"]),
                "currency": _currency(raw, assignment), "assignment_id": assignment_id}
    current = order_shipping_identity(raw, source)
    if not isinstance(current, dict):
        _fail("current_shipment_required")
    if _status(raw.get("status")) not in DELIVERED and _status(current.get("status")) not in DELIVERED:
        return None
    current, shipment, waybill = _current_shipping(raw, source, require_delivered=False, require_label=recorded_fee and not (frozen_event or {}).get("shipping_identity_source"))
    if ("shipment", shipment) in shipment_conflicts or ("waybill", waybill) in shipment_conflicts:
        _fail("shipment_shared_by_orders")
    from operational_courier_transitions import CARRIER_HISTORY, WEBHOOK_PROOF, carrier_history_snapshot, order_delivery_time
    provenance = {}
    evidence_raw = raw
    observation = current.get(WEBHOOK_PROOF) or {}
    if observation.get("source") == CARRIER_HISTORY:
        # The separately fetched shipment is newer than an order-only payload.
        # Its complete native history and binding are checked before use.
        order_delivery_time(raw, source, history, end, provenance=provenance)
        evidence_raw = carrier_history_snapshot(raw, current)
    # Existing confirmed amounts keep their original native evidence. New fees
    # use the order's actual delivered transition, never a label timestamp or a
    # generic order update. The waybill supplies courier identity only.
    legacy = recorded_fee and (frozen_event is None or frozen_event.get("timestamp_source") != "order_status_transition")
    if legacy:
        # A grandfathered fee does not grandfather new cash evidence when the
        # current carrier's state no longer satisfies the native COD contract.
        _current_shipping(raw, source, require_delivered=True)
        at, from_order = _shipment_time(evidence_raw, current, shipment, waybill,
            order_transition=lambda: order_delivery_time(raw, source, history, end, provenance=provenance))
    else:
        at = order_delivery_time(raw, source, history, end, provenance=provenance)
        # The authoritative order delivery and its actual event date establish
        # the earned fee. A label is optional logistics metadata, not an extra
        # financial eligibility document. Native COD proof remains separate.
        _order_waybill(evidence_raw, current, shipment, waybill, at, optional=True)
        from_order = True
        # Preserve the separate pre-existing native delivery/COD contract when
        # its own dated shipment independently agrees with this order event.
        if (frozen_event is None or frozen_event.get("timestamp_source") != "order_status_transition") and _status(current.get("status")) in DELIVERED:
            try:
                native_at, _ = _shipment_time(evidence_raw, current, shipment, waybill)
                from_order = native_at != at
                if from_order:
                    base['native_delivery_at'] = native_at.isoformat()
            except ValueError:
                pass
        elif frozen_event is not None and frozen_event.get("timestamp_source") == "order_status_transition":
            if frozen_event.get('native_delivery_at'):
                base['native_delivery_at'] = frozen_event['native_delivery_at']
            try:
                native_at, _ = (_shipment_time(evidence_raw, current, shipment, waybill)
                                if _status(current.get('status')) in DELIVERED else (at, True))
            except ValueError:
                native_at = at
            if native_at != at and native_at.isoformat() != frozen_event.get('native_delivery_at'):
                _fail("confirmed_evidence_changed")
    if provenance and _instant(provenance["at"]) != at:
        _fail("timestamp_ambiguous")
    delivered = _window(at, created, start, end)
    if delivered is None:
        return None
    if from_order:
        base["timestamp_source"] = "order_status_transition"
    if provenance:
        event = provenance["event"]
        base.update(timestamp_source="salla_carrier_history", source_history_id=event["history_id"],
                    source_actor_id=event["actor"]["id"], source_actor_name=event["actor"]["name"],
                    source_native_type=event["native_type"], source_dispatch_history_id=provenance["dispatch"]["history_id"])
    party, carrier_key = _courier(setup, owner, current)
    if frozen_event and frozen_event.get("shipping_identity_source") == "order_carrier":
        if frozen_event.get("party_id") != party or frozen_event.get("delivered_at") != delivered.isoformat():
            _fail("confirmed_evidence_changed")
        # Later label enrichment cannot rewrite an already recognized order fee.
        shipment, waybill = frozen_event.get("shipment_id"), frozen_event.get("waybill")
        base["shipping_identity_source"] = "order_carrier"
        identity = frozen_event["id"].removeprefix("courier-delivery:")
    else:
        if not shipment or not waybill:
            base["shipping_identity_source"] = "order_carrier"
        identity = sha256(json.dumps([order_id, shipment] if shipment and waybill else [order_id, "courier", party],
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {**base, "id": "courier-delivery:" + identity, "delivered_at": delivered.isoformat(),
            "party_type": "courier", "party_id": party, "currency": _currency(raw),
            "shipment_id": shipment, "waybill": waybill, "source_carrier_key": carrier_key}
