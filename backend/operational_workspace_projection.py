"""Read-only statement and display projections over authorized canonical rows.

Display compaction never supplies accounting totals. Statements use the complete
ledger; historical balances are withheld unless that ledger reconciles to the
canonical current balance and the reader can see every movement and the balance.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from operational_balance_store import fail
from operational_financial_activity_runtime import preparation_checkpoint


RIYADH = ZoneInfo("Asia/Riyadh")
CASH_KINDS = {"bank", "cash", "employee_custody"}


def decimal_text(value):
    return format(Decimal(value).quantize(Decimal(".01")), ".2f")


def strict_day(value):
    if not isinstance(value, str):
        raise ValueError("date_required")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date_invalid")
    return parsed


def row_order(row):
    stamp = datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat((row.get("recorded_at") or "").replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            stamp = parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass
    # The cutover opening precedes activity even when its audit was stored later.
    return row["business_date"], 0 if row.get("reason") == "opening" else 1, stamp, row["id"]


def delta(row, kind):
    if row.get("informational"):
        return Decimal(0)
    positive = "for_us" if kind in CASH_KINDS else "for_party"
    return Decimal(row["amount"]) * (1 if row["side"] == positive else -1)


def can_see_balances(resource):
    return resource["access"].get("balance", False) and resource["access"]["movements"] == "all"


def balance_note(status):
    if status == "unreconciled":
        return "تعذر مطابقة تفاصيل السجل مع الرصيد المعتمد؛ لا يعرض رصيد تاريخي حتى مراجعة المصدر."
    if status == "restricted":
        return "يعرض الكشف الحركات المسموحة فقط؛ الأرصدة التاريخية تتطلب صلاحية الرصيد وجميع الحركات."
    return ""


def signed_current_balance(resource):
    # Rows retain their historical API convention. Reconcile them against the
    # store's position in the same currency before exposing a running balance.
    kind = resource["kind"]
    if "position_balance" in resource:
        value = resource["position_balance"]
        return None if value is None else Decimal(value) * (1 if kind in CASH_KINDS else -1)
    if kind == "provider" and "provider_position" in resource:
        position = resource["provider_position"]
        if not position.get("complete") or position.get("currency") != resource.get("currency") or position.get("net") is None:
            return None
        return -Decimal(position["net"])
    if kind == "ad_account" and "ad_amounts" in resource:
        amounts = resource["ad_amounts"] or {}
        native = amounts.get("native", amounts)
        if native.get("currency") != resource.get("currency") or native.get("payable") is None or native.get("prepaid") is None:
            return None
        return Decimal(native["payable"]) - Decimal(native["prepaid"])
    current = resource.get("balance")
    if not current:
        return None
    positive = "for_us" if resource["kind"] in CASH_KINDS else "for_party"
    return Decimal(current["amount"]) * (1 if current["side"] == positive else -1)


def projected_balance_status(resource, rows):
    if not can_see_balances(resource):
        return "restricted"
    if any(row.get("balance_after") is None for row in rows):
        return "unreconciled"
    if not rows and signed_current_balance(resource) != 0:
        return "unreconciled"
    return "available"


def with_running_balances(resource, rows):
    """Never derive actual balances from an own-only or incomplete activity list."""
    status = "restricted"
    if can_see_balances(resource):
        status = "available"
        try:
            for row in rows:
                strict_day(row["business_date"])
            if sum((delta(row, resource["kind"]) for row in rows), Decimal(0)) != signed_current_balance(resource):
                status = "unreconciled"
        except (ValueError, TypeError, InvalidOperation):
            status = "unreconciled"
    running = Decimal(0)
    result = []
    for row in sorted(rows, key=row_order):
        preparation_checkpoint()
        running += delta(row, resource["kind"])
        result.append({**row, "balance_after": decimal_text(running) if status == "available" else None})
    return list(reversed(result)), status


def automatic_source(state, item):
    """Classify paired recorded provenance, never free text or an ID alone."""
    engine = state.get("engine", {})
    identity = item.get("id", "")
    if item.get("kind") == "salary" and item.get("party_type") == "employee":
        day = (engine.get("salary_days") or {}).get(identity)
        expected = "salary:" + item["party_id"] + ":" + str(item.get("business_date", ""))
        if isinstance(day, dict) and identity == expected and item.get("direction") == "payable":
            try:
                strict_day(item["business_date"])
                if (Decimal(day["amount"]) == Decimal(item["confirmed"])
                        and day.get("currency", item["currency"]) == item["currency"]):
                    return "salary", identity
            except (KeyError, ValueError, TypeError, InvalidOperation):
                pass
    if (item.get("kind") == "shipping" and item.get("direction") == "payable"
            and item.get("party_type") in {"store_driver", "courier"} and identity.startswith("shipping:")):
        key = identity
        facts = engine.get("facts") or {}
        while key.startswith("shipping:"):
            frozen = facts.get(key)
            if (isinstance(frozen, dict) and frozen.get("id") == item.get("party_id")
                    and frozen.get("party_type") == item.get("party_type")
                    and (frozen.get("delivered_at") or frozen.get("recorded_at"))):
                fee = (facts.get("shipping_fee:" + key.removeprefix("shipping:")) or frozen
                       if key == identity else facts.get(identity))
                try:
                    if (isinstance(fee, dict) and Decimal(fee["cost"]) == Decimal(item["confirmed"])
                            and fee.get("currency", frozen.get("fee_currency", item["currency"])) == item["currency"]):
                        return "delivery", key
                except (KeyError, ValueError, TypeError, InvalidOperation):
                    pass
            key = key.rsplit(":", 1)[0]
    return None, None


def automatic_events(rows):
    """One day or one delivery; multi-component summaries have no invented balance."""
    groups = defaultdict(list)
    for row in rows:
        if row.get("automatic_history"):
            groups[(row["automatic_history"], row["automatic_group"])].append(row)
    result = []
    for (_, group), members in groups.items():
        preparation_checkpoint()
        latest = max(members, key=row_order)
        row = dict(latest)
        if len(members) > 1:
            row.update(id=group, event_id=group, amount=decimal_text(sum(
                (Decimal(item["amount"]) for item in members), Decimal(0))),
                summary=True, balance_after=None, component_count=len(members))
        result.append(row)
    return sorted(result, key=row_order, reverse=True)


def compact_rows(rows):
    automatic = automatic_events(rows)
    latest, counts = {}, defaultdict(int)
    for row in automatic:
        family = row["automatic_history"]
        counts[family] += 1
        latest.setdefault(family, row)
    result = [row for row in rows if not row.get("automatic_history")]
    result.extend({**row, "history_count": counts[family]} for family, row in latest.items())
    return sorted(result, key=row_order, reverse=True)


def report_resource(resource):
    result = deepcopy(resource)
    if not can_see_balances(resource):
        result["balance"] = None
        for field in ("position_balance", "ad_wallet_balance", "ad_amounts", "provider_position"):
            result.pop(field, None)
    return result


def statement(resource, rows, *, date_from, date_to, actor, generated_at, purpose, started_at, already_projected=False):
    try:
        first, last = strict_day(date_from), strict_day(date_to)
        if first > last:
            raise ValueError("date_order")
    except (ValueError, TypeError):
        fail("operational_statement_dates_invalid", "اختر فترة صحيحة من تاريخ إلى تاريخ", 422)
    if started_at:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00")).astimezone(RIYADH).date()
        if first < start:
            fail("operational_statement_before_start", "الفترة تسبق بداية السجل التشغيلي المعتمد", 422)
    authorized = resource["access"]
    if not authorized.get("report") or authorized["movements"] == "none":
        fail("operational_report_forbidden", "ليست لديك صلاحية إعداد كشف لهذا الحساب", 403)
    if purpose == "print" and not authorized.get("print"):
        fail("operational_print_forbidden", "ليست لديك صلاحية طباعة أو تصدير هذا الكشف", 403)
    safe_rows = [row for row in rows if authorized["movements"] == "all" or row.get("reason") != "opening"]
    try:
        for row in safe_rows:
            strict_day(row["business_date"])
    except (ValueError, TypeError):
        fail("operational_statement_date_required", "توجد حركة بلا تاريخ موثق؛ راجع المصدر قبل إعداد كشف الفترة", 409)
    # ledger_items has already reconciled and sorted the full authorized ledger.
    # Other callers can pass raw rows and retain the original validation path.
    if already_projected:
        projected, balance_status = safe_rows, projected_balance_status(resource, safe_rows)
    else:
        projected, balance_status = with_running_balances(resource, safe_rows)
    # An approved opening is the baseline, not turnover during the first day.
    items = [row for row in reversed(projected) if row.get("reason") != "opening"
             and date_from <= row["business_date"] <= date_to]
    available = balance_status == "available"
    opening = sum((delta(row, resource["kind"]) for row in safe_rows
                   if row.get("reason") == "opening" or row["business_date"] < date_from), Decimal(0))
    closing = opening + sum((delta(row, resource["kind"]) for row in items), Decimal(0))
    totals = {side: decimal_text(sum((Decimal(row["amount"]) for row in items
              if not row.get("informational") and row["side"] == side), Decimal(0))) for side in ("for_party", "for_us")}
    return {"resource": report_resource(resource), "items": items,
            "opening_balance": decimal_text(opening) if available else None,
            "closing_balance": decimal_text(closing) if available else None,
            "totals": totals, "date_from": date_from, "date_to": date_to,
            "currency": resource["currency"], "scope": authorized["movements"],
            "generated_at": generated_at, "prepared_by": actor,
            "can_print": bool(authorized.get("report") and authorized.get("print") and authorized["movements"] != "none"),
            "balance_status": balance_status, "balance_note": balance_note(balance_status)}


def history(resource, rows, *, page, page_size, generated_at):
    family = "salary" if resource["kind"] == "employee" else "delivery"
    month = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).astimezone(RIYADH).strftime("%Y-%m")
    events = [row for row in automatic_events(rows) if row["automatic_history"] == family]
    current = [row for row in events if row["business_date"][:7] == month]
    previous = defaultdict(list)
    for row in events:
        period = row["business_date"][:7]
        if period < month:
            previous[period].append(row)
    pages = max(1, (len(current) + page_size - 1) // page_size)
    page = min(page, pages)
    return {"resource": report_resource(resource), "automatic_history": family,
            "current_month": month, "items": current[(page - 1) * page_size:page * page_size],
            "page": page, "page_size": page_size, "total": len(current), "total_pages": pages,
            "history_count": len(events), "current_month_total": decimal_text(sum(
                (Decimal(row["amount"]) for row in current), Decimal(0))),
            "balance_status": projected_balance_status(resource, rows),
            "balance_note": balance_note(projected_balance_status(resource, rows)),
            "previous_months": [{"month": period, "count": len(members), "amount": decimal_text(sum(
                (Decimal(row["amount"]) for row in members), Decimal(0))), "currency": resource["currency"],
                "balance_after": None} for period, members in sorted(previous.items(), reverse=True)]}
