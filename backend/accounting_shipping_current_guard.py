"""Read-only current-shipment adapter for existing courier fee writers.

Operational truth is PR1231's owner-scoped salla_shipping_current group.
No legacy read, rate calculation, posting, evidence rewrite or provider I/O.
New native delivery evidence can bind a fee proof without changing COD facts.
Old unposted evidence without that binding fails closed; posted replay is
handled by the existing writer before this adapter is called.
"""
from shipping_companies import normalize_shipping_company
from accounting_shipping_native_contract import SETUP


class CurrentShippingError(ValueError):
    pass


def _text(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _reject(code):
    raise CurrentShippingError(code)


def _inactive(row):
    status = row.get("status") or row.get("shipment_status")
    if isinstance(status, dict):
        status = status.get("slug") or status.get("code") or status.get("name")
    return (any(row.get(k) for k in ("archived", "superseded", "cancelled", "returned")) or
            _text(row.get("type")).casefold() in {"return", "return_shipment", "reverse"} or
            _text(status).casefold() in {"cancelled", "canceled", "void", "deleted", "archived", "superseded", "returned"})


def _store_driver(current):
    reserved = {"store_driver", "store-driver", "store driver", "store_delivery", "store-delivery",
                "local_delivery", "local-driver", "pickup"}
    return any(_text(current.get(key)).casefold() in reserved or
               normalize_shipping_company(_text(current.get(key)))[0] in {"mandoob", "mandoob_riyadh", "pickup"}
               for key in ("company_code", "company_name", "method"))


async def _current(db, owner, number, order_id=None):
    number = _text(number)
    if not number:
        _reject("shipping_current_order_missing")
    numbers = [number]
    if number.isascii() and number.isdigit() and str(int(number)) == number:
        numbers.append(int(number))
    rows = await db.unified_orders.find({"user_id": owner, "order_number": {"$in": numbers}}).limit(2).to_list(2)
    if len(rows) != 1:
        _reject("shipping_current_order_ambiguous" if rows else "shipping_current_order_missing")
    row = rows[0]
    raw = (row.get("raw_by_source") or {}).get("salla_direct") or {}
    if order_id is not None and _text(raw.get("id")) != _text(order_id):
        _reject("shipping_current_order_ambiguous")
    current = row.get("salla_shipping_current")
    if not isinstance(current, dict) or current.get("source_kind") not in {"order", "shipment"}:
        _reject("shipping_current_carrier_unresolved")
    key = _text(current.get("company_code")) or _text(current.get("company_name"))
    if (not key or normalize_shipping_company(key)[0] == "unknown" or
            not _text(current.get("company_code")) and key.isdigit()):
        _reject("shipping_current_carrier_unresolved")
    if _store_driver(current):
        _reject("shipping_current_store_driver_no_courier_fee")
    shipment = _text(current.get("shipment_id"))
    waybill = _text(current.get("tracking_number"))
    superseded = current.get("superseded_shipment_ids") or []
    if not isinstance(superseded, list):
        _reject("shipping_current_shipment_evidence_required")
    if (_inactive(current) or
            shipment in {_text(x) for x in superseded} or
            _text(current.get("status")).casefold() not in {"delivered", "تم التوصيل"} or
            _text(current.get("type")).casefold() in {"return", "return_shipment", "reverse"}):
        _reject("shipping_current_shipment_inactive")
    watermark = row.get("g47_salla_snapshot") or {}
    if watermark.get("requires_authoritative_refresh") or watermark.get("cancelled"):
        _reject("shipping_current_shipment_inactive")
    if not shipment or not waybill:
        _reject("shipping_current_shipment_evidence_required")
    for root, field in (("shipping_company", "company_name"), ("shipping_company_code", "company_code"),
                        ("salla_shipment_id", "shipment_id"), ("tracking_number", "tracking_number"),
                        ("shipping_number", "tracking_number"), ("shipping_status", "status"),
                        ("shipment_status", "status")):
        if root in row and _text(row[root]) != _text(current.get(field)):
            _reject("shipping_current_shipping_identity_conflict")
    return row, current, key


def _native_party(setup, current, key):
    parties = [r for r in setup.get("couriers", []) if r.get("status") == "active"
               and r.get("confirmed_by") and r.get("confirmed_at") and key in (r.get("salla_carrier_keys") or [])]
    if len(parties) != 1:
        _reject("shipping_current_carrier_unresolved")
    # An explicitly configured name contradicting an explicitly configured
    # provider code is ambiguous, even if one of the two is individually valid.
    name = _text(current.get("company_name"))
    named = [r for r in setup.get("couriers", []) if r.get("status") == "active"
             and r.get("confirmed_by") and r.get("confirmed_at") and name in (r.get("salla_carrier_keys") or [])]
    if named and {r["courier_key"] for r in named} != {parties[0]["courier_key"]}:
        _reject("shipping_current_carrier_unresolved")
    return parties[0]


def _raw_shipment_matches(raw, current):
    shipping = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
    rows = raw.get("shipments")
    rows = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
    if isinstance(raw.get("shipment"), dict):
        rows = [*rows, raw["shipment"]]
    if shipping.get("shipment_id") is not None:
        rows = [*rows, {**shipping, "id": shipping["shipment_id"]}]
    if raw.get("salla_shipment_id") is not None:
        rows = [*rows, {**raw, "id": raw["salla_shipment_id"]}]
    selected = [r for r in rows if isinstance(r, dict) and _text(r.get("id") or r.get("shipment_id")) == _text(current["shipment_id"])]
    if not selected:
        return False
    for row in selected:
        if _inactive(row):
            return False
        company = row.get("courier") or row.get("company") or {}
        if isinstance(company, dict):
            code = _text(company.get("code"))
            name = _text(company.get("name"))
            if code and code != _text(current.get("company_code")):
                return False
            if name and name != _text(current.get("company_name")):
                return False
        tracking = row.get("tracking") if isinstance(row.get("tracking"), dict) else {}
        awb = next((_text(row.get(k)) for k in ("tracking_number", "tracking_id", "shipping_number", "awb") if _text(row.get(k))), "") or _text(tracking.get("number"))
        if awb != _text(current["tracking_number"]):
            return False
    return True


async def capture_native_fee_proof(db, *, owner, facts, raw):
    """Optional new-evidence binding; missing proof never blocks COD sealing."""
    try:
        _, current, key = await _current(db, owner, facts["order_number"], facts["order_id"])
        if key != _text(facts.get("source_carrier_key")) or not _raw_shipment_matches(raw, current):
            return None
        return {"schema": "current_courier_fee_proof_v1", "carrier_key": key,
                "shipment_id": _text(current["shipment_id"]), "waybill": _text(current["tracking_number"])}
    except CurrentShippingError:
        return None


async def require_native_current_fee(db, *, owner, evidence, setup):
    if _inactive(evidence):
        _reject("shipping_current_shipment_inactive")
    _, current, key = await _current(db, owner, evidence.get("order_number"), evidence.get("order_id"))
    party = _native_party(setup, current, key)
    if key != _text(evidence.get("source_carrier_key")) or party["courier_key"] != evidence.get("party_id"):
        _reject("shipping_current_carrier_conflict")
    proof = evidence.get("current_shipping_fee_proof")
    expected = {"schema": "current_courier_fee_proof_v1", "carrier_key": key,
                "shipment_id": _text(current["shipment_id"]), "waybill": _text(current["tracking_number"])}
    if proof != expected or evidence.get("delivery_status") != "delivered":
        _reject("shipping_current_shipment_evidence_required")


async def require_imported_current_fee(db, *, owner, evidence, rate, normalize):
    # Export reference is the public order number when the export also carries
    # an internal Salla order ID/number. Never guess across multiple owners.
    _, current, key = await _current(db, owner, evidence.get("export_order_reference") or evidence.get("order_number"))
    if _inactive(evidence):
        _reject("shipping_current_shipment_inactive")
    name = _text(current.get("company_name"))
    if current.get("company_code"):
        setup = await db[SETUP].find_one({"_id": owner}) or {}
        party = _native_party(setup, current, key)
        if normalize(party.get("name")) not in (rate.get("aliases_normalized") or []):
            _reject("shipping_current_carrier_conflict")
    if not name or normalize(name) not in (rate.get("aliases_normalized") or []):
        _reject("shipping_current_carrier_conflict")
    if _text(evidence.get("waybill")) != _text(current["tracking_number"]):
        _reject("shipping_current_shipment_evidence_required")
    shipment = evidence.get("salla_shipment_id") or evidence.get("shipment_id")
    if shipment is None and current.get("superseded_shipment_ids"):
        # A CSV waybill alone cannot disambiguate replacement shipments that
        # reuse a waybill. Require the actual current shipment identity.
        _reject("shipping_current_shipment_evidence_required")
    if shipment is not None and _text(shipment) != _text(current["shipment_id"]):
        _reject("shipping_current_shipment_evidence_required")
