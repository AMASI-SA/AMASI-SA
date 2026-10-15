"""Read-only customer acquisition and repeat-purchase cohort report.

ADR-001 mapping: this is an additive, tenant-scoped reader over Salla-owned
orders (principles 1, 4, 7, 9 and 11).  It reuses Mezan's attribution ledger
for source and profit facts, never stores customer identifiers, never treats
unknown money as zero, and performs no provider, order or accounting writes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mezan_attribution_order_ledger import LEDGER_COLLECTION

CONTRACT_VERSION = "mezan_customer_cohort_profit_report_v1"
DEFAULT_MAX_ORDERS = 100_000

_EXCLUDED_STATUS_TOKENS = (
    "cancelled", "canceled", "deleted", "refunded", "returned", "restored",
    "ملغي", "ملغى", "ملغية", "محذوف", "مسترجع", "مرتجع", "تم الاسترجاع",
)


def _text(value: Any, limit: int = 300) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return " ".join(str(value).split()).strip()[:limit]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _money(value: float) -> float:
    return round(float(value), 2)


def _order_key(order: dict[str, Any]) -> str:
    for key in ("order_id", "id", "reference_id", "order_number"):
        value = _text(order.get(key), 180)
        if value:
            return value
    raw = _dict(_dict(order.get("raw_by_source")).get("salla_direct"))
    for key in ("id", "reference_id", "order_number"):
        value = _text(raw.get(key), 180)
        if value:
            return value
    return ""


def _order_date(order: dict[str, Any]) -> date | None:
    for key in (
        "created_at_utc", "order_created_at", "order_date", "created_at",
        "source_created_at",
    ):
        value = order.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            return value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                try:
                    return date.fromisoformat(str(value)[:10])
                except (TypeError, ValueError):
                    continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date()
    return None


def _salla_raw(order: dict[str, Any]) -> dict[str, Any]:
    return _dict(_dict(order.get("raw_by_source")).get("salla_direct"))


def _has_salla_evidence(order: dict[str, Any]) -> bool:
    sources = order.get("data_sources")
    if isinstance(sources, list) and "salla_direct" in sources:
        return True
    if _text(order.get("data_source"), 80).casefold() == "salla_direct":
        return True
    return bool(_salla_raw(order))


def _normalize_phone(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if digits.startswith("00966"):
        digits = digits[2:]
    if digits.startswith("05") and len(digits) == 10:
        digits = f"966{digits[1:]}"
    return digits


def _customer_identity(order: dict[str, Any]) -> str:
    raw = _salla_raw(order)
    raw_customer = _dict(raw.get("customer"))
    customer = _dict(order.get("customer"))
    for value in (
        order.get("customer_id"), customer.get("id"), raw_customer.get("id"),
    ):
        normalized = _text(value, 180)
        if normalized:
            return f"salla:{normalized}"
    for value in (
        order.get("customer_mobile"), customer.get("mobile"), customer.get("phone"),
        raw_customer.get("mobile"), raw_customer.get("phone"),
    ):
        normalized = _normalize_phone(value)
        if len(normalized) >= 7:
            return f"phone:{normalized}"
    for value in (
        order.get("customer_email"), customer.get("email"), raw_customer.get("email"),
    ):
        normalized = _text(value, 240).casefold()
        if "@" in normalized:
            return f"email:{normalized}"
    return ""


def _status(order: dict[str, Any]) -> str:
    return _text(
        order.get("order_status_native")
        or order.get("status_native")
        or order.get("order_status")
        or order.get("status"),
        160,
    ).casefold().replace("_", " ")


def _financially_included(order: dict[str, Any], included_statuses: list[str]) -> bool:
    status = _status(order)
    if any(token in status for token in _EXCLUDED_STATUS_TOKENS):
        return False
    allowed = [_text(item, 160).casefold().replace("_", " ") for item in included_statuses]
    allowed = [item for item in allowed if item]
    if not allowed:
        return True
    return any(item == status or item in status or status in item for item in allowed)


def _source_bucket(ledger: dict[str, Any] | None) -> str:
    attribution = _dict(_dict(ledger).get("attribution"))
    if attribution.get("decision_safe") is True and attribution.get("quality") == "confirmed":
        return "confirmed_ad"
    source = _text(attribution.get("marketing_source"), 80).casefold()
    if source in {"direct", "whatsapp", "manual", "gift"}:
        return "explicit_non_ad"
    return "unresolved"


def _contribution_profit(ledger: dict[str, Any] | None) -> float | None:
    profit = _dict(_dict(ledger).get("profit"))
    if profit.get("known") is not True:
        return None
    revenue = _number(profit.get("revenue_sar"))
    cogs = _number(profit.get("cogs_sar"))
    ad_spend = _number(profit.get("allocated_ad_spend_sar"))
    if revenue is None or cogs is None or ad_spend is None:
        return None
    return _money(revenue - cogs - ad_spend)


def _new_money_coverage() -> dict[str, Any]:
    return {"known_orders": 0, "unknown_orders": 0, "known_amount_sar": 0.0}


def _add_contribution(target: dict[str, Any], ledger: dict[str, Any] | None) -> None:
    value = _contribution_profit(ledger)
    if value is None:
        target["unknown_orders"] += 1
        return
    target["known_orders"] += 1
    target["known_amount_sar"] += value


def _finalize_money_coverage(value: dict[str, Any]) -> dict[str, Any]:
    total = int(value["known_orders"]) + int(value["unknown_orders"])
    return {
        "known_orders": int(value["known_orders"]),
        "unknown_orders": int(value["unknown_orders"]),
        "coverage_pct": round((int(value["known_orders"]) / total) * 100, 2) if total else 0.0,
        "known_amount_sar": _money(value["known_amount_sar"]),
        "partial": bool(value["unknown_orders"]),
        "unknown_is_zero": False,
    }


def _cohort_row(key: str, label: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "customers": 0,
        "first_orders": 0,
        "first_order_sales_sar": 0.0,
        "later_orders": 0,
        "first_acquisition_cost": _new_money_coverage(),
        "later_order_contribution_profit": _new_money_coverage(),
        "first_order_sources": {
            "confirmed_ad": 0,
            "explicit_non_ad": 0,
            "unresolved": 0,
        },
    }


def build_customer_cohort_report_from_rows(
    orders: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    *,
    as_of: date,
    included_statuses: list[str] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    """Aggregate privacy-safe cohorts from Salla orders and ledger facts."""
    included = included_statuses or []
    ledgers = {
        _text(row.get("order_key"), 180): row
        for row in ledger_rows
        if isinstance(row, dict) and _text(row.get("order_key"), 180)
    }
    customers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    coverage = {
        "input_orders": len(orders),
        "salla_orders": 0,
        "financially_included_orders": 0,
        "excluded_status_orders": 0,
        "future_orders_excluded": 0,
        "missing_order_date": 0,
        "missing_customer_identity": 0,
        "ledger_matched_orders": 0,
        "ledger_unmatched_orders": 0,
        "truncated": bool(truncated),
    }

    for order in orders:
        if not isinstance(order, dict) or not _has_salla_evidence(order):
            continue
        coverage["salla_orders"] += 1
        created = _order_date(order)
        if created is None:
            coverage["missing_order_date"] += 1
            continue
        if created > as_of:
            coverage["future_orders_excluded"] += 1
            continue
        if not _financially_included(order, included):
            coverage["excluded_status_orders"] += 1
            continue
        identity = _customer_identity(order)
        if not identity:
            coverage["missing_customer_identity"] += 1
            continue
        key = _order_key(order)
        ledger = ledgers.get(key)
        coverage["financially_included_orders"] += 1
        coverage["ledger_matched_orders" if ledger else "ledger_unmatched_orders"] += 1
        amount = _number(order.get("total_amount_sar"))
        if amount is None:
            amount = _number(order.get("total_amount") or order.get("total"))
        customers[identity].append({
            "order_key": key,
            "created": created,
            "sales_sar": amount,
            "ledger": ledger,
        })

    cohorts = {
        "first_purchase_0_30_days": _cohort_row(
            "first_purchase_0_30_days", "أول شراء خلال آخر 0–30 يومًا"
        ),
        "first_purchase_31_60_days": _cohort_row(
            "first_purchase_31_60_days", "أول شراء منذ 31–60 يومًا"
        ),
    }
    returning = {
        "last_30_days": {
            "customers": 0, "orders": 0,
            "contribution_profit": _new_money_coverage(),
        },
        "last_60_days": {
            "customers": 0, "orders": 0,
            "contribution_profit": _new_money_coverage(),
        },
    }
    acquisition = _new_money_coverage()
    later_all = _new_money_coverage()
    first_sources = {"confirmed_ad": 0, "explicit_non_ad": 0, "unresolved": 0}
    returning_customers = 0
    repeat_orders = 0
    window_30_start = as_of - timedelta(days=29)
    window_60_start = as_of - timedelta(days=59)

    for customer_orders in customers.values():
        customer_orders.sort(key=lambda row: (row["created"], row["order_key"]))
        first = customer_orders[0]
        later = customer_orders[1:]
        age_days = (as_of - first["created"]).days
        source = _source_bucket(first["ledger"])
        first_sources[source] += 1
        first_profit = _dict(_dict(first["ledger"]).get("profit"))
        first_ad_cost = _number(first_profit.get("allocated_ad_spend_sar"))
        if source == "confirmed_ad" and first_ad_cost is not None:
            acquisition["known_orders"] += 1
            acquisition["known_amount_sar"] += first_ad_cost
        elif source == "confirmed_ad":
            acquisition["unknown_orders"] += 1

        cohort = None
        if 0 <= age_days <= 30:
            cohort = cohorts["first_purchase_0_30_days"]
        elif 31 <= age_days <= 60:
            cohort = cohorts["first_purchase_31_60_days"]
        if cohort is not None:
            cohort["customers"] += 1
            cohort["first_orders"] += 1
            cohort["first_order_sources"][source] += 1
            if first["sales_sar"] is not None:
                cohort["first_order_sales_sar"] += first["sales_sar"]
            if source == "confirmed_ad" and first_ad_cost is not None:
                cohort["first_acquisition_cost"]["known_orders"] += 1
                cohort["first_acquisition_cost"]["known_amount_sar"] += first_ad_cost
            elif source == "confirmed_ad":
                cohort["first_acquisition_cost"]["unknown_orders"] += 1

        if later:
            returning_customers += 1
        for row in later:
            repeat_orders += 1
            _add_contribution(later_all, row["ledger"])
            if cohort is not None:
                cohort["later_orders"] += 1
                _add_contribution(cohort["later_order_contribution_profit"], row["ledger"])

        in_30 = [row for row in later if row["created"] >= window_30_start]
        in_60 = [row for row in later if row["created"] >= window_60_start]
        for key, rows in (("last_30_days", in_30), ("last_60_days", in_60)):
            if rows:
                returning[key]["customers"] += 1
            returning[key]["orders"] += len(rows)
            for row in rows:
                _add_contribution(returning[key]["contribution_profit"], row["ledger"])

    cohort_rows = []
    for cohort in cohorts.values():
        cohort["first_order_sales_sar"] = _money(cohort["first_order_sales_sar"])
        cohort["first_acquisition_cost"] = _finalize_money_coverage(
            cohort["first_acquisition_cost"]
        )
        cohort["later_order_contribution_profit"] = _finalize_money_coverage(
            cohort["later_order_contribution_profit"]
        )
        cohort_rows.append(cohort)
    for row in returning.values():
        row["contribution_profit"] = _finalize_money_coverage(row["contribution_profit"])

    return {
        "contract_version": CONTRACT_VERSION,
        "as_of": as_of.isoformat(),
        "read_only": True,
        "privacy": {"customer_identifiers_returned": False, "aggregate_only": True},
        "summary": {
            "identified_customers": len(customers),
            "returning_customers_all_time": returning_customers,
            "repeat_orders_all_time": repeat_orders,
            "first_order_sources": first_sources,
            "first_acquisition_cost": _finalize_money_coverage(acquisition),
            "later_order_contribution_profit": _finalize_money_coverage(later_all),
        },
        "cohorts": cohort_rows,
        "returning": returning,
        "coverage": coverage,
        "definitions": {
            "matched_salla_order": "Order carries direct Salla source evidence and is financially included by the store report policy.",
            "first_purchase": "Earliest financially included Salla order for the resolved customer identity on or before as_of.",
            "returning_customer": "Customer with at least one financially included order after the first purchase.",
            "first_acquisition_cost": "Allocated ad spend on the first order, reported only for confirmed decision-safe ad attribution.",
            "later_order_contribution_profit": "Later-order revenue minus product cost minus allocated ad spend; unknown unless all three facts are present.",
            "confirmed_ad": "Exact, decision-safe campaign attribution.",
            "explicit_non_ad": "Explicit direct, WhatsApp, manual or gift source.",
            "unresolved": "Inferred, ambiguous or unattributed source.",
        },
        "guardrails": {
            "unknown_money_treated_as_zero": False,
            "provider_purchase_counts_used_for_attribution": False,
            "customer_rows_returned": False,
            "external_writes": False,
        },
    }


async def _to_list(cursor: Any, *, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


async def build_customer_cohort_report(
    db: Any,
    user_id: str,
    *,
    as_of: date,
    included_statuses: list[str] | None = None,
    max_orders: int = DEFAULT_MAX_ORDERS,
) -> dict[str, Any]:
    """Load bounded tenant data and return the aggregate read-only report."""
    cap = max(1, min(DEFAULT_MAX_ORDERS, int(max_orders)))
    order_query = {
        "user_id": user_id,
        "$or": [
            {"data_source": "salla_direct"},
            {"data_sources": "salla_direct"},
            {"raw_by_source.salla_direct": {"$exists": True}},
        ],
    }
    projection = {
        "_id": 0,
        "order_id": 1,
        "id": 1,
        "reference_id": 1,
        "order_number": 1,
        "created_at_utc": 1,
        "order_created_at": 1,
        "order_date": 1,
        "created_at": 1,
        "source_created_at": 1,
        "order_status_native": 1,
        "status_native": 1,
        "order_status": 1,
        "status": 1,
        "total_amount_sar": 1,
        "total_amount": 1,
        "total": 1,
        "data_source": 1,
        "data_sources": 1,
        "customer_id": 1,
        "customer_mobile": 1,
        "customer_email": 1,
        "customer.id": 1,
        "customer.mobile": 1,
        "customer.phone": 1,
        "customer.email": 1,
        "raw_by_source.salla_direct.id": 1,
        "raw_by_source.salla_direct.reference_id": 1,
        "raw_by_source.salla_direct.order_number": 1,
        "raw_by_source.salla_direct.customer.id": 1,
        "raw_by_source.salla_direct.customer.mobile": 1,
        "raw_by_source.salla_direct.customer.phone": 1,
        "raw_by_source.salla_direct.customer.email": 1,
    }
    orders_cursor = db.unified_orders.find(order_query, projection).sort("order_date", 1).limit(cap + 1)
    orders = await _to_list(orders_cursor, limit=cap + 1)
    truncated = len(orders) > cap
    orders = orders[:cap]

    ledger_cursor = db[LEDGER_COLLECTION].find(
        {
            "user_id": user_id,
            "order_created_at": {"$lte": f"{as_of.isoformat()}T23:59:59.999999+00:00"},
        },
        {"_id": 0, "user_id": 0, "line_items": 0, "evidence": 0},
    ).limit(cap + 1)
    ledger_rows = await _to_list(ledger_cursor, limit=cap + 1)
    truncated = truncated or len(ledger_rows) > cap
    return build_customer_cohort_report_from_rows(
        orders,
        ledger_rows[:cap],
        as_of=as_of,
        included_statuses=included_statuses,
        truncated=truncated,
    )


__all__ = [
    "CONTRACT_VERSION",
    "build_customer_cohort_report",
    "build_customer_cohort_report_from_rows",
]
