"""Complete missing native bank identity with a bounded, version-bound source write."""
from datetime import datetime, timedelta, timezone

from operational_balance_bank_orders import NATIVE_BANKS, native_paid_amount
from operational_payment_evidence import _amount, _instant
from .service import call_salla
from .sync import _enrich_order_receiving_bank


_RETRY = "salla_bank_details_retry_after"
_RAW = "raw_by_source.salla_direct"
_PROJECTION = {"_id": 0, "user_id": 1, "order_number": 1, "order_id": 1, "salla_order_id": 1,
               "g47_salla_snapshot": 1, "order_status_slug": 1,
               "last_salla_direct_status_source_updated_at": 1, _RAW: 1}


def _bank_names(raw):
    payment = raw.get("payment") or {}
    values = []
    for container in (raw, payment):
        if not isinstance(container, dict):
            continue
        values.extend(container.get(key) for key in ("receiving_bank_name", "receiving_bank", "bank_name", "receiving_bank_code"))
        bank = container.get("bank")
        if isinstance(bank, dict):
            values.extend((bank.get("bank_name"), bank.get("name")))
        elif bank is not None:
            values.append(bank)
    return [value for value in values if value not in (None, "", {}, [])]


def _store_bank_ids(raw):
    values = [raw.get("store_bank_id")]
    for container, key in ((raw.get("payment"), "store_bank_id"), (raw.get("bank"), "id")):
        if isinstance(container, dict):
            values.append(container.get(key))
    return {str(value).strip() for value in values if value is not None} - {""}


def _signature(raw):
    total = (raw.get("amounts") or {}).get("total", raw.get("total"))
    return (str(raw["id"]), str(raw.get("reference_id") or raw.get("order_number")),
            _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date")),
            _instant(raw.get("updated_at") or raw.get("date_updated")), _amount(total, "SAR"),
            sorted((leg["payment_method"], _amount(leg["amount"], "SAR"), leg.get("transaction_reference"))
                   for leg in raw["payment_methods"]))


async def acquire_missing(db, owner, issues, *, limit=4):
    """Add verified bank identity only. The next ordinary refresh recognizes its bank credit."""
    targets = {str(row["source_id"]) for row in issues
               if row.get("code") == "operational_order_bank_binding_incomplete" and row.get("source_id")}
    if not targets:
        return
    identities = list(targets) + [int(value) for value in targets if value.isascii() and value.isdigit()]
    now = datetime.now(timezone.utc)
    for _ in range(min(limit, 4)):
        source = await db.unified_orders.find_one_and_update(
            {"user_id": owner, _RAW + ".id": {"$in": identities},
             "$or": [{_RETRY: {"$exists": False}}, {_RETRY: {"$lte": now}}]},
            {"$set": {_RETRY: now + timedelta(minutes=15)}}, projection=_PROJECTION)
        if not source:
            break
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        if _bank_names(raw):
            continue
        previous_bank_ids = _store_bank_ids(raw)
        if len(previous_bank_ids) > 1:
            continue
        number, identity = str(source.get("order_number") or ""), str(raw.get("id") or "")
        try:
            paid = native_paid_amount(raw, source, owner=owner, order_number=number,
                provider_order_id=identity, currency="SAR", total=(raw.get("amounts") or {}).get("total", raw.get("total")), as_of=now)
            response = await call_salla(db, owner, "GET", "/orders/" + identity)
            details = response.get("data") if isinstance(response, dict) else None
            if not isinstance(details, dict) or _signature(details) != _signature(raw):
                continue
            observed = native_paid_amount(details, source, owner=owner, order_number=number,
                provider_order_id=identity, currency="SAR",
                total=(details.get("amounts") or {}).get("total", details.get("total")), as_of=now)
            if observed != paid:
                continue
            native_bank_ids = _store_bank_ids(details)
            if len(native_bank_ids) > 1 or (previous_bank_ids and previous_bank_ids != native_bank_ids):
                continue
            details = await _enrich_order_receiving_bank(db, owner, details)
            bank = details.get("bank") or {}
            if not isinstance(bank, dict) or not isinstance(bank.get("bank_name"), str):
                continue
            names = _bank_names(details)
            codes = {next((code for code, labels in NATIVE_BANKS.items()
                           if isinstance(name, str) and " ".join(name.split()).casefold() in labels), None)
                     for name in names}
            if len(codes) != 1 or None in codes:
                continue
            previous_bank = raw.get("bank") or {}
            if (not isinstance(previous_bank, dict)
                    or (previous_bank.get("bank_id") is not None
                        and str(previous_bank["bank_id"]) != str(bank.get("bank_id")))):
                continue
        except (ValueError, KeyError, TypeError, ArithmeticError):
            continue
        # Every source byte and watermark must still be the inspected version.
        # This writes bank identity without invoking source or fulfillment observers.
        selector = {"user_id": owner, "order_number": number, _RAW: raw,
                    **{key: source.get(key) for key in ("order_id", "salla_order_id", "g47_salla_snapshot",
                        "order_status_slug", "last_salla_direct_status_source_updated_at")}}
        native_bank = {key: bank[key] for key in ("id", "bank_id", "bank_name", "name") if bank.get(key) is not None}
        await db.unified_orders.update_one(selector, {"$set": {_RAW + ".bank": {**previous_bank, **native_bank}}})
