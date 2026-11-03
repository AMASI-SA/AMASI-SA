"""Customer COD forecast for evidenced open external shipments, from already-loaded rows."""
from collections import Counter, defaultdict
from decimal import Decimal

from assembly_status_policy import canonical_status
from operational_balance_sources import instant
from operational_delivery_evidence import _inactive, _status, _text
from operational_payment_evidence import _amount
from order_currency import SUPPORTED_ORDER_CURRENCIES
from salla_shipping import extract_shipping, SHIPMENT_FIELDS
from accounting_shipping_current_guard import _store_driver
from shipping_companies import normalize_shipping_company

COD = {"cod", "cash_on_delivery", "cashondelivery", "cash on delivery",
       "الدفع عند الاستلام", "دفع عند الاستلام", "دفع عند الإستلام"}
TRANSIT = {"created", "pending_pickup", "picked_up", "in_transit", "shipped", "out_for_delivery", "shipping"}


def _explicit_amount(containers, fields, currency):
    values = [row[key] for row in containers for key in fields if row.get(key) is not None]
    if not any(isinstance(v, dict) and v.get("currency") == currency for v in values):
        raise ValueError("currency_or_amount_missing")
    amounts = {_amount(value, currency) for value in values}
    if len(amounts) != 1:
        raise ValueError("conflicting_amounts")
    return amounts.pop()


def _remaining(raw):
    currency = raw["currency"]
    if currency not in SUPPORTED_ORDER_CURRENCIES:
        raise ValueError("unverified_currency")
    payment, actions, amounts = (raw.get(field) or {} for field in ("payment", "payment_actions", "amounts"))
    remaining, refund = actions.get("remaining_action") or {}, actions.get("refund_action") or {}
    fields = [raw, payment, raw.get("remaining_action") or {}, remaining]
    if (any(obj.get("currency") not in (None, currency) for obj in [*fields, refund, amounts]) or
            raw.get("payment_methods") or refund.get("refunded_transactions") or
            any(row.get(key) for row in (raw, payment) for key in ("refunds", "refunded_at", "cancelled_at")) or
            any(_amount(row[key], currency) for row in (raw, payment, refund) for key in ("refunded_amount", "refund_amount")
                if row.get(key) is not None)):
        raise ValueError("payment_history")
    gross = _explicit_amount([raw, amounts], ("total", "total_amount"), currency)
    paid = _explicit_amount([*fields, refund], ("paid_amount",), currency)
    due = _explicit_amount(fields, ("remaining_amount",), currency)
    if gross != paid + due or any(str(row.get(key) or "").strip().lower() in {
            "refunded", "partially_refunded", "voided", "cancelled", "canceled", "paid", "captured", "مرتجع", "مدفوع"}
            for row in (raw, payment) for key in ("payment_status", "collection_status", "status")):
        raise ValueError("conflicting_payment_status")
    return currency, due


def _open_waybill(source, raw, started, stamp):
    current = source.get("salla_shipping_current") or {}
    observed = extract_shipping(raw) or {}
    shipments = raw.get("shipments")
    if not isinstance(shipments, list) or len(shipments) != 1 or not isinstance(shipments[0], dict):
        return False
    shipment = shipments[0]
    shipping = raw.get("shipping") or {}
    nested = raw.get("salla_shipping_current")
    if (raw.get("shipment") or shipping.get("shipment") or _inactive(current) or _inactive(shipment)
            or _inactive(shipping) or any(row.get("delivered_at") for row in (raw, source, current, shipment, shipping))
            or current.get("source_kind") not in {"shipment", "order"} or current.get("status") not in TRANSIT
            or current.get("shipment_id") in (current.get("superseded_shipment_ids") or [])
            or (nested is not None and nested != current)):
        return False
    if (_store_driver(current) or
            any(_status(shipping[key]) != current["status"] for key in ("shipment_status", "shipping_status", "status")
                if shipping.get(key)) or
            any(source.get(key) is not None and str(source[key]) != str(current.get(field))
                for key, field in (("shipping_company_code", "company_code"), ("shipping_company", "company_name"))) or
            any(raw.get(key) is not None and _text(raw[key]) != current["status"] for key in ("shipment_status", "shipping_status"))):
        return False
    for key in ("shipment_id","tracking_number","company_code","company_name","status"):
        if not _text(current.get(key)) or current[key] != observed.get(key):
            return False
    carrier = normalize_shipping_company(current["company_name"])[0]
    if carrier in {"unknown","pickup","mandoob","mandoob_riyadh"} or carrier.startswith("other:"):
        return False
    if any(source.get(k) is not None and str(source[k]) != str(current.get(v)) for k,v in SHIPMENT_FIELDS.items()
           if v in {"shipment_id","tracking_number","status"}):
        return False
    versions = [raw.get("updated_at"), current.get("shipment_updated_at"), current.get("provider_updated_at")]
    watermark = source.get("g47_salla_snapshot") or {}
    if watermark.get("cancelled") or watermark.get("requires_authoritative_refresh") or watermark.get("component_pending"):
        return False
    if watermark.get("source_updated_at") is not None and instant(watermark["source_updated_at"]) != instant(raw["updated_at"]):
        return False
    if shipment.get("updated_at") is not None:
        versions.append(shipment["updated_at"])
    return all(started <= instant(v) <= stamp for v in versions)


def prestart_open_cod(raw_orders, data, state, started, stamp, ids, numbers, open_statuses):
    """Require positive current state; absence of a delivery fact alone proves nothing."""
    engine = state.get("engine") or {}
    booked = set(engine.get("obligations", {})) | set(engine.get("cod_reports", {})) | set(engine.get("facts", {}))
    linked = [*data["store_delivery_assignments"], *state.get("movements", []), *state.get("customer_returns", []),
              *(state.get("driver_payment_review_facts") or {}).values()]
    stored_ids = Counter(_text(doc.get("salla_order_id")) for doc in data["unified_orders"])
    stored_numbers = Counter(_text(doc.get("order_number")) for doc in data["unified_orders"])
    linked_ids = {_text(row.get("order_id")) for row in linked}
    linked_numbers = {_text(row.get("order_number")) for row in linked}
    booked.update(a.get("obligation_id") for row in state.get("movements", []) for a in row.get("allocations", []))
    groups = defaultdict(lambda: [0, Decimal(0)])
    withheld = 0
    for source, raw in raw_orders:
        try:
            payment = raw.get("payment") or {}
            methods = {str(m).strip().lower() for m in (raw.get("payment_method"), payment.get("method")) if m}
            if not methods.intersection(COD):
                continue
            if instant(raw.get("date") or raw.get("created_at") or raw.get("order_date")) >= started:
                continue
            oid, num = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
            if (not methods <= COD or not oid or not num or ids[oid] != 1 or numbers[num] != 1
                    or _text(source.get("order_number")) != num or _text(source.get("salla_order_id")) != oid
                    or stored_ids[oid] != 1 or stored_numbers[num] != 1
                    or (raw.get("order_number") is not None and _text(raw["order_number"]) != num)
                    or num in linked_numbers or oid in linked_ids or any(prefix + oid in booked for prefix in
                       ("cod:", "credit:cod:", "shipping:", "delivery_event:", "cod_collection:", "cod_customer_confirmation:"))
                    or canonical_status(source) not in open_statuses or not _open_waybill(source, raw, started, stamp)):
                withheld += 1
                continue
            currency, due = _remaining(raw)
            if due > 0:
                groups[currency][0] += 1
                groups[currency][1] += due
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            withheld += 1
    return {"by_currency":{ccy:{"orders":count, "amount":format(amount, ".2f")} for ccy,(count,amount) in groups.items()},
            "withheld_candidates":withheld}


def snapshot_cod_preview(exposure, started_at, as_of, key='prestart_open_cod'):
    if not isinstance(exposure, dict) or exposure.get("status") != "preview":
        return {}
    try:
        if instant(exposure["started_at"]) == instant(started_at) and instant(exposure["as_of"]) == instant(as_of) == instant(exposure["engine_as_of"]):
            return (exposure.get(key) or {}).get("by_currency") or {}
    except (ValueError, KeyError, TypeError):
        pass
    return {}
