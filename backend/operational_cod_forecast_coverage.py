"""Account for every open COD candidate before certifying a forecast total."""
from collections import Counter, defaultdict
from decimal import Decimal

from assembly_status_policy import status_values
from operational_balance_sources import instant
from operational_payment_evidence import PAYMENT_METHODS, NON_PROVIDER_METHODS
from operational_prestart_cod_forecast import COD
from order_currency import SUPPORTED_ORDER_CURRENCIES
from salla_payment_collection import order_page_source_current


def _methods(raw):
    payment = raw.get("payment") or {}
    if not isinstance(payment, dict) or raw.get("payment_methods") or payment.get("methods"):
        return set()
    values = []
    for value in (raw.get("payment_method"), payment.get("method")):
        if isinstance(value, dict):
            values.extend(value[k] for k in ("code", "slug") if value.get(k))
        elif value:
            values.append(value)
    return {v.strip().lower() for v in values if isinstance(v, str)}


def project(raw_orders, data, state, started, stamp, accepted, *, open_statuses, closed_statuses):
    """No missing records, stale versions, unknown methods, or silently skipped COD."""
    candidates, currencies, issues = defaultdict(set), set(), []
    known_non_cod = (set().union(*PAYMENT_METHODS.values()) | NON_PROVIDER_METHODS) - COD
    normalize = lambda names: {value for name in names for value in status_values(name)}
    opened, closed = normalize(open_statuses), normalize(closed_statuses)
    identities = Counter(str(raw.get("id") or "") for _, raw in raw_orders)
    numbers = Counter(str(raw.get("reference_id") or raw.get("order_number") or "") for _, raw in raw_orders)

    def reject(code, oid=None, currency=None):
        issues.append({"code": code, "source_id": oid, "currency": currency})

    if len(raw_orders) != len(data["unified_orders"]):
        reject("cod_forecast_canonical_source_missing")
    for source, raw in raw_orders:
        oid = str(raw.get("id") or "")
        amounts = raw.get("amounts")
        total = amounts.get("total") if isinstance(amounts, dict) else None
        ccy = raw.get("currency") or (total.get("currency") if isinstance(total, dict) else None)
        ccy = ccy if ccy in SUPPORTED_ORDER_CURRENCIES else None
        if ccy:
            currencies.add(ccy)
        try:
            created = instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
            if created > stamp:
                continue
            number = str(raw.get("reference_id") or raw.get("order_number") or "")
            version = instant(raw.get("updated_at") or raw.get("date_updated") or created)
            if (not oid or not number or identities[oid] != 1 or numbers[number] != 1
                    or str(source.get("order_number") or "") != number
                    or source.get("salla_order_id") is not None and str(source["salla_order_id"]) != oid
                    or not created <= version <= stamp or not order_page_source_current(source, raw)):
                reject("cod_forecast_identity_or_version_unverified", oid, ccy)
                continue
            states = status_values(raw.get("status")) | status_values(raw.get("status_slug"))
            states |= status_values(source.get("order_status")) | status_values(source.get("order_status_slug"))
            if states and states <= closed:
                continue
            if not states or not states <= opened:
                reject("cod_forecast_open_status_unverified", oid, ccy)
                continue
            methods = _methods(raw)
            if methods and methods <= known_non_cod:
                continue
            if not methods or not methods <= COD or ccy is None:
                reject("cod_forecast_payment_scope_unverified", oid, ccy)
                continue
            candidates[ccy].add(oid)
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            reject("cod_forecast_source_unverified", oid, ccy)

    accepted_by_id = defaultdict(list)
    for row in accepted:
        accepted_by_id[row["currency"], row["order_id"]].append(row)
    result = {}
    for currency in currencies | set(candidates):
        missing = sorted(oid for oid in candidates[currency] if len(accepted_by_id[currency, oid]) != 1)
        for oid in missing:
            reject("cod_forecast_collection_evidence_incomplete", oid, currency)
        values = [accepted_by_id[currency, oid][0] for oid in candidates[currency] if oid not in missing]
        relevant = [row for row in issues if row["currency"] in (None, currency)]
        complete = not relevant
        result[currency] = {"complete": complete, "candidate_orders": len(candidates[currency]),
            "positive_orders": sum(Decimal(row["amount"]) > 0 for row in values),
            "amount": format(sum((Decimal(row["amount"]) for row in values), Decimal(0)), ".2f") if complete else None,
            "issues": relevant, "affected_order_count": len({row["source_id"] for row in relevant if row["source_id"]}),
            "unidentified_issue_count": sum(not row["source_id"] for row in relevant)}
    return {"version": 1, "scope": "all_open_canonical_orders", "started_at": started.isoformat(),
            "as_of": stamp.isoformat(), "source_record_count": len(data["unified_orders"]),
            "by_currency": result, "issues": issues}
