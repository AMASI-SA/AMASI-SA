"""Conservative aggregation of independent account spend in a shared timezone."""
from datetime import timedelta
from math import isfinite


def _sum_known(values):
    if not values or any(v is None or isinstance(v, bool) for v in values):
        return None
    try:
        numbers = [float(v) for v in values]
        return round(sum(numbers), 2) if all(isfinite(v) for v in numbers) else None
    except (ValueError, TypeError):
        return None


def _available_account_amount(report):
    value = report.get("total_sar")
    if ((report.get("quality") or {}).get("amount_available") is not True
            or not isinstance(value, (int, float)) or isinstance(value, bool)):
        return None
    try:
        amount = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return amount if isfinite(amount) and amount >= 0 else None


def combine_dashboard_spend(reports, accounts, date_from, date_to, timezone_name):
    qualities = [r.get("quality") or {} for r in reports]
    available = all(q.get("amount_available") is True for q in qualities)
    complete = all(q.get("complete") is True for q in qualities)
    provisional = any(q.get("provisional") is True for q in qualities)
    total = _sum_known([r.get("total_sar") for r in reports]) if available else None
    known_amounts = [amount for report in reports
                     if (amount := _available_account_amount(report)) is not None]
    known_sum = sum(known_amounts)
    known_total = round(known_sum, 2) if known_amounts and isfinite(known_sum) else None
    daily, states, hourly = {}, {}, {}
    for offset in range((date_to - date_from).days + 1):
        day = (date_from + timedelta(days=offset)).isoformat()
        daily[day] = _sum_known([(r.get("daily_sar") or {}).get(day) for r in reports])
        day_states = [(r.get("daily_state") or {}).get(day, "unknown_incomplete") for r in reports]
        day_complete = all(s in {"confirmed_data", "confirmed_zero", "not_connected"} for s in day_states)
        day_provisional = all(s in {"confirmed_data", "confirmed_zero", "provisional_data", "provisional_zero", "not_connected"} for s in day_states)
        states[day] = ("unknown_incomplete" if daily[day] is None or not day_provisional else
                       ("confirmed_" if day_complete else "provisional_") + ("data" if daily[day] else "zero"))
        by_account = [{str(h.get("hour_index")): h for h in (r.get("hourly_sar") or {}).get(day, [])} for r in reports]
        indices = sorted({key for hours in by_account for key in hours}, key=lambda x: int(x))
        hourly[day] = []
        for key in indices:
            parts = [hours.get(key) for hours in by_account]
            exemplar = next(p for p in parts if p)
            value = _sum_known([p.get("spend_sar") if p else None for p in parts])
            statuses = [p.get("status") if p else "unknown_incomplete" for p in parts]
            state = (statuses[0] if len(set(statuses)) == 1 else
                     "provisional" if "provisional" in statuses and value is not None else
                     "confirmed_data" if value is not None else "unknown_incomplete")
            hourly[day].append({"date": day, "hour_index": int(key), "hour": exemplar.get("hour"),
                                "spend_sar": value, "status": state})
    commissions = [r.get("bank_commissions") for r in reports]
    commission_summary = None
    if all(isinstance(c, dict) for c in commissions):
        commission_summary = {"accounts": [a for c in commissions for a in c.get("accounts", [])],
                              "coverage": {"complete": all((c.get("coverage") or {}).get("complete") is True for c in commissions)}}
        for key in ("total_fee_sar", "fee_subject_spend_sar", "total_effective_spend_sar"):
            commission_summary[key] = _sum_known([c.get(key) for c in commissions])
    return {
        "accounts": accounts, "rows": [row for r in reports for row in r.get("rows", [])] if available else [],
        "total_sar": total, "daily_sar": daily, "daily_state": states, "hourly_sar": hourly,
        "known_total_sar": known_total, "known_account_count": len(known_amounts),
        "incomplete_account_count": len(reports) - len(known_amounts),
        "bank_commissions": commission_summary,
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "timezone": timezone_name},
        "quality": {"status": "complete" if complete else "provisional" if available and provisional else "incomplete",
            "data_state": "unknown_incomplete" if total is None else ("provisional_" if provisional else "confirmed_") + ("data" if total else "zero"),
            "complete": complete, "amount_complete": complete,
            "coverage_complete": all(q.get("coverage_complete") is True for q in qualities),
            "amount_available": available and total is not None, "provisional": provisional,
            "connected": True, "timezone": timezone_name,
            "contains_open_day": any(q.get("contains_open_day") for q in qualities),
            "range_ends_on_open_day": any(q.get("range_ends_on_open_day") for q in qualities),
            "source_sync_run_ids": sorted({i for q in qualities for i in q.get("source_sync_run_ids", [])}),
            "reason_codes": sorted({code for q in qualities for code in q.get("reason_codes", [])}),
        },
        "source_contract": "unified-marketing-data-v1:dashboard-spend", "contract_version": "unified-marketing-data-v1",
        "source_only": True, "provider_write_reached": False, "campaign_write_reached": False,
        "accounting_write_reached": False, "qoyod_write_reached": False,
    }
