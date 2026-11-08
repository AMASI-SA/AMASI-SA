"""Read only the paired, frozen employee-day accruals in one operational snapshot."""
from calendar import monthrange
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

from operational_salary_start import RIYADH, instant, select_first_day, strict_day
from operational_salary_reconciliation import reviewed_salary_day_matches


def recorded_salary_subtotals(state):
    engine = state.get("engine", {})
    try:
        first = select_first_day(state, engine["as_of"])
        last = instant(engine["as_of"]).astimezone(RIYADH).date()
    except (KeyError, TypeError, ValueError, OverflowError):
        return {}
    days = engine.get("salary_days", {})
    obligations = engine.get("obligations", {})
    conflicts = engine.get("salary_conflicts", {})
    if not all(isinstance(rows, dict) for rows in (days, obligations, conflicts)):
        return {}
    salaries = {key: row for key, row in obligations.items() if row.get("kind") == "salary"}
    amounts, counts, blocked = {}, {}, set()
    for key in set(salaries) | set(days):
        day, obligation = days.get(key, {}), salaries.get(key, {})
        if not isinstance(day, dict) or not isinstance(key, str) or not key.startswith("salary:"):
            return {}
        employee, _, stamp = key.removeprefix("salary:").rpartition(":")
        currencies = {v for v in (day.get("currency"), obligation.get("currency")) if isinstance(v, str) and v}
        if not currencies:
            return {}
        try:
            business_day = strict_day(stamp)
            if not first <= business_day <= last:
                continue
            revision = day["revision"]
            amount = Decimal(day["amount"])
            end = revision.get("effective_to")
            salary = Decimal(revision["salary"])
            # The engine prorates by calendar days with cumulative cent rounding.
            monthly_basis = Decimal(day["daily_salary"])
            quota = monthly_basis / monthrange(business_day.year, business_day.month)[1]
            if (not employee or key in conflicts or len(currencies) != 1
                    or day.get("currency") != obligation.get("currency")
                    or (obligation.get("id"), obligation.get("party_id"), obligation.get("business_date")) != (key, employee, stamp)
                    or (obligation.get("party_type"), obligation.get("direction")) != ("employee", "payable")
                    or obligation.get("superseded") or obligation.get("derived_credit")
                    or not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01"))
                    or amount != Decimal(obligation["confirmed"])
                    or not salary.is_finite() or salary < 0 or monthly_basis not in (0, salary)
                    or amount not in (quota.quantize(Decimal("0.01"), rounding=ROUND_FLOOR),
                                      quota.quantize(Decimal("0.01"), rounding=ROUND_CEILING))
                    or strict_day(revision["effective_from"]) > business_day or (end and strict_day(end) < business_day)
                    or revision["status"] not in {"active", "inactive", "unpaid_leave"}
                    or (revision["status"] != "active" and amount != 0)):
                blocked.update(currencies)
                continue
        except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError):
            blocked.update(currencies)
            continue
        currency = day["currency"]
        amounts[currency] = amounts.get(currency, Decimal(0)) + amount
        counts[currency] = counts.get(currency, 0) + 1
    adjustments = {key: row for key, row in obligations.items() if row.get('kind') == 'salary_cutover_adjustment'}
    approved_rows = ((state.get('salary_accrual_policy') or {}).get('rounding_review') or {}).get('rows', {})
    adjustment_totals = {}
    for key, proof in approved_rows.items():
        currency = proof.get('proposed', {}).get('currency')
        if not reviewed_salary_day_matches(state, key, days.get(key), proof.get('proposed'), currency):
            if currency:
                blocked.add(currency)
            continue
        delta = Decimal(proof['proposed']['amount']) - Decimal(proof['recorded']['amount'])
        adjustment_totals[currency] = adjustment_totals.get(currency, Decimal(0)) + delta
    for adjustment in adjustments.values():
        key, currency = adjustment.get('original_salary_day_id'), adjustment.get('currency')
        if key not in approved_rows:
            if currency:
                blocked.add(currency)
    for currency, delta in adjustment_totals.items():
        if currency in amounts:
            amounts[currency] += delta
    return {code: {"amount": format(value, ".2f"), "currency": code, "employee_days": counts[code],
                   **({'cutover_adjustment_amount': format(adjustment_totals[code], '.2f')}
                      if code in adjustment_totals else {}),
                   "first_day": first.isoformat(), "last_day": last.isoformat()}
            for code, value in amounts.items() if code not in blocked}
