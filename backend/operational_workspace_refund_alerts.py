"""Read Salla refund indications and compare them with documented operational cases."""
from collections import defaultdict
from decimal import Decimal
from math import ceil

from operational_balance_service import fmt, money
from operational_balance_sources import instant
from operational_balance_store import now
from operational_customer_returns import observed_salla_refund
from operational_financial_position_state import read_financial_position_state
from operational_provider_statement_reconciliation import statement_refund_cases
from order_status_policy import default_category_for
from salla_payment_collection import provider_order_page_current


RAW = "raw_by_source.salla_direct."
TRANSACTIONS = "payment_actions.refund_action.refunded_transactions"
# Use the Reports refund monitor's status indicators and the native Salla status slug.
STATUS = {"$regex": "refund|مسترج|^restored$", "$options": "i"}
STATUS_FIELDS = ("status", "status.slug", "status.name")
RAW_FIELDS = ("id", "reference_id", "order_number", "updated_at", "date_updated",
              "date", "created_at", "order_date", "status", TRANSACTIONS)
PROJECTION = {"_id": 0, "user_id": 1, "order_number": 1, "order_id": 1, "salla_order_id": 1,
              "g47_salla_snapshot": 1, "order_status_slug": 1, "last_salla_direct_status_source_updated_at": 1,
              **{RAW + field: 1 for field in RAW_FIELDS}}


def timestamp(value):
    try:
        return instant(value)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def documented_amounts(state):
    amounts = defaultdict(Decimal)
    for case in [*state.get("customer_returns", []), *statement_refund_cases(state)]:
        if case.get("status") in {"refunded", "salla_refund_confirmed", "salla_refund_recorded", "provider_matched"}:
            amounts[str(case["order_id"])] += money(case["amount"])
    return amounts


def alert_row(source, owner, started, cutoff, documented):
    raw = (source.get("raw_by_source") or {}).get("salla_direct")
    if not isinstance(raw, dict):
        return None
    number = str(raw.get("reference_id") or raw.get("order_number") or "")
    oid = str(raw.get("id") or "")
    if not provider_order_page_current(source, raw, owner=owner, order_number=number, provider_order_id=oid):
        return None
    created = timestamp(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
    updated = timestamp(raw.get("updated_at") or raw.get("date_updated"))
    if not created or created > cutoff or updated and (updated > cutoff or updated < created):
        return None
    actions = raw.get("payment_actions") or {}
    action = actions.get("refund_action") if isinstance(actions, dict) else {}
    evidence = action.get("refunded_transactions") if isinstance(action, dict) else None
    dated_events, window_events = [], []
    invalid_date = False
    for event in evidence if isinstance(evidence, list) else []:
        if isinstance(event, dict):
            explicit = event.get("refunded_at") or event.get("executed_at") or event.get("date")
            at = timestamp(explicit)
            if at and created <= at <= cutoff:
                dated_events.append(at)
                if at >= started:
                    window_events.append(event)
            elif explicit is not None:
                invalid_date = True
    if created < started and not window_events:
        # An unrelated edit to a pre-opening order does not date its refund.
        return None
    status = raw.get("status")
    names = (status.get("slug"), status.get("name")) if isinstance(status, dict) else (status,)
    restored = any(isinstance(name, str) and (name.lower() == "restored" or default_category_for(name) == "refunded")
                   for name in names)
    if created < started:
        observed = observed_salla_refund({"payment_actions": {"refund_action": {"refunded_transactions": window_events}}})
    else:
        observed = observed_salla_refund(raw)
    if observed["status"] == "not_observed" and not restored:
        return None
    amount = None
    if observed["status"] == "observed_requires_execution_evidence" and not invalid_date:
        remaining = Decimal(observed["amount"]) - documented.get(oid, Decimal(0))
        if remaining <= 0:
            return None
        amount = fmt(remaining)
    elif documented.get(oid):
        return None
    observed_at = max([at for at in [updated, *dated_events] if at], default=None)
    return {"order_number": number, "amount": amount, "currency": "SAR" if amount is not None else None,
            "observed_at": observed_at.isoformat() if observed_at else None}


async def read_alerts(db, owner, page, page_size):
    state = await read_financial_position_state(db, owner)
    started, cutoff = timestamp(state.get("started_at")), instant(now())
    items = []
    if started:
        documented = documented_amounts(state)
        query = {"user_id": owner, "$or": [
            {RAW + TRANSACTIONS + ".0": {"$exists": True}},
            *[{RAW + field: STATUS} for field in STATUS_FIELDS]]}
        numbers, ambiguous = set(), set()
        async for source in db.unified_orders.find(query, PROJECTION):
            item = alert_row(source, owner, started, cutoff, documented)
            if item:
                number = item["order_number"]
                if number in numbers:
                    ambiguous.add(number)
                else:
                    numbers.add(number)
                    items.append(item)
        items = [item for item in items if item["order_number"] not in ambiguous]
        items.sort(key=lambda item: (item["observed_at"] or "", item["order_number"]), reverse=True)
    total = len(items)
    pages = max(1, ceil(total / page_size))
    page = min(page, pages)
    return {"items": items[(page - 1) * page_size:page * page_size], "page": page, "page_size": page_size,
            "total": total, "total_pages": pages}
